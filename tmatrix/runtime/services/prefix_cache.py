from typing import List, Dict, Optional

from tmatrix.common.logging import init_logger
from tmatrix.runtime.metrics import StatType, KVEventStats, MetricsRegistry
from tmatrix.runtime.service_discovery import get_etcd_service_discovery
from tmatrix.runtime.components.events_subscriber import (
    PrefixCacheFinder, ZMQInstanceConfig, ZMQEventSubscriber, SubscriberFactory,
)
logger = init_logger("services/prefix_cache_service")


def get_prefix_cache_service() -> "PrefixCacheService":
    return PrefixCacheServiceSingleton.get_instance()


class PrefixCacheServiceSingleton:
    """PrefixCacheService单例"""
    _instance = None

    @classmethod
    def get_instance(cls, subscriber_type: str = "zmq", topic: str = "kv-events"):
        if cls._instance is None:
            cls._instance = PrefixCacheService(subscriber_type=subscriber_type, topic=topic)
        return cls._instance


class PrefixCacheService:
    """
    前缀缓存服务
    集成了前缀缓存查找器和事件订阅器
    """

    def __init__(self, subscriber_type: str = "zmq", topic: str = "kv-events"):
        """
        初始化前缀缓存服务
        参数:
            subscriber_type: 订阅器类型，支持"zmq"
            topic: 订阅主题
        """
        # 创建统计模块
        self.stats: Optional[KVEventStats] = MetricsRegistry().get_stats_instance(StatType.KV_EVENTS)

        # 创建前缀缓存查找器
        self.finder = PrefixCacheFinder(stats=self.stats)

        # 创建订阅器
        if subscriber_type.lower() == "zmq":
            self.subscriber = SubscriberFactory.create_zmq_subscriber(
                self.finder, topic=topic
            )
        elif subscriber_type.lower() == "nats":
            # TODO 这里应该创建NATS订阅器，但尚未实现
            raise ValueError("NATS订阅器尚未实现")
        else:
            raise ValueError(f"不支持的订阅器类型: {subscriber_type}")

    def start(self) -> None:
        """启动服务"""
        self.subscriber.start()
        service_discovery = get_etcd_service_discovery()
        endpoints = service_discovery.get_endpoints()
        for endpoint in endpoints:
            self.add_vllm_instance(endpoint.instance_name, endpoint.kv_event_config.endpoint)
        logger.info("全局前缀缓存感知服务已启动")

    def stop(self) -> None:
        """停止服务"""
        self.subscriber.stop()
        self.finder.shutdown()
        logger.info("全局前缀缓存感知服务已停止")

    def add_vllm_instance(self, instance_id: str, pub_endpoint: str = "tcp://*:5557", replay_endpoint=None) -> bool:
        """
        添加vLLM实例
        参数:
            instance_id: 实例ID
            pub_endpoint: 发布地址
            replay_endpoint: 重放地址
        返回:
            是否成功添加
        """
        if not isinstance(self.subscriber, ZMQEventSubscriber):
            raise TypeError("当前订阅器不支持添加ZMQ实例")

        config = ZMQInstanceConfig(
            instance_id=instance_id,
            pub_endpoint=pub_endpoint,
            replay_endpoint=replay_endpoint
        )

        return self.subscriber.add_instance(config)

    def remove_vllm_instance(self, instance_id: str) -> bool:
        """移除vLLM实例"""
        if not hasattr(self.subscriber, 'remove_instance'):
            raise TypeError("当前订阅器不支持移除实例")

        return self.subscriber.remove_instance(instance_id)

    async def find_longest_prefix_match(self, token_ids: List[int]) -> Optional[Dict]:
        """
        查找最长的前缀匹配
        参数:
            token_ids: 输入token序列
        返回:
            匹配结果字典，若无匹配则返回None
        """
        match = self.finder.find_longest_prefix_match(token_ids)
        if not match:
            return None

        return {
            "instance_id": match.instance_id,
            "block_hash": match.block_hash,
            "match_length": match.match_length,
            "token_ids": match.block_info.token_ids,
            "timestamp": match.block_info.timestamp
        }

    async def find_all_prefix_matches(self, token_ids: List[int], limit: int = 5) -> List[Dict]:
        """
        查找所有实例的最长前缀匹配
        参数:
            token_ids: 输入token序列
            limit: 返回结果的最大数量
        返回:
            按匹配长度降序排序的匹配结果列表
        """
        matches = self.finder.find_all_prefix_matches(token_ids, limit=limit)

        results = []
        for match in matches:
            results.append({
                "instance_id": match.instance_id,
                "block_hash": match.block_hash,
                "match_length": match.match_length,
                "token_ids": match.block_info.token_ids,
                "timestamp": match.block_info.timestamp
            })

        return results

    def get_stats(self) -> Dict:
        """获取统计信息"""
        stats_dict = self.stats.to_dict()

        # 添加订阅器状态
        if hasattr(self.subscriber, 'get_running_instances'):
            stats_dict["running_instances"] = self.subscriber.get_running_instances()
            stats_dict["instance_count"] = self.subscriber.get_instance_count()

        return stats_dict
