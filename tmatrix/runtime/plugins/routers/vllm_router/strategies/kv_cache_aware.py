import httpx
import asyncio
from typing import Dict, List, Optional, Any

from tmatrix.common.logging import init_logger
from tmatrix.runtime.metrics import MetricsClientConfig, AsyncMetricsClient, RealtimeVLLMLoadBalancer
from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.services import PrefixCacheService, get_prefix_cache_service
from tmatrix.runtime.core import RequestContext
from .base import RequestStats, EngineStats, RouteStrategy

logger = init_logger("plugins/router")


class KVCacheAwareStrategy(RouteStrategy):
    """基于KV缓存的路由策略"""

    def __init__(self):
        super().__init__()
        self.req_id = 0
        self._lock = asyncio.Lock()
        self.kv_cache_available = False
        self._async_client: Optional[httpx.AsyncClient] = None
        self.load_balancer: Optional[RealtimeVLLMLoadBalancer] = None
        self.prefix_cache_service: Optional[PrefixCacheService] = None

    async def _initialize(self):
        # TODO
        #   LMCache Adapter未实现，当前基于vLLM Adapter进行KV Cache感知
        self.prefix_cache_service = get_prefix_cache_service()
        self.kv_cache_available = True if self.prefix_cache_service is not None else False
        if self.kv_cache_available:
            self._async_client = httpx.AsyncClient(timeout=None, http2=True, limits=httpx.Limits(
                max_keepalive_connections=1024,  # 长连接数需 ≥ 最大并发数
                max_connections=1500,            # 总连接数 = 最大并发数+缓冲（建议1.5倍）
                keepalive_expiry=60              # 空闲连接60秒释放（平衡复用率与资源占用）
            ))
            logger.warning("LMCache Adapter尚未实现，当前基于vLLM Adapter进行KV Cache感知")
        else:
            logger.warning("KV Cache感知组件异常，使用轮询策略调度请求")

        # 初始化负载均衡器
        # 配置监控客户端
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

    async def _tokenize(self, endpoint: Endpoint, prompt: str) -> List[int]:
        """将提示文本转换为token ID"""
        url = endpoint.address + "/tokenize"
        headers = {"Content-Type": "application/json"}
        data = {"model": endpoint.model_name, "prompt": prompt}

        response = await self._async_client.post(url, headers=headers, json=data)
        response_json = response.json()

        return response_json.get("tokens", [])

    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """基于KV缓存路由请求"""
        if not endpoints:
            raise ValueError("No available endpoints for routing")

        prefill_endpoints: List[Endpoint] = endpoints["prefill_instances"]
        if len(prefill_endpoints) == 0:
            raise ValueError("No available endpoints for routing")

        # 只有一个Prefill实例时，不需要额外调度
        if len(prefill_endpoints) == 1:
            return prefill_endpoints[0].address

        if not self.kv_cache_available:
            # 如果KV缓存不可用，则使用轮询
            len_engines = len(prefill_endpoints)
            chosen = sorted(prefill_endpoints, key=lambda e: e.url)[self.req_id % len_engines]
            self.req_id += 1
            return chosen.address

        # 获取请求中的提示文本
        prompt = context.parsed_body.get("prompt", "")
        if not prompt:
            # 如果没有提示文本，则使用轮询
            len_engines = len(prefill_endpoints)
            chosen = sorted(prefill_endpoints, key=lambda e: e.url)[self.req_id % len_engines]
            self.req_id += 1
            return chosen.address

        # TODO 高优先级！！！
        #   1.将tokenize的逻辑统一入队调度，异步写入Context
        #   2.选择Prefill实例时，基于负载均衡策略
        # 将提示文本转换为token ID
        token_ids = await self._tokenize(prefill_endpoints[0], prompt)

        with self._lock:
            target: str
            matched_result = await self.prefix_cache_service.find_longest_prefix_match(token_ids)
            for endpoint in prefill_endpoints:
                if matched_result["instance_id"] == endpoint.instance_name:
                    target = endpoint.address
                    break

        return target

    async def route_decode_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        if not endpoints:
            raise ValueError("No available endpoints for routing")

        decode_endpoints: List[Endpoint] = endpoints["decode_instances"]
        if len(decode_endpoints) == 0:
            raise ValueError("No available endpoints for routing")

        # 只有一个Decode实例时，不需要额外调度
        if len(decode_endpoints) == 1 or self.load_balancer is None:
            return decode_endpoints[0].address

        # Decode实例基于负载均衡调度
        scores_data = self.load_balancer.get_load_status()
        logger.info(f"运行状态: {scores_data['is_running']}, "
                    f"实例总数: {scores_data['total_instances']}, "
                    f"健康实例: {scores_data['healthy_instances']}")

        min_load_score = float('inf')
        min_instance_id = None
        for instance in scores_data['instances']:
            logger.info(f"{instance['instance_id']}: "
                        f"负载={instance['load_score']}, "
                        f"运行={instance['running_requests']}, "
                        f"等待={instance['waiting_requests']}, "
                        f"缓存={instance['gpu_cache_usage']}%, "
                        f"状态={instance['status']}")
            load_score = instance['load_score']
            if load_score < min_load_score:
                min_load_score = load_score
                min_instance_id = instance['instance_id']

        for endpoint in decode_endpoints:
            if endpoint.instance_name == min_instance_id:
                return endpoint.address
