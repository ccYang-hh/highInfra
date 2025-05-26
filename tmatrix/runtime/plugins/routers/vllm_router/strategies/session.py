from uhashring import HashRing
from typing import Dict, List

from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.core import RequestContext
from .base import RequestStats, EngineStats, RouteStrategy


class SessionStrategy(RouteStrategy):
    """基于会话的路由策略"""

    def __init__(self, session_key: str = "x-session-id"):
        super().__init__()
        self.session_key = session_key
        self.hash_ring = HashRing()

    def _update_hash_ring(self, endpoints: List[Endpoint]):
        """更新哈希环"""
        # 提取端点URL
        endpoint_urls = [endpoint.address for endpoint in endpoints]

        # 获取哈希环中当前节点
        current_nodes = set(self.hash_ring.get_nodes())

        # 将新端点URL转换为集合，便于比较
        new_nodes = set(endpoint_urls)

        # 移除不再存在的节点
        for node in current_nodes - new_nodes:
            self.hash_ring.remove_node(node)

        # 添加新节点
        for node in new_nodes - current_nodes:
            self.hash_ring.add_node(node)

    @staticmethod
    def _qps_routing(
            endpoints: List[Endpoint],
            request_stats: Dict[str, RequestStats]
    ) -> str:
        """基于QPS选择负载最低的后端"""
        lowest_qps = float("inf")
        ret = None

        for info in endpoints:
            url = info.address
            if url not in request_stats:
                return url  # 该引擎尚无请求

            request_stat = request_stats[url]
            if request_stat.qps < lowest_qps:
                lowest_qps = request_stat.qps
                ret = url

        if ret is None and endpoints:
            return endpoints[0].address

        return ret

    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """基于会话ID路由请求"""
        if not endpoints:
            raise ValueError("No available endpoints for routing")

        prefill_endpoints: List[Endpoint] = endpoints["prefill_instances"]
        if len(prefill_endpoints) == 0:
            raise ValueError("No available endpoints for routing")

        # 只有一个Prefill实例时，不需要额外调度
        if len(prefill_endpoints) == 1:
            return prefill_endpoints[0].address

        # 获取会话ID
        session_id = context.headers.get(self.session_key)

        # 更新哈希环
        self._update_hash_ring(prefill_endpoints)

        if not session_id:
            # 如果没有会话ID，则基于QPS路由
            url = self._qps_routing(prefill_endpoints, request_stats)
        else:
            # 使用哈希环获取会话ID的端点
            url = self.hash_ring.get_node(session_id)

        return url

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
