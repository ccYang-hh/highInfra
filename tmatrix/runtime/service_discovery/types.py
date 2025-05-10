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


# 高性能端点信息，保持轻量
@dataclass(frozen=True)  # 不可变对象，提高性能
class EndpointInfo:
    """端点信息"""
    worker_id: str                 # worker标识
    address: str                   # 连接地址
    model_name: str                # 模型名称
    transport_type: TransportType  # 传输类型
    added_timestamp: float         # 添加时间
    priority: int = 0              # 优先级，可用于负载均衡
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
