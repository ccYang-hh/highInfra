import json
import logging
from typing import Any, Optional, Protocol, runtime_checkable

from .base import BaseError


@runtime_checkable
class ErrorOutputAdapter(Protocol):
    """错误输出适配器协议，定义错误输出的接口"""

    def output(self, error: BaseError) -> Any:
        """输出错误，返回值由具体适配器决定"""
        ...


@runtime_checkable
class AsyncErrorOutputAdapter(Protocol):
    """异步错误输出适配器协议"""

    async def async_output(self, error: BaseError) -> Any:
        """异步输出错误，返回值由具体适配器决定"""
        ...


class JsonOutputAdapter:
    """JSON格式错误输出适配器"""

    def __init__(self, include_details: bool = True, indent: Optional[int] = None):
        self.include_details = include_details
        self.indent = indent

    def output(self, error: BaseError) -> str:
        """将错误输出为JSON字符串"""
        # 获取错误字典表示
        error_dict = error.to_dict()

        # 根据配置决定是否包含详细信息
        if not self.include_details:
            # 移除敏感或详细信息
            context = error_dict["error"]["context"]

            # 仅保留基本信息
            minimal_context = {
                "trace_id": context.get("trace_id")
            }
            error_dict["error"]["context"] = minimal_context

        # 转换为JSON字符串
        return json.dumps(error_dict, indent=self.indent, ensure_ascii=False)


class WebOutputAdapter:
    """Web响应适配器，输出适合Web框架的响应格式"""

    def __init__(self, include_details: bool = False):
        self.include_details = include_details
        self.json_adapter = JsonOutputAdapter(include_details=include_details, indent=None)

    def output(self, error: BaseError) -> tuple:
        """将错误输出为Web响应元组 (status_code, body, headers)"""
        # 获取状态码
        status_code = error.error_def.status_code

        # 使用JSON适配器获取响应体
        body_str = self.json_adapter.output(error)
        body = json.loads(body_str)

        # 构建HTTP响应头
        headers = {
            "Content-Type": "application/json",
            "X-Error-Code": error.error_def.code,
            "X-Trace-ID": error.context.trace_id
        }

        return status_code, body, headers


class LogOutputAdapter:
    """日志输出适配器，将错误写入日志系统"""

    def __init__(
            self,
            logger: Optional[logging.Logger] = None,
            include_traceback: bool = True
    ):
        self.logger = logger or logging.getLogger("errors_with_log")
        self.include_traceback = include_traceback

    def output(self, error: BaseError) -> None:
        """将错误输出到日志"""
        # 构建日志消息
        message = f"[{error.error_def.code}] {error.message}"

        # 添加上下文信息
        extra = {
            "error_code": error.error_def.code,
            "trace_id": error.context.trace_id,
            "error_category": error.error_def.category.value
        }

        # 记录到日志系统
        exc_info = None
        if self.include_traceback:
            # 尝试获取原始异常的traceback
            if "traceback" in error.context.metadata:
                # 作为消息的一部分记录traceback
                message += f"\n\nTraceback:\n{error.context.metadata['traceback']}"
            else:
                # 使用当前异常的traceback
                exc_info = True

        self.logger.error(message, extra=extra, exc_info=exc_info)
        return None
