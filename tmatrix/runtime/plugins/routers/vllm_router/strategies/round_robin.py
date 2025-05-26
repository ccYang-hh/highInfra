from typing import Dict, List

from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.core import RequestContext
from .base import EngineStats, RequestStats, RouteStrategy


class RoundRobinStrategy(RouteStrategy):
    """轮询路由策略"""

    def __init__(self):
        super().__init__()
        self.req_id = 0

    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """轮询方式选择后端URL"""
        if not endpoints:
            raise ValueError("No available endpoints for routing")

        prefill_endpoints: List[Endpoint] = endpoints["prefill_instances"]
        if len(prefill_endpoints) == 0:
            raise ValueError("No available endpoints for routing")

        # 只有一个Prefill实例时，不需要额外调度
        if len(prefill_endpoints) == 1:
            return prefill_endpoints[0].address

        len_engines = len(prefill_endpoints)
        chosen = sorted(prefill_endpoints, key=lambda e: e.address)[self.req_id % len_engines]
        self.req_id += 1
        return chosen.address

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
        if len(decode_endpoints) == 1:
            return decode_endpoints[0].address

        # Decode实例基于负载均衡调度
        return decode_endpoints[0].address
