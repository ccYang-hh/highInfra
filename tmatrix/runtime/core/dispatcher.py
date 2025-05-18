from typing import Dict, Optional, Callable, List
from fastapi import Request, Response, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

from tmatrix.components.logging import init_logger
from tmatrix.runtime.pipeline import Pipeline
from tmatrix.runtime.core.context import RequestContext, RequestState

logger = init_logger("runtime/core")


class RequestDispatcher:
    """
    请求分发器
    将请求路由到对应的Pipeline进行处理
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = RequestDispatcher()
        return cls._instance

    def __init__(self):
        """初始化请求分发器"""
        # 路由到Pipeline的映射 {route_path: pipeline_instance}
        self.route_mapping: Dict[str, Pipeline] = {}

        # 错误处理器
        self.error_handlers: Dict[int, Callable] = {}

        # 注册默认错误处理器
        self.register_error_handler(404, self._handle_not_found)
        self.register_error_handler(500, self._handle_server_error)

    def register_route(self, path: str, pipeline: Pipeline, methods: List[str] = None) -> None:
        """
        注册路由到Pipeline的映射

        Args:
            path: API路径
            pipeline: 处理该路径的Pipeline
            methods: 支持的HTTP方法（可选）
        """
        self.route_mapping[path] = pipeline
        methods_str = ", ".join(methods) if methods else "ALL"
        logger.info(f"注册路由: {path} [{methods_str}] -> {pipeline.name}")

    def register_error_handler(self, status_code: int, handler: Callable) -> None:
        """
        注册错误处理器

        Args:
            status_code: HTTP状态码
            handler: 处理函数
        """
        self.error_handlers[status_code] = handler
        logger.debug(f"注册错误处理器: {status_code}")

    def get_handler(self) -> Callable:
        """
        获取通用请求处理函数

        Returns:
            异步处理函数
        """

        async def handler(request: Request, background_tasks: BackgroundTasks = None):
            """通用请求处理函数"""
            return await self.dispatch(request, background_tasks)

        return handler

    async def dispatch(self, request: Request, background_tasks: Optional[BackgroundTasks] = None) -> Response:
        """
        分发请求到对应的Pipeline

        Args:
            request: FastAPI请求
            background_tasks: 后台任务（可选）

        Returns:
            HTTP响应
        """
        # 创建请求上下文
        context = await self._create_context(request)

        # 获取请求路径
        path = request.url.path

        # 查找对应的Pipeline
        pipeline = self.route_mapping.get(path)

        if not pipeline:
            logger.warning(f"未找到路径映射: {path}")
            return await self._handle_error(context, 404, f"未找到API: {path}")

        # 添加Pipeline信息到上下文
        context.set_extension("pipeline_name", pipeline.name)

        # 添加后台任务到上下文
        if background_tasks:
            context.set_extension("background_tasks", background_tasks)

        try:
            # 处理请求
            logger.debug(f"分发请求到Pipeline: {pipeline.name}")
            processed_context = await pipeline.process(context)

            # 创建响应
            return self._create_response(processed_context, background_tasks)

        except Exception as e:
            logger.exception(f"Pipeline处理请求出错: {e}")
            return await self._handle_error(context, 500, str(e))

    @staticmethod
    async def _create_context(request: Request) -> RequestContext:
        """
        从HTTP请求创建上下文

        Args:
            request: FastAPI请求

        Returns:
            请求上下文
        """
        # 从Request创建RequestContext实例
        context = await RequestContext.from_request(request)

        # 开始处理
        context.start_processing()
        context.set_state(RequestState.INITIALIZED)

        return context

    @staticmethod
    def _create_response(context: RequestContext, background_tasks: Optional[BackgroundTasks]) -> Response:
        """
        从上下文创建响应

        Args:
            context: 处理后的上下文
            background_tasks: 后台任务

        Returns:
            HTTP响应
        """
        # 处理后台任务
        if background_tasks and context.has_extension("background_tasks_list"):
            tasks = context.get_extension("background_tasks_list", [])
            for task_func, task_args in tasks:
                background_tasks.add_task(task_func, *task_args)

        # 检查是否有错误
        if context.state == RequestState.FAILED:
            status_code = context.get_extension("status_code", 500)
            return JSONResponse(
                status_code=status_code,
                content={"error": context.error_message or "Unknown error", "request_id": context.request_id}
            )

        # 处理流式响应
        if context.is_streaming and context.response_chunks:
            async def stream_generator():
                for chunk in context.response_chunks:
                    yield chunk

            return StreamingResponse(
                stream_generator(),
                media_type=context.response_headers.get("content-type", "application/json")
            )

        # 标准响应
        if context.final_response is not None:
            # 如果是字典，作为JSON返回
            if isinstance(context.final_response, dict):
                return JSONResponse(
                    content=context.final_response,
                    headers=context.response_headers
                )

            # 如果是字符串，作为文本返回
            if isinstance(context.final_response, str):
                from fastapi.responses import PlainTextResponse
                return PlainTextResponse(
                    content=context.final_response,
                    headers=context.response_headers
                )

            # 如果是字节，作为二进制返回
            if isinstance(context.final_response, bytes):
                from fastapi.responses import Response
                return Response(
                    content=context.final_response,
                    media_type=context.response_headers.get("content-type", "application/octet-stream"),
                    headers=context.response_headers
                )

        # 默认响应 - 如果上下文没有明确的响应内容
        return JSONResponse(
            content={"status": "success", "request_id": context.request_id},
            headers=context.response_headers
        )

    async def _handle_error(self, context: RequestContext, status_code: int, message: str) -> Response:
        """
        处理错误

        Args:
            context: 请求上下文
            status_code: HTTP状态码
            message: 错误消息

        Returns:
            错误响应
        """
        # 设置错误信息
        context.fail(message)
        context.set_extension("status_code", status_code)

        # 使用注册的错误处理器
        handler = self.error_handlers.get(status_code)
        if handler:
            return await handler(context, Exception(message))

        # 默认错误响应
        return JSONResponse(
            status_code=status_code,
            content={"error": message, "request_id": context.request_id}
        )

    @staticmethod
    async def _handle_not_found(context: RequestContext, exception: Exception) -> Response:
        """404错误处理"""
        return JSONResponse(
            status_code=404,
            content={
                "error": "Not Found",
                "message": str(exception),
                "request_id": context.request_id,
                "path": context.endpoint
            }
        )

    @staticmethod
    async def _handle_server_error(context: RequestContext, exception: Exception) -> Response:
        """500错误处理"""
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal Server Error",
                "message": str(exception),
                "request_id": context.request_id
            }
        )
