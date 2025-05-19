from typing import Dict, List

from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.core import RequestContext
from .base import EngineStats, RequestStats, RouteStrategy


class RoundRobinStrategy(RouteStrategy):
    """轮询路由策略"""

    def __init__(self):
        self.req_id = 0

    async def route_request(
            self,
            endpoints: List[Endpoint],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """轮询方式选择后端URL"""
        if not endpoints or len(endpoints) == 0:
            raise ValueError("No available endpoints for routing")

        len_engines = len(endpoints)
        chosen = sorted(endpoints, key=lambda e: e.url)[self.req_id % len_engines]
        self.req_id += 1
        return chosen.address
