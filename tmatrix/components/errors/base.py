from typing import Any, Dict, Optional

from .types import ErrorContext, ErrorDefinition


class BaseError(Exception):
    """应用错误基类，所有自定义错误继承此类"""

    def __init__(
            self,
            error_def: ErrorDefinition,
            context: Optional[ErrorContext] = None,
            **kwargs
    ):
        self.error_def = error_def
        self.context = context or ErrorContext()
        self.params = kwargs  # 保存格式化参数，便于后续使用

        # 格式化错误消息
        self.message = error_def.format_message(**kwargs)

        # 调用父类构造函数
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """将错误转换为字典表示"""
        return {
            "error": {
                "code": self.error_def.code,
                "message": self.message,
                "category": self.error_def.category.value,
                "context": {
                    "trace_id": self.context.trace_id,
                    "timestamp": self.context.timestamp,
                    # 只包含非空值，**(...)用于字典解构
                    **({"request_id": self.context.request_id} if self.context.request_id else {}),
                    **({"user_id": self.context.user_id} if self.context.user_id else {}),
                    **({"session_id": self.context.session_id} if self.context.session_id else {}),
                    **({"metadata": self.context.metadata} if self.context.metadata else {})
                }
            }
        }

    @classmethod
    def wrap_exception(cls, exception: Exception, error_def: ErrorDefinition,
                       context: Optional[ErrorContext] = None) -> 'BaseError':
        """包装一个标准的Exception -> BaseError"""
        base_error = cls(error_def, context)

        # 保存原始异常信息
        if context:
            context.with_metadata("original_exception", {
                "type": type(exception).__name__,
                "message": str(exception)
            })

        return base_error