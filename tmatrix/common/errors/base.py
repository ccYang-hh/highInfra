import traceback
from typing import Any, Dict, Optional

from .types import ErrorContext, ErrorInfo


class AppError(Exception):
    """统一应用错误类，所有错误都通过此类表示"""

    def __init__(
            self,
            info: ErrorInfo,
            context: Optional[ErrorContext] = None,
            **kwargs
    ):
        self.info = info
        self.context = context or ErrorContext()
        self.params = kwargs

        # 格式化消息
        self.message = info.message
        if kwargs:
            try:
                self.message = info.message.format(**kwargs)
            except (KeyError, ValueError):
                # 格式化失败，保留原始消息
                pass

        super().__init__(self.message)

    @classmethod
    def from_exception(cls, exception: Exception, error_info: ErrorInfo) -> 'AppError':
        """从普通异常创建应用错误"""
        context = ErrorContext().with_data(
            cause=exception,
            exception_type=type(exception).__name__,
            exception_msg=str(exception),
            traceback=traceback.format_exc()
        )
        return cls(error_info, context)

    # 增加context相关的方法
    def with_context(self, **kwargs) -> 'AppError':
        """添加上下文信息，返回自身"""
        self.context.with_data(**kwargs)
        return self

    def set_context(self, context: ErrorContext) -> 'AppError':
        """设置完整的上下文"""
        self.context = context
        return self

    def merge_context(self, context: ErrorContext) -> 'AppError':
        """合并上下文"""
        self.context = self.context.merge(context)
        return self

    def to_dict(self) -> Dict[str, Any]:
        """将错误转换为字典"""
        result = {
            "error": {
                "code": self.info.code,
                "message": self.message,
                "category": self.info.category,
                "context": {
                    "trace_id": self.context.trace_id,
                    "timestamp": self.context.timestamp,
                }
            }
        }

        # 添加可选字段
        if self.context.request_id:
            result["error"]["context"]["request_id"] = self.context.request_id
        if self.context.user_id:
            result["error"]["context"]["user_id"] = self.context.user_id
        if self.context.metadata:
            result["error"]["context"]["metadata"] = self.context.metadata

        return result
