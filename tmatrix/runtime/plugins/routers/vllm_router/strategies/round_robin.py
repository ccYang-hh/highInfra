from typing import Dict, List, Optional

from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.metrics import LoadBalancer, RealtimeVLLMLoadBalancer
from tmatrix.runtime.core import RequestContext
from .base import BaseRouter


class RoundRobinStrategy(BaseRouter):
    """轮询路由策略"""

    def __init__(self, load_balancer: Optional[LoadBalancer] = None):
        super().__init__()
        self.req_id = 0
        self._load_balancer: Optional[RealtimeVLLMLoadBalancer] = load_balancer

    def _route_with_round_robin(self, endpoints: List[Endpoint]) -> Endpoint:
        if len(endpoints) == 1:
            return endpoints[0]

        len_engines = len(endpoints)
        chosen = sorted(endpoints, key=lambda e: e.address)[self.req_id % len_engines]
        self.req_id += 1
        return chosen

    async def route_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """非PD分离场景使用该API进行调度"""
        both_endpoints = self._validate_endpoints(endpoints, "both_instances")
        return self._route_with_round_robin(both_endpoints)

    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """轮询方式选择后端URL"""
        prefill_endpoints = self._validate_endpoints(endpoints, "prefill_instances")
        return self._route_with_round_robin(prefill_endpoints)

    async def route_decode_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        decode_endpoints = self._validate_endpoints(endpoints, "decode_instances")
        return self._route_with_round_robin(decode_endpoints)
