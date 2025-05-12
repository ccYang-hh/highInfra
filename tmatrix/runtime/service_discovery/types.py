import enum
from typing import Dict, Any
from dataclasses import dataclass, field


class ServiceDiscoveryType(enum.Enum):
    """支持实现服务发现的类型枚举"""
    STATIC = "static"
    K8S = "k8s"
    ETCD = "etcd"


class TransportType(enum.Enum):
    """传输类型"""
    TCP = "tcp"
    HTTP = "http"
    HTTPS = "https"


class EndpointType(enum.Enum):
    """端点服务类型，基于OpenAI"""
    COMPLETION = 0
    CHAT_COMPLETION = 1


class EndpointStatus(enum.Enum):
    HEALTHY = 0
    UNHEALTHY = 1


# 高性能端点信息，保持轻量
@dataclass(frozen=True)  # 不可变对象，提高性能
class Endpoint:
    """端点信息"""
    endpoint_id: str               # endpoint标识
    endpoint_type: EndpointType    # 端点服务类型
    address: str                   # 连接地址
    model_name: str                # 当前主服务模型
    transport_type: TransportType  # 传输类型
    added_timestamp: float         # 添加时间
    priority: int = 0              # 优先级，可用于负载均衡
    status: EndpointStatus = EndpointStatus.UNHEALTHY       # 端点健康状态
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


class PodServiceLabel(enum.Enum):
    """POD资源标签，用于服务发现"""
    MODEL_NAME = "tmatrix.service.model"
    SERVICE_TRANSPORT_TYPE = "tmatrix.service.transport"
    SERVICE_PRIORITY = "tmatrix.service.priority"
