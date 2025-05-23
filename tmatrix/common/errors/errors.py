from functools import wraps
from contextlib import contextmanager
from typing import Union, cast

from .base import AppError
from .types import ErrorInfo, ErrorContext
from .registry import ErrorRegistry, ErrorDef


class Errors:
    """统一错误处理工具类"""

    @staticmethod
    def _get_registry() -> ErrorRegistry:
        return ErrorRegistry()

    @classmethod
    def error(cls, error_info: Union[ErrorInfo, ErrorDef, str], **kwargs) -> AppError:
        """创建一个错误实例

        参数:
            error_info: 错误信息或错误码
            **kwargs: 格式化参数和上下文数据
        """
        registry = cls._get_registry()

        # 提取上下文数据
        context_kwargs = {}
        format_kwargs = {}

        for k, v in kwargs.items():
            if k in ('request_id', 'user_id', 'trace_id'):
                context_kwargs[k] = v
            format_kwargs[k] = v

        # 创建上下文
        context = ErrorContext(**context_kwargs) if context_kwargs else None

        # 获取错误信息
        if isinstance(error_info, str):
            info = registry.get_or_unknown(error_info)
        else:
            info = error_info

        return AppError(info, context, **format_kwargs)

    @classmethod
    def raise_error(cls, error_info: Union[ErrorInfo, ErrorDef, str], **kwargs) -> None:
        """抛出一个错误

        参数:
            error_info: 错误信息或错误码
            **kwargs: 格式化参数和上下文数据
        """
        raise cls.error(error_info, **kwargs)

    @classmethod
    def wrap(cls, exception: Exception, code: str = "SYSTEM/UNKNOWN") -> AppError:
        """包装一个普通异常为应用错误"""
        if isinstance(exception, AppError):
            return exception

        registry = cls._get_registry()
        error_info = registry.get_or_unknown(code)
        return AppError.from_exception(exception, error_info)

    @classmethod
    def is_app_error(cls, exception: Exception) -> bool:
        """检查异常是否为应用错误"""
        return isinstance(exception, AppError)

    @classmethod
    @contextmanager
    def context(cls, **context_data):
        """创建错误上下文管理器"""
        try:
            yield
        except Exception as e:
            if cls.is_app_error(e):
                app_error = cast(AppError, e)
                app_error.with_context(**context_data)
            else:
                app_error = cls.wrap(e)
                app_error.with_context(**context_data)
            raise app_error

    @classmethod
    def with_context(cls, **context_data):
        """函数错误上下文装饰器"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                with cls.context(**context_data):
                    return func(*args, **kwargs)
            return wrapper
        return decorator
