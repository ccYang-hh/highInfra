import logging
from typing import Optional

from .registry import ErrorModule, ErrorRegistry
from .error_handler import ErrorHandler
from .adapters import LogOutputAdapter, WebOutputAdapter, JsonOutputAdapter


def setup_module_errors(module_name: str) -> ErrorModule:
    """便捷函数，用于在模块中设置错误定义"""
    registry = ErrorRegistry()
    return registry.get_module(module_name)


def create_log_handler(
        logger: Optional[logging.Logger] = None,
        include_traceback: bool = True
) -> ErrorHandler:
    handler = ErrorHandler()
    handler.add_adapter(LogOutputAdapter(logger, include_traceback))
    return handler


def create_web_handler() -> ErrorHandler:
    handler = ErrorHandler()
    handler.add_adapter(WebOutputAdapter())
    return handler


def create_json_handler() -> ErrorHandler:
    handler = ErrorHandler()
    handler.add_adapter(JsonOutputAdapter())
    return handler
