import time
from typing import TypeVar, List, Optional, AsyncGenerator

from tmatrix.common.logging import init_logger
from tmatrix.runtime.utils import HTTPXClientWrapper
from tmatrix.runtime.pipeline import PipelineStage
from tmatrix.runtime.plugins import Plugin
from tmatrix.runtime.core import RequestContext, RequestState

logger = init_logger("plugins/request_processor")
T = TypeVar('T', bound='IPluginConfig')


class StreamProcessorStage(PipelineStage):
    """处理流式请求并生成响应的阶段"""

    def __init__(self, stage_name: str):
        """初始化流处理阶段"""
        super().__init__(stage_name)
        self.httpx_client_wrapper: Optional[HTTPXClientWrapper] = HTTPXClientWrapper()
        self.httpx_client_wrapper.start()

    async def _on_initialize(self) -> None:
        """初始化HTTPX客户端"""
        if not self.httpx_client_wrapper:
            self.httpx_client_wrapper = HTTPXClientWrapper()
            self.httpx_client_wrapper.start()

    async def _on_shutdown(self) -> None:
        """关闭HTTPX客户端"""
        if self.httpx_client_wrapper is not None:
            await self.httpx_client_wrapper.stop()

    async def process_request(
            self, context: RequestContext
    ) -> AsyncGenerator[bytes, None]:
        """
        处理请求并生成响应流

        Args:
            context: 包含请求信息的上下文对象

        Yields:
            首先yield (headers, status_code)，然后yield响应内容块
        """
        request_id = context.request_id
        backend_url = context.backend_url
        endpoint = context.endpoint
        body = context.raw_body
        headers = context.headers

        # 记录请求开始
        start_time = time.time()
        first_token = False
        total_len = 0
        full_response = bytearray() if not context.is_streaming else None

        async with self.httpx_client_wrapper().stream(
                method=context.http_method or "POST",
                url=f"{backend_url}{endpoint}",
                headers=dict(headers),
                content=body,
                timeout=None,
        ) as backend_response:
            # 首先yield headers和status_code
            yield backend_response.headers, backend_response.status_code

            # 在context中保存响应头和状态码
            context.response_headers = dict(backend_response.headers)
            context.status_code = backend_response.status_code

            # 流式处理响应内容
            async for chunk in backend_response.aiter_bytes():
                total_len += len(chunk)

                # 记录首个令牌接收时间
                if not first_token:
                    first_token = True
                    context.extensions["first_token_time"] = time.time()

                # 对于非流式请求，收集完整响应
                if full_response is not None:
                    full_response.extend(chunk)

                yield chunk

        # 请求完成
        context.processing_end_time = time.time()

        # 保存完整响应（如果是非流式请求）
        if full_response is not None:
            context.final_response = bytes(full_response)

    async def process(self, context: RequestContext) -> None:
        """处理请求上下文，创建流生成器并获取headers"""
        if not context.backend_url:
            context.fail("No backend URL specified")
            return

        context.set_state(RequestState.PROCESSING_RESPONSE)

        try:
            # 创建流生成器
            stream_generator = self.process_request(context)

            # 获取第一个值（headers和status_code）
            headers, status_code = await anext(stream_generator)

            # 保存到上下文
            context.response_headers = dict(headers)
            context.status_code = status_code

            # 将流生成器保存到上下文
            context.extensions["stream_generator"] = stream_generator

            context.set_state(RequestState.COMPLETED)
        except Exception as e:
            context.error = e
            context.error_message = str(e)
            context.state = RequestState.FAILED


class StreamProcessorPlugin(Plugin):
    """处理流式请求并生成响应的插件"""
    plugin_name: str = "request_processor"
    plugin_version: str = "0.0.1"

    def __init__(self):
        super().__init__()
        self.stream_processor_stage: Optional[PipelineStage] = StreamProcessorStage("stream_processor")

    async def _on_initialize(self) -> None:
        """初始化插件，创建HTTP客户端"""
        await self.stream_processor_stage.initialize()
        logger.info("Stream processor plugin initialized")

    async def _on_shutdown(self) -> None:
        """关闭插件，释放HTTP客户端"""
        await self.stream_processor_stage.shutdown()
        logger.info("Stream processor plugin shutdown")

    def get_pipeline_stages(self) -> List[PipelineStage]:
        """获取管道阶段"""
        return [self.stream_processor_stage]

    # def get_api_router(self) -> APIRouter:
    #     """获取API路由器"""
    #     router = APIRouter()
    #
    #     @router.post("/stream_response")
    #     async def create_streaming_response(
    #             request: Request,
    #             background_tasks: BackgroundTasks
    #     ):
    #         # 从请求状态获取上下文
    #         context = request.state.context
    #
    #         # 获取流生成器
    #         stream_generator = context.extensions.get("stream_generator")
    #         if not stream_generator:
    #             return JSONResponse(
    #                 status_code=500,
    #                 content={"error": "Stream generator not available"}
    #             )
    #
    #         # 创建并返回流式响应
    #         return StreamingResponse(
    #             stream_generator,
    #             status_code=200,
    #             headers=context.response_headers,
    #             media_type="text/event-stream" if context.is_streaming else "application/json"
    #         )
    #
    #     return router
