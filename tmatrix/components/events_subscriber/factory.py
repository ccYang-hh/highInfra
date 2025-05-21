from .kv_events import KVEventHandlerBase
from .base import EventSubscriberConfig
from .zmq_kvevent_subscriber import ZMQEventSubscriber
from tmatrix.components.logging import init_logger

logger = init_logger("events_subscriber/kv_events")


class SubscriberFactory:
    """
    订阅器工厂类
    用于创建不同类型的事件订阅器
    """

    @staticmethod
    def create_zmq_subscriber(event_handler: KVEventHandlerBase,
                              topic: str = "kv-events") -> ZMQEventSubscriber:
        """
        创建ZMQ事件订阅器
        参数:
            event_handler: 事件处理器
            topic: 订阅主题
        返回:
            ZMQ事件订阅器
        """
        config = EventSubscriberConfig(topic=topic)
        return ZMQEventSubscriber(event_handler, config)

    @staticmethod
    def create_nats_subscriber(event_handler: KVEventHandlerBase,
                               topic: str = "kv-events") -> None:
        """
        创建NATS事件订阅器（尚未实现）
        参数:
            event_handler: 事件处理器
            topic: 订阅主题
        返回:
            None（未实现）
        """
        # 这里返回None，实际中应该返回NATS实现的订阅器
        logger.warning("NATS订阅器尚未实现")
        return None

