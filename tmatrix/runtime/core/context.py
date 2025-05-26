import uuid
import time
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union
from fastapi import Request
from fastapi.routing import APIRoute

from tmatrix.common.logging import init_logger
logger = init_logger("runtime/core")


class RequestState(str, Enum):
    """请求状态枚举，定义请求在处理过程中的各个阶段"""
    INITIALIZED = "initialized"  # 请求初始化完成
    PREPROCESSING = "preprocessing"  # 预处理阶段
    MODEL_RESOLUTION = "model_resolution"  # 模型识别与解析
    ROUTING = "routing"  # 路由选择
    CACHE_CHECK = "cache_check"  # 缓存检查
    REQUEST_REWRITING = "request_rewriting"  # 请求重写
    OPTIMIZING = "optimizing"  # 请求优化
    EXECUTING = "executing"  # 执行推理
    PROCESSING_RESPONSE = "processing_response"  # 处理响应
    COMPLETED = "completed"  # 请求完成
    FAILED = "failed"  # 请求失败


@dataclass
class RequestContext:
    """
    请求上下文类，用于在整个请求处理流程中传递信息

    作为处理管道中各阶段之间交流的媒介，包含请求的所有相关信息
    """
    # 基本标识信息
    request_id: str = field(default_factory=lambda: str)
    creation_time: float = field(default_factory=time.time)
    last_updated_time: float = field(default_factory=time.time)
    state: RequestState = RequestState.INITIALIZED

    # 请求元数据
    headers: Dict[str, str] = field(default_factory=dict)
    endpoint: Optional[str] = None
    http_method: Optional[str] = None
    query_params: Dict[str, str] = field(default_factory=dict)
    client_info: Dict[str, Any] = field(default_factory=dict)

    # 请求内容
    raw_body: bytes = field(default_factory=lambda: bytes())
    parsed_body: Dict[str, Any] = field(default_factory=dict)

    # 请求标识：first_time、history、rag
    request_type: str = field(default_factory=str)
    request_identifiers: Optional[Dict[str, str]] = field(default_factory=dict)

    # 模型信息
    model_name: Optional[str] = None
    sampling_parameters: Dict[str, Any] = field(default_factory=dict)
    is_streaming: bool = False

    # 路由信息
    backend_url: Optional[str] = None
    backend_info: Dict[str, Any] = field(default_factory=dict)
    available_backends: List[Dict[str, Any]] = field(default_factory=list)

    # 缓存信息
    cache_key: Optional[str] = None
    cache_hit: bool = False
    cache_metadata: Dict[str, Any] = field(default_factory=dict)

    # 响应信息
    status_code: Any = field(default_factory=str)
    response_started: bool = False
    response_headers: Dict[str, str] = field(default_factory=dict)
    response_chunks: List[bytes] = field(default_factory=list)
    final_response: Optional[Any] = None

    # 性能跟踪
    stage_timings: Dict[str, float] = field(default_factory=dict)
    stage_sequence: List[str] = field(default_factory=list)
    processing_start_time: Optional[float] = None
    processing_end_time: Optional[float] = None
    metrics: Optional[Dict[str, Any]] = None

    # 错误处理
    error: Optional[Exception] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None

    # 维护全局APP State实例
    app_state: Optional[Dict[str, Any]] = None
    routes: List[APIRoute] = field(default_factory=list)

    # 扩展数据 - 供插件使用的任意数据存储
    extensions: Dict[str, Any] = field(default_factory=dict)

    def start_processing(self) -> None:
        """标记请求处理开始"""
        self.processing_start_time = time.time()

    def end_processing(self) -> None:
        """标记请求处理结束"""
        self.processing_end_time = time.time()

    def get_processing_time(self) -> Optional[float]:
        """获取请求处理总时间"""
        if self.processing_start_time and self.processing_end_time:
            return self.processing_end_time - self.processing_start_time
        return None

    def set_state(self, state: RequestState) -> None:
        """
        更新请求状态，记录时间戳和阶段时长

        Args:
            state: 请求状态
        """
        current_time = time.time()
        duration = current_time - self.last_updated_time

        # 记录上一阶段的时长
        self.stage_timings[self.state] = duration
        self.stage_sequence.append(self.state)

        # 更新状态和时间戳
        self.state = state
        self.last_updated_time = current_time

        logger.debug(f"Request {self.request_id} state changed to {state} "
                     f"(previous stage took {duration:.4f}s)")

    def fail(self, error: Union[Exception, str], stack_trace: Optional[str] = None) -> None:
        """
        标记请求为失败状态

        Args:
            error: 错误对象或错误消息
            stack_trace: 可选的堆栈跟踪信息
        """
        if isinstance(error, Exception):
            self.error = error
            self.error_message = str(error)
        else:
            self.error_message = error

        self.stack_trace = stack_trace
        self.set_state(RequestState.FAILED)
        logger.error(f"Request {self.request_id} failed: {self.error_message}")

    def complete(self, response: Any = None) -> None:
        """
        标记请求为完成状态

        Args:
            response: 可选的最终响应对象
        """
        if response is not None:
            self.final_response = response

        self.set_state(RequestState.COMPLETED)
        self.end_processing()

        # 计算总处理时间
        total_time = self.get_processing_time()
        if total_time:
            logger.info(f"Request {self.request_id} completed in {total_time:.4f}s")

    def add_response_chunk(self, chunk: bytes) -> None:
        """
        添加响应数据块

        Args:
            chunk: 响应数据块
        """
        if not self.response_started:
            self.response_started = True
            logger.debug(f"Request {self.request_id} first response chunk received")

        self.response_chunks.append(chunk)

    def set_extension(self, key: str, value: Any) -> None:
        """
        设置扩展数据

        Args:
            key: 扩展数据键
            value: 扩展数据值
        """
        self.extensions[key] = value

    def get_extension(self, key: str, default: Any = None) -> Any:
        """
        获取扩展数据

        Args:
            key: 扩展数据键
            default: 默认值，当键不存在时返回

        Returns:
            扩展数据值或默认值
        """
        return self.extensions.get(key, default)

    def has_extension(self, key: str) -> bool:
        """
        检查扩展数据是否存在

        Args:
            key: 扩展数据键

        Returns:
            扩展数据是否存在
        """
        return key in self.extensions

    @classmethod
    async def from_request(cls, request: "Request") -> "RequestContext":
        """
        从 FastAPI 请求创建一个请求上下文

        Args:
            request: FastAPI 请求对象

        Returns:
            初始化好的请求上下文
        """
        # 创建基础 context 实例
        context = cls(
            request_id=str(uuid.uuid4()),
            creation_time=time.time(),
            last_updated_time=time.time(),
            state=RequestState.INITIALIZED,

            # 请求元数据
            headers={k: v for k, v in request.headers.items()},
            endpoint=str(request.url.path),
            http_method=request.method,
            query_params=dict(request.query_params),
            client_info={
                "host": request.client.host if request.client else None,
                "port": request.client.port if request.client else None,
                "user_agent": request.headers.get("user-agent"),
                "forwarded_for": request.headers.get("x-forwarded-for"),
                "request_id": request.headers.get("x-request-id"),
            },
        )

        # 保存对应用状态的引用
        context.app_state = request.app.state
        context.routes = request.app.routes

        # 处理请求体 (如果有)
        context = await cls._parse_request_body(request, context)

        return context

    @classmethod
    async def _parse_request_body(cls, request: "Request", context: "RequestContext") -> "RequestContext":
        # 处理请求体 (如果有)
        try:
            # 对于较小的请求体，直接读取
            raw_body = await request.body()
            context.raw_body = raw_body

            # 尝试解析为JSON (如果适用)
            if request.headers.get("content-type", "").lower().startswith("application/json"):
                try:
                    body_json = await request.json()
                    context.parsed_body = body_json

                    # 提取常见的模型参数 (例如 model_name, streaming 等)
                    if isinstance(body_json, dict):
                        if "model" in body_json:
                            context.model_name = body_json["model"]

                        # 可能的流式响应标志
                        if "stream" in body_json:
                            context.is_streaming = bool(body_json["stream"])

                        # 模型参数收集
                        for param in ["temperature", "top_p", "max_tokens", "n", "presence_penalty",
                                      "frequency_penalty", "top_k", "seed"]:
                            if param in body_json:
                                context.sampling_parameters[param] = body_json[param]
                except ValueError:
                    # 不是有效的JSON，保持默认值
                    pass
        except Exception as e:
            logger.warning(f"Error reading request body: {str(e)}")

        return context

    def to_dict(self) -> Dict[str, Any]:
        """
        将上下文转换为字典，主要用于日志记录和调试

        Returns:
            上下文的字典表示
        """
        return asdict(self)

    def __str__(self) -> str:
        """字符串表示，用于日志和调试"""
        return f"RequestContext(id={self.request_id}, model={self.model_name}, state={self.state})"
    