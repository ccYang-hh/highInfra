import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from tmatrix.components.logging import init_logger
logger = init_logger("components/errors")


class ErrorCategory(str, Enum):
    """错误类别枚举，用于对错误进行分类"""
    VALIDATION = "validation"    # 数据验证错误
    BUSINESS = "business"        # 业务逻辑错误
    SYSTEM = "system"            # 系统内部错误
    EXTERNAL = "external"        # 外部系统错误
    SECURITY = "security"        # 安全相关错误


SYSTEM_UNKNOWN_ERROR = "000000"

# 公共ERRORS，在Registry初始化时即注册
COMMON_ERRORS = [
    (SYSTEM_UNKNOWN_ERROR, "系统未知错误!"),
]


@dataclass
class ErrorContext:
    """错误上下文信息"""
    # 可选标识信息
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None

    # 基础追踪信息
    timestamp: float = field(default_factory=time.time)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # 扩展数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def with_metadata(self, key: str, value: Any) -> 'ErrorContext':
        """添加元数据并返回自身，支持链式调用"""
        self.metadata[key] = value
        return self

    def merge(self, other: 'ErrorContext') -> 'ErrorContext':
        """合并另一个上下文的非空值到当前上下文"""
        result = ErrorContext(
            timestamp=self.timestamp,
            trace_id=self.trace_id,
            request_id=other.request_id or self.request_id,
            session_id=other.session_id or self.session_id,
            user_id=other.user_id or self.user_id,
        )

        # 合并元数据，优先使用other的值
        result.metadata = self.metadata.copy()
        result.metadata.update(other.metadata)

        return result


@dataclass(frozen=True)
class ErrorDefinition:
    """不可变的错误定义类，确保错误定义一旦创建不可修改"""
    code: str                            # 错误码，如 "AUTH-001"
    message: str                         # 错误消息模板
    status_code: int = 500               # HTTP状态码 (用于Web场景)
    category: ErrorCategory = ErrorCategory.SYSTEM  # 错误类别

    def format_message(self, **kwargs) -> str:
        """使用提供的参数格式化错误消息"""
        if not kwargs:  # 优化：如果没有参数，直接返回原始消息
            return self.message

        try:
            return self.message.format(**kwargs)
        except KeyError:
            # 如果缺少格式化参数，添加提示但仍返回有意义的消息
            e_message = f"{self.message} (格式化错误: 缺少参数 {KeyError})"
            logger.error(e_message)
            return e_message
        except Exception:
            # 其他格式化错误，返回原始消息
            return self.message
