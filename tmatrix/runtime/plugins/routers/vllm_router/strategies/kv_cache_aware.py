import httpx
import asyncio
from typing import Dict, List, Optional

from tmatrix.common.logging import init_logger
from tmatrix.runtime.metrics import LoadBalancer, RealtimeVLLMLoadBalancer
from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.services import PrefixCacheService, get_prefix_cache_service
from tmatrix.runtime.core import RequestContext
from .base import BaseRouter

logger = init_logger("plugins/router")


class KVCacheAwareStrategy(BaseRouter):
    """基于KV缓存的路由策略"""

    def __init__(self, load_balancer: Optional[LoadBalancer] = None):
        super().__init__()
        self.req_id = 0
        self._lock = asyncio.Lock()
        self.kv_cache_available = False
        self._load_balancer: Optional[RealtimeVLLMLoadBalancer] = load_balancer
        self._async_client: Optional[httpx.AsyncClient] = None
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

    def _get_round_robin_endpoint(self, endpoints: List[Endpoint]) -> Endpoint:
        """基于轮询策略选择端点"""
        len_engines = len(endpoints)
        chosen = sorted(endpoints, key=lambda e: e.url)[self.req_id % len_engines]
        self.req_id += 1
        return chosen

    @staticmethod
    def _extract_prompt_from_context(context: RequestContext) -> tuple[str, bool]:
        """从请求上下文中提取提示文本和是否为聊天模式"""
        prompt = ""
        is_chat = True

        if context.endpoint == "/v1/chat/completions":
            messages = context.parsed_body.get("messages", [])
            assert len(messages) == 1, "KVCacheAware感知路由暂不支持处理历史请求"
            message_dict = messages[0]
            prompt = message_dict["content"]
        elif context.endpoint == "/v1/completions":
            prompt = context.parsed_body.get("prompt", "")
            is_chat = False

        return prompt, is_chat

    async def _tokenize(self, endpoint: Endpoint, prompt: str) -> List[int]:
        """将提示文本转换为token ID"""
        url = f"{endpoint.transport_type.value}://{endpoint.address}/tokenize"
        headers = {"Content-Type": "application/json"}
        data = {"model": endpoint.model_name, "prompt": prompt}

        response = await self._async_client.post(url, headers=headers, json=data)
        response_json = response.json()

        return response_json.get("tokens", [])

    @staticmethod
    def _prepare_token_ids(token_ids: List[int], is_chat: bool) -> List[int]:
        """准备token ID，对于聊天模式添加角色标识符"""
        if is_chat:
            # Chat场景，Prompt会追加三个标识符Token，TokenID为：151644, 872, 198
            # TODO 取消硬编码
            role_tokens = [151644, 872, 198]
            return role_tokens + token_ids
        return token_ids

    @staticmethod
    def _find_endpoint_by_instance_id(endpoints: List[Endpoint], instance_id: str) -> Optional[Endpoint]:
        """根据实例ID查找对应的端点"""
        for endpoint in endpoints:
            if endpoint.instance_name == instance_id:
                return endpoint
        return None

    async def _route_with_kv_cache_awareness(
            self,
            endpoints: List[Endpoint],
            context: RequestContext
    ) -> Endpoint:
        """基于KV缓存感知的核心路由逻辑"""
        instance_names = [item.instance_name for item in endpoints]

        # 如果KV缓存不可用，使用轮询策略
        if not self.kv_cache_available:
            return self._get_round_robin_endpoint(endpoints)

        # 提取prompt
        prompt, is_chat = self._extract_prompt_from_context(context)
        if not prompt:
            return self._get_round_robin_endpoint(endpoints)

        # TODO 高优先！！！
        #   1.将tokenize的逻辑统一入队提前调度，异步写入Context
        #   2.选择Prefill实例进行Tokenize时，基于负载均衡策略
        token_ids = await self._tokenize(endpoints[0], prompt)
        token_ids = self._prepare_token_ids(token_ids, is_chat)

        async with self._lock:
            matched_result = await self.prefix_cache_service.find_longest_prefix_match(token_ids)
            logger.info(f"最长前缀匹配结果：{matched_result}")
            # 匹配到了结果
            # if matched_result is not None:


            # 当前使用负载均衡的场景：
            #   1.前缀匹配失败
            #   2.前缀匹配/输入比值小于0.3（防止一直命中同一实例）
            if (matched_result is None or
                    len(matched_result.items()) == 0 or
                    matched_result["match_length"] <= 3 or
                    matched_result["match_length"]/len(token_ids) <= 0.1):

                target = self._find_min_load_endpoint(endpoints, instance_names)
                return target
            else:
                # 基于全局前缀缓存感知
                target = self._find_endpoint_by_instance_id(endpoints, matched_result["instance_id"])
                return target

    async def route_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """非PD分离场景使用该API进行调度"""
        both_endpoints = self._validate_endpoints(endpoints, "both_instances")
        return await self._route_with_kv_cache_awareness(both_endpoints, context)

    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """基于KV缓存路由Prefill请求"""
        prefill_endpoints = self._validate_endpoints(endpoints, "prefill_instances")

        # 只有一个Prefill实例时，不需要额外调度
        if len(prefill_endpoints) == 1:
            return prefill_endpoints[0]

        return await self._route_with_kv_cache_awareness(prefill_endpoints, context)

    async def route_decode_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """路由Decode请求（仅基于负载均衡）"""
        decode_endpoints = self._validate_endpoints(endpoints, "decode_instances")
        return await self._route_with_load_balancing_only(decode_endpoints)
