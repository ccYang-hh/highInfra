import asyncio
from uhashring import HashRing
from typing import Dict, List, Optional

from tmatrix.common.logging import init_logger
from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.metrics import LoadBalancer, RealtimeVLLMLoadBalancer
from tmatrix.runtime.metrics import MetricsClientConfig, AsyncMetricsClient, RealtimeVLLMLoadBalancer
from tmatrix.runtime.core import RequestContext
from .base import BaseRouter

logger = init_logger("plugins/router")


class SessionStrategy(BaseRouter):
    """基于会话的路由策略"""

    def __init__(self, load_balancer: Optional[LoadBalancer] = None):
        super().__init__()
        self.hash_ring = HashRing()
        self._lock = asyncio.Lock()
        self._load_balancer: Optional[RealtimeVLLMLoadBalancer] = load_balancer
        self._binding_store: Optional[Dict[str, Optional[Endpoint]]] = {}

    def update_binding_store(self, chat_id: str, endpoint: Endpoint) -> None:
        logger.info(f"Session Store: 记录当前Chat ID {chat_id}")
        self._binding_store[chat_id] = endpoint

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

    async def _route_with_session(self, endpoints: List[Endpoint], context: RequestContext) -> Endpoint:
        # 只有一个实例时，不需要额外调度
        if len(endpoints) == 1:
            return endpoints[0]

        # 获取会话ID
        chat_id = context.request_identifiers.get("chat_id", "")
        if not chat_id:
            # 改为负载均衡的调度（正常走不到该分支）
            return await self._route_with_load_balancing_only(endpoints)

        # 获取会话ID的端点
        return self._binding_store.get(chat_id, None)

    async def route_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """非PD分离场景使用该API进行调度"""
        both_endpoints = self._validate_endpoints(endpoints, "both_instances")
        return await self._route_with_session(both_endpoints, context)

    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """基于会话ID路由请求"""
        prefill_endpoints = self._validate_endpoints(endpoints, "prefill_instances")
        return await self._route_with_session(prefill_endpoints, context)

    async def route_decode_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        decode_endpoints = self._validate_endpoints(endpoints, "decode_instances")
        return await self._route_with_session(decode_endpoints, context)
