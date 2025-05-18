import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional
from dataclasses import dataclass, field


# 业务模块可追加此类
class ErrorCategory(str, Enum):
    """错误类别枚举，用于对错误进行分类"""
    VALIDATION = "validation"    # 数据验证错误
    BUSINESS = "business"        # 业务逻辑错误
    SYSTEM = "system"            # 系统内部错误
    EXTERNAL = "external"        # 外部系统错误
    SECURITY = "security"        # 安全相关错误


@dataclass
class ErrorInfo:
    """错误基本信息，不可变"""
    code: str
    message: str
    status_code: int = 500
    category: ErrorCategory = ErrorCategory.SYSTEM

    def __str__(self):
        return f"{self.code}:{self.message}"


@dataclass
class ErrorContext:
    """错误上下文信息"""
    request_id: Optional[str] = None
    user_id: Optional[str] = None

    timestamp: float = field(default_factory=time.time)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)

    cause: Optional[Exception] = None  # 原始异常，支持错误链

    def with_data(self, **kwargs) -> 'ErrorContext':
        """添加元数据，支持链式调用"""
        for key, value in kwargs.items():
            if hasattr(self, key) and key not in ('metadata', 'cause'):
                setattr(self, key, value)
            else:
                self.metadata[key] = value
        return self

    def merge(self, other: Optional['ErrorContext'] = None) -> 'ErrorContext':
        """合并另一个上下文"""
        if not other:
            return self

        # 创建新实例避免修改原实例
        result = ErrorContext(
            request_id=other.request_id or self.request_id,
            user_id=other.user_id or self.user_id,
            trace_id=other.trace_id,  # 优先使用其他上下文的trace_id
            timestamp=other.timestamp,  # 优先使用其他上下文的时间戳
            cause=other.cause or self.cause
        )

        # 合并元数据
        result.metadata = self.metadata.copy()
        result.metadata.update(other.metadata)

        return result
