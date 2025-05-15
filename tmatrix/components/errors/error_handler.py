import traceback
from functools import wraps
from contextlib import contextmanager
from typing import Any, Callable, List, TypeVar, Generator

from .base import BaseError
from .registry import ErrorRegistry
from .adapters import ErrorOutputAdapter
from .types import ErrorCategory, ErrorContext, ErrorDefinition, SYSTEM_UNKNOWN_ERROR


# 类型变量定义
T = TypeVar('T')
R = TypeVar('R')


class BaseErrorHandler:
    """错误处理器基类"""

    def __init__(self):
        self.global_context = ErrorContext()

    def set_global_context(self, **kwargs) -> 'BaseErrorHandler':
        """设置全局上下文，支持链式调用"""
        for key, value in kwargs.items():
            if hasattr(self.global_context, key):
                setattr(self.global_context, key, value)
            else:
                self.global_context.metadata[key] = value
        return self

    def _prepare_error(self, error: Exception) -> BaseError:
        """准备错误，合并上下文并处理非BaseError类型"""
        if isinstance(error, BaseError):
            # 合并全局上下文
            merged_context = self.global_context.merge(error.context)
            error.context = merged_context
            return error
        else:
            registry = ErrorRegistry()
            base_error = registry.get_or_create_error(SYSTEM_UNKNOWN_ERROR)

            # 添加堆栈跟踪到上下文
            tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            base_error.context.with_metadata("traceback", tb_str)

            return base_error


class ErrorHandler(BaseErrorHandler):
    """同步错误处理器"""

    def __init__(self):
        super().__init__()
        self.adapters: List[ErrorOutputAdapter] = []

    def add_adapter(self, adapter: ErrorOutputAdapter) -> 'ErrorHandler':
        """添加输出适配器，支持链式调用"""
        self.adapters.append(adapter)
        return self

    def handle(self, error: Exception) -> List[Any]:
        """处理异常，返回所有适配器的输出结果"""
        app_error = self._prepare_error(error)

        # 通过所有适配器输出错误
        results = []
        for adapter in self.adapters:
            try:
                result = adapter.output(app_error)
                results.append(result)
            except Exception as e:
                # 适配器出错不应影响其他适配器
                import sys
                print(f"错误适配器异常: {e}", file=sys.stderr)

        return results

    def error_boundary(self, func: Callable[..., R]) -> Callable[..., R]:
        """错误边界装饰器，捕获函数中的错误并通过处理器处理"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                self.handle(e)
                raise  # 重新抛出异常，允许上层继续处理

        return wrapper

    @staticmethod
    def with_context(**context_data) -> Callable[[Callable[..., R]], Callable[..., R]]:
        """
        为函数添加错误上下文的装饰器

        example:
            >>> handler = ErrorHandler()
            ... @handler.with_context(user_id=123, request_id="xyz")
            ... def some_func(...)

        1. 如果捕获到的是BaseError，把此局部上下文合并到该错误上下文内，然后重新抛出
        2. 如果捕获到的是其它异常，包装成BaseError，将局部上下文作为其上下文信息，抛出新的BaseError
        """

        def decorator(func: Callable[..., R]) -> Callable[..., R]:
            @wraps(func)
            def wrapper(*args, **kwargs):
                # 创建本地上下文
                local_context = ErrorContext()
                for key, value in context_data.items():
                    if hasattr(local_context, key):
                        setattr(local_context, key, value)
                    else:
                        local_context.metadata[key] = value

                try:
                    return func(*args, **kwargs)
                except BaseError as e:
                    # 合并本地上下文到错误上下文
                    e.context = local_context.merge(e.context)
                    raise
                except Exception as e:
                    registry = ErrorRegistry()
                    base_error = registry.get_or_create_error(SYSTEM_UNKNOWN_ERROR)
                    raise base_error from e

            return wrapper

        return decorator

    @contextmanager
    def error_context(self, **context_data) -> Generator[None, None, None]:
        """
        上下文管理器，为代码块添加错误上下文

        example:
            >>> handler = ErrorHandler()
            ... with handler.error_context(user_id=123, request_id="xyz")
            ...     pass

        1. 代码块运行期间如果抛出 BaseError，就合并上下文信息后重抛
        2. 如果抛出其它异常，包装成 BaseError 并附带上下文，抛出
        """
        # 创建本地上下文
        local_context = ErrorContext()
        for key, value in context_data.items():
            if hasattr(local_context, key):
                setattr(local_context, key, value)
            else:
                local_context.metadata[key] = value

        try:
            yield
        except BaseError as e:
            # 合并本地上下文到错误上下文
            e.context = local_context.merge(e.context)
            raise
        except Exception as e:
            registry = ErrorRegistry()
            base_error = registry.get_or_create_error(SYSTEM_UNKNOWN_ERROR)
            raise base_error from e


# TODO, Support AsyncErrorHandler
class AsyncErrorHandler(BaseErrorHandler):
    pass
