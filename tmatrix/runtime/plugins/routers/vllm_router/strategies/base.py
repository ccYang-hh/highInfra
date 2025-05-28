import abc
from typing import Dict, List, Optional

from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.metrics import LoadBalancer, RealtimeVLLMLoadBalancer
from tmatrix.runtime.core import RequestContext

from tmatrix.common.logging import init_logger
logger = init_logger("plugins/router")


class BaseRouter(abc.ABC):
    """路由策略的基类"""

    def __init__(self, load_balancer: Optional[LoadBalancer] = None):
        self._initialized = False
        self._load_balancer: Optional[RealtimeVLLMLoadBalancer] = load_balancer

    async def initialize(self):
        await self._initialize()
        self._initialized = True

    async def _initialize(self):
        pass

    @staticmethod
    def _validate_endpoints(endpoints: Dict[str, List[Endpoint]], endpoint_type: str) -> List[Endpoint]:
        """验证端点列表并返回指定类型的端点"""
        if not endpoints:
            raise ValueError("No available endpoints for routing")

        target_endpoints = endpoints.get(endpoint_type, [])
        if not target_endpoints:
            raise ValueError(f"No available {endpoint_type} for routing")

        return target_endpoints

    def _get_current_load_stats(self, instance_name: str) -> Dict:
        """返回指定实例的性能"""
        scores_data = self._load_balancer.get_load_status()

        result = None
        for instance in scores_data['instances']:
            if instance['instance_id'] == instance_name:
                result = instance

        return result

    def _find_min_load_endpoint(self, endpoints: List[Endpoint], instance_names: List[str]) -> Optional[Endpoint]:
        """基于负载均衡策略找到负载最小的端点"""
        if not self._load_balancer:
            return endpoints[0]

        scores_data = self._load_balancer.get_load_status()
        logger.info(f"运行状态: {scores_data['is_running']}, "
                    f"实例总数: {scores_data['total_instances']}, "
                    f"健康实例: {scores_data['healthy_instances']}")

        min_load_score = float('inf')
        min_instance_id = None

        for instance in scores_data['instances']:
            if instance['instance_id'] in instance_names:
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

        # 根据实例ID找到对应的端点
        for endpoint in endpoints:
            if endpoint.instance_name == min_instance_id:
                return endpoint

        return endpoints[0]

    async def _route_with_load_balancing_only(self, endpoints: List[Endpoint]) -> Endpoint:
        """仅基于负载均衡的路由逻辑"""
        # 只有一个实例时，直接返回
        if len(endpoints) == 1 or not self._load_balancer:
            return endpoints[0]

        instance_names = [item.instance_name for item in endpoints]
        target = self._find_min_load_endpoint(endpoints, instance_names)
        return target if target else endpoints[0]

    @abc.abstractmethod
    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """根据路由策略选择合适的Prefill实例URL"""
        pass

    @abc.abstractmethod
    async def route_decode_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """根据路由策略选择合适的Decode实例URL"""
        pass

    @abc.abstractmethod
    async def route_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            context: RequestContext
    ) -> Endpoint:
        """非PD分离场景使用该API进行调度"""
        pass

    @property
    def initialized(self):
        return self._initialized
