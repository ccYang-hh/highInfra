import httpx
import asyncio
import threading
from typing import Dict, List

from tmatrix.common.logging import init_logger
from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.core import RequestContext
from .base import RequestStats, EngineStats, RouteStrategy

logger = init_logger("plugins/router")


class KVCacheAwareStrategy(RouteStrategy):
    """基于KV缓存的路由策略"""

    def __init__(self, lmcache_controller_port: int = 8000):
        self.lmcache_controller_port = lmcache_controller_port
        self.req_id = 0
        self.instance_id_to_ip = {}

        # 初始化KV缓存管理器
        try:
            from lmcache.experimental.cache_controller import controller_manager
            from lmcache.experimental.cache_controller.message import (
                LookupMsg, QueryInstMsg, QueryInstRetMsg
            )
            self.controller_manager = controller_manager.LMCacheControllerManager(
                f"0.0.0.0:{self.lmcache_controller_port}"
            )
            self.LookupMsg = LookupMsg
            self.QueryInstMsg = QueryInstMsg
            self.kv_cache_available = True

            # 启动KV缓存管理器
            self._start_kv_manager()
        except ImportError:
            logger.warning("lmcache package not found, KVCacheAware routing disabled")
            self.kv_cache_available = False

    def _start_kv_manager(self):
        """启动KV缓存管理器"""
        if not self.kv_cache_available:
            return

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.controller_manager.start_all(), self.loop)

    async def _query_manager(self, msg):
        """查询KV缓存管理器"""
        if not self.kv_cache_available:
            return None

        instance_id = self.controller_manager.handle_orchestration_message(msg)
        return instance_id

    @staticmethod
    async def _tokenize(endpoint: Endpoint, prompt: str) -> List[int]:
        """将提示文本转换为token ID"""
        url = endpoint.address + "/tokenize"
        headers = {"Content-Type": "application/json"}
        data = {"model": endpoint.model_name, "prompt": prompt}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=data)
            response_json = response.json()

        return response_json.get("tokens", [])

    async def route_request(
            self,
            endpoints: List[Endpoint],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """基于KV缓存路由请求"""
        if not endpoints:
            raise ValueError("No available endpoints for routing")

        if not self.kv_cache_available:
            # 如果KV缓存不可用，则使用轮询
            len_engines = len(endpoints)
            chosen = sorted(endpoints, key=lambda e: e.url)[self.req_id % len_engines]
            self.req_id += 1
            return chosen.address

        # 获取请求中的提示文本
        prompt = context.parsed_body.get("prompt", "")
        if not prompt:
            # 如果没有提示文本，则使用轮询
            len_engines = len(endpoints)
            chosen = sorted(endpoints, key=lambda e: e.url)[self.req_id % len_engines]
            self.req_id += 1
            return chosen.address

        # 将提示文本转换为token ID
        token_ids = await self._tokenize(endpoints[0], prompt)

        # 查询KV缓存管理器
        msg = self.LookupMsg(tokens=token_ids)
        instance_id = await self._query_manager(msg)

        if instance_id is None or instance_id.best_instance_id is None:
            # 如果没有找到匹配的实例，则使用轮询
            len_engines = len(endpoints)
            chosen = sorted(endpoints, key=lambda e: e.url)[self.req_id % len_engines]
            self.req_id += 1
            return chosen.address
        else:
            self.req_id += 1
            if instance_id.best_instance_id not in self.instance_id_to_ip:
                # 查询每个端点，找到匹配的实例ID
                for endpoint in endpoints:
                    url_parts = endpoint.address.split("://")
                    if len(url_parts) > 1:
                        host = url_parts[1].split(":")[0]
                        query_message = self.QueryInstMsg(ip=host)
                        response = await self._query_manager(query_message)
                        if response and hasattr(response, 'instance_id'):
                            self.instance_id_to_ip[response.instance_id] = endpoint.address

            # 返回匹配的实例URL
            if instance_id.best_instance_id in self.instance_id_to_ip:
                return self.instance_id_to_ip[instance_id.best_instance_id]
            else:
                # 如果没有找到匹配的URL，则使用轮询
                len_engines = len(endpoints)
                chosen = sorted(endpoints, key=lambda e: e.url)[self.req_id % len_engines]
                self.req_id += 1
                return chosen.address
