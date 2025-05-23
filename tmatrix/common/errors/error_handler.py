import json
import logging
from functools import wraps
from typing import Optional, cast

from tmatrix.common.logging import init_logger

from .base import AppError
from .errors import Errors


def create_handler(handler_type: str, **options):
    """创建指定类型的错误处理器

    参数:
        handler_type: 处理器类型，如 'web', 'json', 'log'
        **options: 处理器选项
    """
    if handler_type == 'web':
        return WebErrorHandler(**options)
    elif handler_type == 'json':
        return JsonErrorHandler(**options)
    elif handler_type == 'log':
        return LogErrorHandler(**options)
    else:
        raise ValueError(f"未知的处理器类型: {handler_type}")


class BaseErrorHandler:
    """错误处理器基类"""

    def __init__(self, raise_after_handle: bool = True):
        self.raise_after_handle = raise_after_handle

    def handle(self, error: Exception) -> AppError:
        """处理异常并返回应用错误"""
        # 确保是应用错误
        if not isinstance(error, AppError):
            error = Errors.wrap(error)

        return cast(AppError, error)

    def __call__(self, func):
        """作为装饰器使用"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                app_error = self.handle(e)
                if self.raise_after_handle:
                    raise app_error
                return None
        return wrapper


class WebErrorHandler(BaseErrorHandler):
    """Web错误处理器，用于HTTP API响应"""

    def __init__(self, include_details: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.include_details = include_details

    def handle(self, error: Exception) -> AppError:
        """处理异常并准备Web响应"""
        app_error = super().handle(error)
        return app_error

    def to_response(self, error: AppError) -> tuple:
        """转换为HTTP响应元组 (status_code, body, headers)"""
        status = error.info.status_code

        # 构建响应体
        body = error.to_dict()

        # 根据配置决定是否包含详细信息
        if not self.include_details:
            # 移除敏感信息
            if 'metadata' in body['error']['context']:
                del body['error']['context']['metadata']

        # 构建响应头
        headers = {
            "Content-Type": "application/json",
            "X-Error-Code": error.info.code,
            "X-Trace-ID": error.context.trace_id
        }

        return status, body, headers


class JsonErrorHandler(BaseErrorHandler):
    """JSON错误处理器，生成JSON格式错误"""

    def __init__(self, indent: Optional[int] = None, ensure_ascii: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.indent = indent
        self.ensure_ascii = ensure_ascii

    def to_json(self, error: AppError) -> str:
        """转换错误为JSON字符串"""
        return json.dumps(
            error.to_dict(),
            indent=self.indent,
            ensure_ascii=self.ensure_ascii
        )


class LogErrorHandler(BaseErrorHandler):
    """日志错误处理器，记录错误到日志系统"""

    def __init__(self,
                 logger: Optional[logging.Logger] = None,
                 include_traceback: bool = True,
                 log_level: int = logging.ERROR,
                 **kwargs):
        super().__init__(**kwargs)
        self.logger = logger or init_logger("errors/logging")
        self.include_traceback = include_traceback
        self.log_level = log_level

    def handle(self, error: Exception) -> AppError:
        """处理异常并记录到日志"""
        app_error = super().handle(error)

        # 构建日志消息
        message = f"[{app_error.info.code}] {app_error.message}"

        # 构建额外信息
        extra = {
            "error_code": app_error.info.code,
            "trace_id": app_error.context.trace_id,
            "category": app_error.info.category
        }

        # 决定是否包含traceback
        exc_info = None
        if self.include_traceback:
            if app_error.context.cause:
                exc_info = app_error.context.cause
            elif "traceback" in app_error.context.metadata:
                message += f"\n\nTraceback:\n{app_error.context.metadata['traceback']}"
            else:
                exc_info = True

        # 记录日志
        self.logger.log(self.log_level, message, extra=extra, exc_info=exc_info)

        return app_error
