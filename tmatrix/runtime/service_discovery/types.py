from enum import Enum
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


class ServiceDiscoveryType(Enum):
    """支持实现服务发现的类型枚举"""
    STATIC = "static"
    K8S = "k8s"
    ETCD = "etcd"


class TransportType(Enum):
    """传输类型"""
    TCP = "tcp"
    HTTP = "http"
    HTTPS = "https"


class EndpointType(Enum):
    """端点服务类型，基于OpenAI"""
    COMPLETION = "completion"
    CHAT_COMPLETION = "chat_completion"


class EndpointStatus(Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class KVRole(Enum):
    PRODUCER: str = "producer"
    CONSUMER: str = "consumer"
    BOTH: str = "both"


@dataclass
class KVEventsConfig:
    # enable_kv_cache_events: bool = False
    publisher: str = "zmq"
    endpoint: str = "tcp://*:5557"
    replay_endpoint: Optional[str] = None
    # 以下参数可默认不暴露可配置
    # buffer_steps: int = 10_000
    # hwm: int = 100_000
    # max_queue_size: int = 100_000
    # topic: str = ""


# 高性能端点信息，保持轻量
@dataclass(frozen=True)  # 不可变对象，提高性能
class Endpoint:
    """端点信息"""
    endpoint_id: str               # endpoint标识
    address: str                   # 连接地址
    model_name: str                # 当前主服务模型
    priority: int = 0                 # 优先级，可用于负载均衡
    kv_role: KVRole = KVRole.BOTH     # KV角色
    instance_name: Optional[str] = None  # 当前实例名称/ID, 标识符
    transport_type: TransportType = TransportType.HTTP  # 传输类型
    status: EndpointStatus = EndpointStatus.UNHEALTHY   # 端点健康状态
    endpoint_type: List[EndpointType] = field(default_factory=list)    # 端点服务类型
    kv_event_config: KVEventsConfig = field(default_factory=lambda: KVEventsConfig())  # KVEvent配置
    added_timestamp: float = field(default_factory=lambda: time.time())                # 添加时间
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


class PodServiceLabel(Enum):
    """POD资源标签，用于服务发现"""
    MODEL_NAME = "tmatrix.service.model"
    SERVICE_TRANSPORT_TYPE = "tmatrix.service.transport"
    SERVICE_PRIORITY = "tmatrix.service.priority"
