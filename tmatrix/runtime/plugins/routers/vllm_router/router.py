import time
import httpx
import random
from typing import Any, Dict, List, Optional, Type, TypeVar

from tmatrix.common.logging import init_logger
from tmatrix.vars import RequestType, InferenceScene
from tmatrix.runtime.service_discovery import Endpoint, EndpointType, EndpointStatus, KVRole
from tmatrix.runtime.metrics import MetricsClientConfig, AsyncMetricsClient, LoadBalancer, RealtimeVLLMLoadBalancer
from tmatrix.runtime.pipeline import StageHook, PipelineHook, PipelineStage
from tmatrix.runtime.plugins import Plugin, IPluginConfig
from tmatrix.runtime.core import RequestContext, RequestState
from .config import RouterConfig, RouterStrategy
from .strategies import (
    BaseRouter,
    RoundRobinStrategy,
    SessionStrategy,
    KVCacheAwareStrategy
)

logger = init_logger("plugins/router")

T = TypeVar('T', bound='IPluginConfig')


class RouterStage(PipelineStage):
    """路由选择阶段"""

    def __init__(self, config: Optional[RouterConfig] = None, load_balancer: Optional[LoadBalancer] = None):
        """初始化路由阶段"""
        super().__init__("router")
        self.config = config or RouterConfig()
        self.routers: Dict[RouterStrategy, BaseRouter] = {}
        self.current_strategy = self.config.default_strategy
        # TODO 优化这个地方的耦合逻辑，包括配置参数等
        self._async_client = httpx.AsyncClient(timeout=None, http2=True, limits=httpx.Limits(
            max_keepalive_connections=1024,  # 长连接数需 ≥ 最大并发数
            max_connections=1500,            # 总连接数 = 最大并发数+缓冲（建议1.5倍）
            keepalive_expiry=60              # 空闲连接60秒释放（平衡复用率与资源占用）
        ))
        self._load_balancer = load_balancer
        self._init_routers()

    async def _on_shutdown(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        self.routers.clear()

    def _init_routers(self):
        """初始化所有支持的路由策略"""
        # 初始化轮询路由
        if RouterStrategy.RoundRobin in self.config.support_strategies:
            self.routers[RouterStrategy.RoundRobin] = RoundRobinStrategy()

        # 初始化会话路由
        if RouterStrategy.Session in self.config.support_strategies:
            self.routers[RouterStrategy.Session] = SessionStrategy()

        # 初始化KV缓存路由
        if RouterStrategy.KVCacheAware in self.config.support_strategies and self.config.kv_cache_enabled:
            self.routers[RouterStrategy.KVCacheAware] = KVCacheAwareStrategy(self._load_balancer)

    def set_strategy(self, strategy: RouterStrategy):
        """设置当前路由策略"""
        if strategy in self.routers:
            self.current_strategy = strategy
            logger.info(f"Router strategy set to {strategy.name}")
        else:
            logger.warning(f"Router strategy {strategy.name} not supported, using current strategy")

    async def select_strategy(self, context: RequestContext) -> RouterStrategy:
        request_type = context.request_type
        if request_type == RequestType.FIRST_TIME:
            self.set_strategy(RouterStrategy.KVCacheAware)
            return RouterStrategy.KVCacheAware

        elif request_type == RequestType.HISTORY:
            self.set_strategy(RouterStrategy.Session)
            return RouterStrategy.Session

        elif request_type == RequestType.RAG:
            self.set_strategy(RouterStrategy.KVCacheAware)
            return RouterStrategy.KVCacheAware

        self.set_strategy(RouterStrategy.RoundRobin)
        return RouterStrategy.RoundRobin

    async def route_by_pd_disaggregation(
        self,
        context: RequestContext,
        endpoints: Dict[str, List[Endpoint]],
        router: BaseRouter
    ):
        # 先选择Prefill实例
        prefill_instance = await router.route_prefill_request(
            endpoints=endpoints,
            context=context
        )

        # 如果是首次请求，且路由策略是KVAware，则记录这次的调度结果
        if context.request_type == RequestType.FIRST_TIME:
            session_router: SessionStrategy = self.routers[RouterStrategy.Session]  # type: ignore
            session_router.update_binding_store(context.request_identifiers["chat_id"], prefill_instance)

        # 执行prefill推理过程
        req_data = context.parsed_body.copy()
        req_data['max_tokens'] = 1
        address = f"{prefill_instance.transport_type.value}://{prefill_instance.address}{context.endpoint}"
        logger.info(f"Prefill阶段，调度到: {address}")
        response = await self._async_client.post(address, json=req_data)
        if not response.is_success:
            # prefill阶段失败
            context.fail(response.text)
            return

        # prefill执行成功，选择Decode实例
        decode_instance = await router.route_decode_request(
            endpoints=endpoints,
            context=context
        )

        # 将选择的后端URL设置到上下文
        address = f"{decode_instance.transport_type.value}://{decode_instance.address}{context.endpoint}"
        context.backend_url = address

    async def route_by_multi_instances(
        self,
        context: RequestContext,
        endpoints: Dict[str, List[Endpoint]],
        router: BaseRouter
    ):
        instance = await router.route_request(endpoints=endpoints, context=context)
        address = f"{instance.transport_type.value}://{instance.address}{context.endpoint}"
        context.backend_url = address
        logger.info(f"路由决策，调度请求：{context.request_id} 到服务实例：{address}")

        # 如果是首次请求，且路由策略是KVAware，则记录这次的调度结果
        if context.request_type == RequestType.FIRST_TIME:
            session_router: SessionStrategy = self.routers[RouterStrategy.Session]  # type: ignore
            session_router.update_binding_store(context.request_identifiers["chat_id"], instance)

    async def process(self, context: RequestContext) -> None:
        """处理请求上下文，选择合适的后端URL"""
        if not context.model_name:
            context.fail("Model name not specified")
            return

        context.set_state(RequestState.ROUTING)
        start_time = time.time()

        try:
            # 获取可用端点
            endpoints = self._get_endpoints_for_model(context.model_name, context)
            if not endpoints:
                context.fail(f"No endpoints available for model {context.model_name}")
                return

            # 选择当前的路由策略
            strategy = await self.select_strategy(context)
            router = self.routers.get(RouterStrategy.RoundRobin)
            if not router:
                context.fail(f"Router strategy {strategy} not initialized")
                return

            # 路由是否已初始化
            if not router.initialized:
                await router.initialize()

            # 场景识别
            match context.scene:
                case context.scene.PD_DISAGGREGATION:
                    await self.route_by_pd_disaggregation(context, endpoints, router)
                case context.scene.MULTI_INSTANCE:
                    await self.route_by_multi_instances(context, endpoints, router)
                case context.scene.SINGLE:
                    assert len(endpoints["both_instances"]) > 0, "multi instances not supported in single scene"
                    single_endpoint = endpoints["both_instances"][0]
                    address = f"{single_endpoint.transport_type.value}://{single_endpoint.address}{context.endpoint}"
                    context.backend_url = address
                case _:
                    context.fail(f"Unknown scene {context.scene}")

            # 记录路由信息和耗时
            end_time = time.time()
            routing_time = end_time - start_time
            logger.info(
                f"model: {context.model_name}, strategy: {self.current_strategy.name}, "
                f"route time: {routing_time:.4f}s"
            )

            # 将路由信息保存到上下文扩展中
            context.extensions["routing_time"] = routing_time
            context.extensions["routing_strategy"] = self.current_strategy.name
        except Exception as e:
            context.fail(f"Routing error: {str(e)}")

    @staticmethod
    def _get_endpoints_for_model(model_name: str, context: RequestContext) -> Dict[str, List[Endpoint]]:
        """获取支持指定模型的端点列表"""
        endpoints: Dict[str, List[Endpoint]] = {
            "both_instances": [],
            "prefill_instances": [],
            "decode_instances": [],
        }

        # 尝试从app_state获取端点信息
        if context.app_state and hasattr(context.app_state, "service_discovery"):
            service_discovery = context.app_state.service_discovery
            endpoint_candidates: List[Endpoint] = service_discovery.get_endpoints()
            for info in endpoint_candidates:
                if info.model_name == model_name:
                    if info.kv_role == KVRole.PRODUCER:
                        endpoints["prefill_instances"].append(info)
                    elif info.kv_role == KVRole.CONSUMER:
                        endpoints["decode_instances"].append(info)
                    else:
                        endpoints["both_instances"].append(info)

        return endpoints


class RouterPlugin(Plugin[RouterConfig]):
    """路由插件，提供模型解析和后端路由功能"""
    plugin_name = "vllm_router"
    plugin_version = "0.0.1"

    def __init__(self):
        super().__init__()
        self.router_stage: Optional[RouterStage] = None
        self.load_balancer: Optional[LoadBalancer] = None

    def get_config_class(self) -> Optional[Type[T]]:
        return RouterConfig

    async def _on_initialize(self) -> None:
        """初始化路由插件"""
        if not self.config or not isinstance(self.config, RouterConfig):
            raise ValueError("路由插件需要RouterConfig配置")

        # 初始化负载均衡器
        config = MetricsClientConfig(
            base_url="http://172.17.111.150:8088",
            endpoint="/api/v1/monitor/stats",
            timeout=5.0
        )

        client = AsyncMetricsClient()
        await client.initialize(config)

        # 创建负载均衡器
        self.load_balancer = RealtimeVLLMLoadBalancer(client, poll_interval=3.0)

        # 启动负载均衡器
        await self.load_balancer.start()

        # 初始化RouterStage
        self.router_stage = RouterStage(self.config, self.load_balancer)

        logger.info(f"路由插件初始化成功，使用策略: {self.config.default_strategy}")

    async def _on_shutdown(self) -> None:
        """关闭路由插件"""
        await self.load_balancer.stop()
        logger.info("Router插件已关闭")

    def get_pipeline_stages(self) -> List[PipelineStage]:
        """获取管道阶段"""
        return [self.router_stage]

    def get_stage_hooks(self) -> List[StageHook]:
        """获取阶段钩子"""
        return []
