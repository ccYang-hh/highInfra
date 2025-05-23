from abc import ABC, abstractmethod
from typing import Optional

from .kv_events import KVEventHandlerBase


class EventSubscriberConfig:
    """事件订阅器配置类"""

    def __init__(self, topic: str = "kv-events"):
        self.topic = topic


class ZMQInstanceConfig:
    """ZMQ实例配置类"""

    def __init__(
            self,
            instance_id: str,
            pub_endpoint: str,
            replay_endpoint: Optional[str] = None
    ):
        self.instance_id = instance_id
        self.pub_endpoint = pub_endpoint
        self.replay_endpoint = replay_endpoint


class EventSubscriberBase(ABC):
    """
    事件订阅器基类
    定义了事件订阅的接口
    """

    def __init__(self, event_handler: KVEventHandlerBase, config: EventSubscriberConfig):
        """
        初始化事件订阅器
        参数:
            event_handler: 事件处理器
            config: 订阅器配置
        """
        self.event_handler = event_handler
        self.config = config
        self.running = False

    @abstractmethod
    def start(self) -> None:
        """启动订阅器"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止订阅器"""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """检查订阅器是否运行中"""
        pass