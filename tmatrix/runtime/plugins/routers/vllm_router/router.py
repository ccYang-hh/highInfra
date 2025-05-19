import time
import random
from typing import Any, Dict, List, Optional, Type, TypeVar
from fastapi import APIRouter

from tmatrix.components.logging import init_logger
from tmatrix.runtime.service_discovery import Endpoint, EndpointType, EndpointStatus
from tmatrix.runtime.pipeline import StageHook, PipelineHook, PipelineStage
from tmatrix.runtime.plugins import Plugin, IPluginConfig
from tmatrix.runtime.core import RequestContext, RequestState
from .config import RouterConfig, RouterStrategy
from .strategies import (
    EngineStats,
    RequestStats,
    RouteStrategy,
    RoundRobinStrategy,
    SessionStrategy,
    KVCacheAwareStrategy
)

logger = init_logger("plugins/router")

T = TypeVar('T', bound='IPluginConfig')


class RouterStage(PipelineStage):
    """路由选择阶段"""

    def __init__(self, config: Optional[RouterConfig] = None):
        """初始化路由阶段"""
        super().__init__("router")
        self.config = config or RouterConfig()
        self.routers = {}
        self.current_strategy = self.config.default_strategy
        self._init_routers()

    def _init_routers(self):
        """初始化所有支持的路由策略"""
        # 初始化轮询路由
        if RouterStrategy.RoundRobin in self.config.support_strategies:
            self.routers[RouterStrategy.RoundRobin] = RouteStrategy()

        # 初始化会话路由
        if RouterStrategy.Session in self.config.support_strategies:
            self.routers[RouterStrategy.Session] = SessionStrategy(
                session_key=self.config.session_key
            )

        # 初始化KV缓存路由
        if RouterStrategy.KVCacheAware in self.config.support_strategies and self.config.kv_cache_enabled:
            self.routers[RouterStrategy.KVCacheAware] = KVCacheAwareStrategy(
                lmcache_controller_port=self.config.lmcache_controller_port
            )

    def set_strategy(self, strategy: RouterStrategy):
        """设置当前路由策略"""
        if strategy in self.routers:
            self.current_strategy = strategy
            logger.info(f"Router strategy set to {strategy.name}")
        else:
            logger.warning(f"Router strategy {strategy.name} not supported, using current strategy")

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
                context.error_message = f"No endpoints available for model {context.model_name}"
                context.state = RequestState.FAILED
                return

            # 获取引擎和请求统计信息
            engine_stats = self._get_engine_stats(context)
            request_stats = self._get_request_stats(context)

            # 选择当前的路由策略
            router = self.routers.get(self.current_strategy)
            if not router:
                context.error_message = f"Router strategy {self.current_strategy.name} not initialized"
                context.state = RequestState.FAILED
                return

            # 根据路由策略选择后端URL
            backend_url = await router.route_request(
                endpoints=endpoints,
                engine_stats=engine_stats,
                request_stats=request_stats,
                context=context
            )

            # 将选择的后端URL设置到上下文
            context.backend_url = backend_url

            # 记录路由信息和耗时
            end_time = time.time()
            routing_time = end_time - start_time
            logger.info(
                f"Routing request {context.request_id} to {backend_url}, "
                f"model: {context.model_name}, strategy: {self.current_strategy.name}, "
                f"time: {routing_time:.4f}s"
            )

            # 将路由信息保存到上下文扩展中
            context.extensions["routing_time"] = routing_time
            context.extensions["routing_strategy"] = self.current_strategy.name
        except Exception as e:
            context.error = e
            context.error_message = f"Routing error: {str(e)}"
            context.state = RequestState.FAILED

    @staticmethod
    def _get_endpoints_for_model(model_name: str, context: RequestContext) -> List[Endpoint]:
        """获取支持指定模型的端点列表"""
        # endpoints = []
        #
        # # 尝试从app_state获取端点信息
        # if context.app_state and "endpoints" in context.app_state:
        #     endpoints_data = context.app_state["endpoints"]
        #     for info in endpoints_data:
        #         if info.get("model_name") == model_name:
        #             endpoints_data.append(info)
        #
        # return endpoints
        endpoints: List[Endpoint] = [
            Endpoint(
                endpoint_id="001",
                address="70.182.43.96:30000",
                model_name="qwen",
                added_timestamp=time.time(),
                status=EndpointStatus.HEALTHY,
                endpoint_type=[EndpointType.COMPLETION]
            )
        ]
        return endpoints

    @staticmethod
    def _get_engine_stats(context: RequestContext) -> Dict[str, EngineStats]:
        """获取引擎统计信息"""
        # 从上下文或扩展中获取引擎统计信息
        engine_stats = {}

        if context.app_state and "engine_stats" in context.app_state:
            return context.app_state["engine_stats"]

        if "engine_stats" in context.extensions:
            return context.extensions["engine_stats"]

        return engine_stats

    @staticmethod
    def _get_request_stats(context: RequestContext) -> Dict[str, RequestStats]:
        """获取请求统计信息"""
        # 从上下文或扩展中获取请求统计信息
        request_stats = {}

        if context.app_state and "request_stats" in context.app_state:
            return context.app_state["request_stats"]

        if "request_stats" in context.extensions:
            return context.extensions["request_stats"]

        return request_stats


class ModelResolutionStage(PipelineStage):
    """从请求中提取和验证模型信息的阶段"""

    def __init__(self):
        super().__init__("model_resolution")

    async def process(self, context: RequestContext) -> None:
        """处理请求上下文"""
        context.set_state(RequestState.MODEL_RESOLUTION)

        # 提取模型名称
        model = context.parsed_body.get("model")
        if not model:
            context.fail("Missing model parameter in request")
            return

        # 设置模型名称
        context.model_name = model

        # 解析其他模型参数
        if "parameters" in context.parsed_body:
            context.model_parameters = context.parsed_body["parameters"]

        # 检查模型是否支持
        model_registry = context.get_extension("model_registry", {})
        if model_registry and model not in model_registry:
            context.fail(f"Model '{model}' is not supported")
            return

        logger.debug(f"Resolved model: {model} for request {context.request_id}")


class BackendRoutingStage(PipelineStage):
    """将请求路由到合适的后端服务器的阶段"""

    def __init__(self, backends=None, strategy="round_robin"):
        """
        初始化路由阶段

        Args:
            backends: 可选的后端列表
            strategy: 路由策略 (round_robin, random, least_loaded)
        """
        super().__init__("backend_routing")
        self._backends = backends or []
        self._strategy = strategy
        self._current_backend_index = 0

    def configure(self, config: Dict[str, Any]) -> "BackendRoutingStage":
        """配置路由阶段"""
        if "backends" in config:
            self._backends = config["backends"]
        if "strategy" in config:
            self._strategy = config["strategy"]
        return self

    def add_backend(self, backend: Dict[str, Any]) -> None:
        """
        添加后端

        Args:
            backend: 后端信息字典
        """
        if backend not in self._backends:
            self._backends.append(backend)

    def remove_backend(self, backend_id: str) -> None:
        """
        移除后端

        Args:
            backend_id: 后端ID
        """
        self._backends = [b for b in self._backends if b.get("id") != backend_id]

    def get_backends(self) -> List[Dict[str, Any]]:
        """
        获取所有后端

        Returns:
            后端列表
        """
        return self._backends.copy()

    async def _select_backend(self, context: RequestContext) -> Optional[Dict[str, Any]]:
        """
        根据策略选择后端

        Args:
            context: 请求上下文

        Returns:
            选择的后端或None
        """
        # 过滤出支持请求模型的后端
        model = context.model_name
        available_backends = [
            b for b in self._backends
            if model in b.get("models", []) and b.get("healthy", True)
        ]

        if not available_backends:
            return None

        # 保存可用后端到上下文
        context.available_backends = available_backends

        # 根据策略选择后端
        if self._strategy == "round_robin":
            # 简单轮询
            backend = available_backends[self._current_backend_index % len(available_backends)]
            self._current_backend_index += 1
        elif self._strategy == "random":
            # 随机选择
            backend = random.choice(available_backends)
        elif self._strategy == "least_loaded":
            # 选择负载最低的
            backend = min(available_backends, key=lambda b: b.get("load", 0))
        else:
            # 默认使用第一个
            backend = available_backends[0]

        return backend

    async def process(self, context: RequestContext) -> None:
        """处理请求上下文"""
        context.set_state(RequestState.ROUTING)

        # 确保已经解析出模型
        if not context.model_name:
            context.fail("Model name not resolved")
            return

        # 选择后端
        backend = await self._select_backend(context)
        if not backend:
            context.fail(f"No backend available for model {context.model_name}")
            return

        # 设置选择的后端
        context.backend_url = backend.get("url")
        context.backend_info = backend

        logger.debug(f"Selected backend {backend.get('id')} for request {context.request_id}")


class RoutingMetricsHook(StageHook):
    """收集路由指标的钩子"""

    def __init__(self):
        super().__init__("routing_metrics")
        self.metrics = {
            "requests_by_model": {},
            "backend_selections": {},
            "routing_times": []
        }

    async def before_stage(self, stage: PipelineStage, context: RequestContext) -> None:
        """在阶段执行前记录时间戳"""
        if stage.name == "backend_routing":
            context.set_extension("routing_start_time", time.time())

    async def after_stage(self, stage: PipelineStage, context: RequestContext) -> None:
        """记录路由指标"""
        if stage.name == "backend_routing":
            # 记录路由时间
            start_time = context.get_extension("routing_start_time")
            if start_time:
                routing_time = time.time() - start_time
                self.metrics["routing_times"].append(routing_time)

                # 保持列表大小合理
                if len(self.metrics["routing_times"]) > 1000:
                    self.metrics["routing_times"] = self.metrics["routing_times"][-1000:]

            # 记录模型请求量
            model = context.model_name
            if model:
                self.metrics["requests_by_model"][model] = self.metrics["requests_by_model"].get(model, 0) + 1

            # 记录后端选择
            if context.backend_info:
                backend_id = context.backend_info.get("id")
                if backend_id:
                    self.metrics["backend_selections"][backend_id] = self.metrics["backend_selections"].get(backend_id, 0) + 1

    def get_metrics(self) -> Dict[str, Any]:
        """获取累积的指标"""
        result = self.metrics.copy()

        # 计算平均路由时间
        if self.metrics["routing_times"]:
            result["avg_routing_time"] = sum(self.metrics["routing_times"]) / len(self.metrics["routing_times"])
        else:
            result["avg_routing_time"] = 0

        return result


class RouterPlugin(Plugin[RouterConfig]):
    """路由插件，提供模型解析和后端路由功能"""
    plugin_name: str = "vllm_router"
    plugin_version: str = "0.0.1"

    def __init__(self):
        super().__init__()
        self.model_resolution_stage = ModelResolutionStage()
        self.routing_stage = BackendRoutingStage()
        self.metrics_hook = RoutingMetricsHook()
        self.router_stage: Optional[RouterStage] = None

    def get_config_class(self) -> Optional[Type[T]]:
        return RouterConfig

    async def _on_initialize(self) -> None:
        """初始化路由插件"""
        if not self.config or not isinstance(self.config, RouterConfig):
            raise ValueError("路由插件需要RouterConfig配置")

        self.router_stage = RouterStage(self.config)
        logger.info(f"路由插件初始化成功，使用策略: {self.config.default_strategy}")

    async def _on_shutdown(self) -> None:
        """关闭路由插件"""
        logger.info("Router插件已关闭")

    def get_pipeline_stages(self) -> List[PipelineStage]:
        """获取管道阶段"""
        return [self.router_stage]

    def get_stage_hooks(self) -> List[StageHook]:
        """获取阶段钩子"""
        return [self.metrics_hook]
