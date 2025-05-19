from enum import Enum, auto
import time
from typing import Dict, Any, List
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
    COMPLETION = auto()
    CHAT_COMPLETION = auto()


class EndpointStatus(Enum):
    HEALTHY = auto()
    UNHEALTHY = auto()


# 高性能端点信息，保持轻量
@dataclass(frozen=True)  # 不可变对象，提高性能
class Endpoint:
    """端点信息"""
    endpoint_id: str               # endpoint标识
    address: str                   # 连接地址
    model_name: str                # 当前主服务模型
    priority: int = 0                 # 优先级，可用于负载均衡
    transport_type: TransportType = TransportType.HTTP  # 传输类型
    status: EndpointStatus = EndpointStatus.UNHEALTHY   # 端点健康状态
    endpoint_type: List[EndpointType] = field(default_factory=list)   # 端点服务类型
    added_timestamp: float = field(default_factory=lambda: time.time())  # 添加时间
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


class PodServiceLabel(Enum):
    """POD资源标签，用于服务发现"""
    MODEL_NAME = "tmatrix.service.model"
    SERVICE_TRANSPORT_TYPE = "tmatrix.service.transport"
    SERVICE_PRIORITY = "tmatrix.service.priority"
