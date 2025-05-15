import os
import time
import json
import yaml
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union, Type, Callable

from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from tmatrix.components.logging import init_logger
from tmatrix.runtime.config import get_config_manager
from tmatrix.runtime.context import RequestContext, RequestState
from tmatrix.runtime.pipeline import Pipeline, PipelineStage, PipelineHook, StageHook
from tmatrix.runtime.plugins import Plugin, PluginManager

logger = init_logger("runtime/core")


class RuntimeCore:
    """
    运行时核心

    负责协调系统的所有组件，包括插件、管道、API等
    """

    def __init__(self,
                 config_path: Optional[str] = None,
                 app: Optional[FastAPI] = None):
        """
        初始化运行时核心

        Args:
            config_path: 配置文件路径，默认为"config.json"
            app: FastAPI应用实例，如果不提供则创建新的
        """
        # 加载配置
        self.config_manager = get_config_manager(config_path)

        # 创建或使用已有的FastAPI应用
        self.app = app or FastAPI(
            title=self.config.get("app_name", "LLM Inference Platform"),
            description=self.config.get("app_description", "Advanced LLM Inference API"),
            version=self.config.get("app_version", "1.0.0")
        )

        # 初始化组件
        self.plugin_manager = PluginManager()
        self.pipelines: Dict[str, Pipeline] = {}

        # 运行状态
        self._is_initialized = False
        self._startup_time = None
        self._request_count = 0

    def _configure_app(self) -> None:
        """配置FastAPI应用"""
        # 添加CORS中间件
        origins = self.config.get("cors_origins", ["*"])
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 添加静态文件服务
        static_path = self.config.get("static_path")
        if static_path and os.path.exists(static_path):
            self.app.mount("/static", StaticFiles(directory=static_path), name="static")

            # 注册默认路由
        self._register_default_routes()

    def _register_default_routes(self) -> None:
        """注册默认API路由"""

        @self.app.get("/health")
        async def health_check():
            """健康检查端点"""
            return {
                "status": "ok",
                "uptime": time.time() - self._startup_time if self._startup_time else 0,
                "request_count": self._request_count,
                "initialized": self._is_initialized
            }

        @self.app.get("/plugins")
        async def list_plugins():
            """列出所有已加载的插件"""
            plugins = []
            for name, plugin in self.plugin_manager.get_initialized_plugins().items():
                plugins.append({
                    "name": name,
                    "version": plugin.get_version(),
                    "description": plugin.get_description()
                })
            return {"plugins": plugins}

        @self.app.get("/pipelines")
        async def list_pipelines():
            """列出所有可用的管道"""
            pipelines = []
            for name, pipeline in self.pipelines.items():
                stages = [stage.name for stage in pipeline.stages.values()]
                pipelines.append({
                    "name": name,
                    "stages": stages,
                    "entry_points": pipeline.entry_stages,
                    "exit_points": pipeline.exit_stages
                })
            return {"pipelines": pipelines}

    def _register_plugin_routes(self) -> None:
        """注册插件提供的API路由"""
        routers = self.plugin_manager.get_all_api_routers()
        for plugin_name, router in routers.items():
            # 添加前缀以防止冲突
            self.app.include_router(
                router,
                prefix=f"/plugins/{plugin_name}",
                tags=[plugin_name]
            )
            logger.info(f"Registered API router for plugin: {plugin_name}")

    async def _create_pipelines(self) -> None:
        """创建处理管道"""
        # 从配置加载管道定义
        pipeline_configs = self.config.get("pipelines", {})

        # 如果没有配置，创建默认的主管道
        if not pipeline_configs:
            pipeline_configs = {
                "main": {
                    "max_parallelism": 4
                }
            }

            # 创建每个管道
        for name, config in pipeline_configs.items():
            pipeline = Pipeline(
                name=name,
                max_parallelism=config.get("max_parallelism", 4),
                error_handler=self._pipeline_error_handler
            )

            # 将管道添加到映射
            self.pipelines[name] = pipeline
            logger.info(f"Created pipeline: {name}")

            # 获取所有阶段
        stages = self.plugin_manager.get_all_pipeline_stages()

        # 为每个管道添加阶段
        for name, pipeline in self.pipelines.items():
            # 对于特定管道的阶段，只添加名称匹配的
            pipeline_stages = [s for s in stages if not hasattr(s, "pipeline") or getattr(s, "pipeline") == name]

            for stage in pipeline_stages:
                pipeline.add_stage(stage)
                logger.debug(f"Added stage {stage.name} to pipeline {name}")

                # 让插件配置管道
        self.plugin_manager.configure_pipelines(self.pipelines)

        # 添加所有钩子
        pipeline_hooks = self.plugin_manager.get_all_pipeline_hooks()
        stage_hooks = self.plugin_manager.get_all_stage_hooks()

        # 添加管道钩子
        for hook in pipeline_hooks:
            for pipeline in self.pipelines.values():
                pipeline.add_hook(hook)

                # 添加阶段钩子
        for hook in stage_hooks:
            # 可以根据属性决定哪些阶段应该添加钩子
            if hasattr(hook, "target_stages"):
                target_stages = getattr(hook, "target_stages")
                for pipeline in self.pipelines.values():
                    for stage_name in target_stages:
                        if stage_name in pipeline.stages:
                            pipeline.stages[stage_name].add_hook(hook)
            else:
                # 默认添加到所有阶段
                for pipeline in self.pipelines.values():
                    for stage in pipeline.stages.values():
                        stage.add_hook(hook)

                        # 验证所有管道
        for pipeline in self.pipelines.values():
            try:
                pipeline.validate()
                logger.info(f"Validated pipeline: {pipeline.name}")
            except ValueError as e:
                logger.error(f"Pipeline validation failed for {pipeline.name}: {e}")

    def _pipeline_error_handler(self, context: RequestContext, stage_name: str, error: Exception) -> None:
        """
        管道错误处理器

        Args:
            context: 请求上下文
            stage_name: 发生错误的阶段
            error: 错误对象
        """
        logger.error(f"Error in stage {stage_name}: {error}", exc_info=True)
        context.fail(error)

    async def _load_plugins(self) -> None:
        """加载插件"""
        # 从配置加载插件模块
        plugin_modules = self.config.get("plugin_modules", [])
        if plugin_modules:
            await self.plugin_manager.load_plugins_from_modules(plugin_modules)

            # 自动发现插件
        plugin_discovery_paths = self.config.get("plugin_discovery_paths", [])
        for path in plugin_discovery_paths:
            await self.plugin_manager.discover_plugins(path)

            # 初始化插件
        plugin_configs = self.config.get("plugins", {})
        await self.plugin_manager.initialize_plugins(plugin_configs)

        # 检查是否加载了任何插件
        if not self.plugin_manager.initialized_plugins:
            logger.warning("No plugins were successfully initialized!")

    async def startup(self) -> None:
        """启动运行时核心"""
        if self._is_initialized:
            logger.warning("Runtime core is already initialized")
            return

        logger.info("Starting LLM Inference Platform")
        self._startup_time = time.time()

        # 配置应用
        self._configure_app()

        # 加载插件
        await self._load_plugins()

        # 创建管道
        await self._create_pipelines()

        # 注册插件路由
        self._register_plugin_routes()

        # 标记为已初始化
        self._is_initialized = True
        logger.info("LLM Inference Platform initialized successfully")

    async def shutdown(self) -> None:
        """关闭运行时核心"""
        if not self._is_initialized:
            logger.warning("Runtime core is not initialized")
            return

        logger.info("Shutting down LLM Inference Platform")

        # 关闭所有插件
        await self.plugin_manager.shutdown_plugins()

        # 清理资源
        self.pipelines.clear()

        # 标记为未初始化
        self._is_initialized = False
        logger.info("LLM Inference Platform shutdown complete")

    async def process_request(self,
                              request: Union[Request, Dict[str, Any], bytes],
                              pipeline_name: str = "main") -> RequestContext:
        """
        处理请求

        Args:
            request: 请求对象、字典或原始数据
            pipeline_name: 要使用的管道名称

        Returns:
            处理后的请求上下文

        Raises:
            ValueError: 如果管道不存在
            RuntimeError: 如果运行时未初始化
        """
        if not self._is_initialized:
            raise RuntimeError("Runtime core is not initialized")

        if pipeline_name not in self.pipelines:
            raise ValueError(f"Pipeline '{pipeline_name}' not found")

        # 统计请求
        self._request_count += 1

        # 创建上下文
        context = await self._create_context(request)

        # 使用选定的管道处理请求
        pipeline = self.pipelines[pipeline_name]
        logger.debug(f"Processing request {context.request_id} with pipeline {pipeline_name}")

        try:
            await pipeline.process(context)
        except Exception as e:
            logger.error(f"Unhandled error processing request: {e}", exc_info=True)
            context.fail(e)

        return context

    async def _create_context(self, request: Union[Request, Dict[str, Any], bytes]) -> RequestContext:
        """
        从请求创建上下文

        Args:
            request: 请求对象、字典或原始数据

        Returns:
            请求上下文
        """
        context = RequestContext()
        context.start_processing()

        # 处理不同类型的请求
        if isinstance(request, Request):
            # FastAPI请求
            context.http_method = request.method
            context.headers = dict(request.headers)
            context.query_params = dict(request.query_params)
            context.endpoint = request.url.path

            # 读取请求体
            body = await request.body()
            context.raw_body = body

            # 尝试解析为JSON
            try:
                if body:
                    context.parsed_body = json.loads(body)

                    # 提取常见字段
                    if "model" in context.parsed_body:
                        context.model_name = context.parsed_body["model"]
                    if "stream" in context.parsed_body:
                        context.is_streaming = context.parsed_body["stream"]
            except json.JSONDecodeError:
                # 非JSON请求
                pass

        elif isinstance(request, dict):
            # 直接使用字典
            context.parsed_body = request

            # 提取常见字段
            if "model" in request:
                context.model_name = request["model"]
            if "stream" in request:
                context.is_streaming = request["stream"]

        elif isinstance(request, bytes):
            # 原始字节
            context.raw_body = request

            # 尝试解析为JSON
            try:
                context.parsed_body = json.loads(request)

                # 提取常见字段
                if "model" in context.parsed_body:
                    context.model_name = context.parsed_body["model"]
                if "stream" in context.parsed_body:
                    context.is_streaming = context.parsed_body["stream"]
            except json.JSONDecodeError:
                # 非JSON请求
                pass

        return context

    def get_api_handler(self, pipeline_name: str = "main"):
        """
        获取API处理函数

        用于创建FastAPI路由处理函数

        Args:
            pipeline_name: 要使用的管道名称

        Returns:
            异步处理函数
        """

        async def handler(request: Request, background_tasks: BackgroundTasks = None):
            # 处理请求
            context = await self.process_request(request, pipeline_name)

            # 构建响应
            return self._create_response(context, background_tasks)

        return handler

    def _create_response(self,
                         context: RequestContext,
                         background_tasks: Optional[BackgroundTasks] = None) -> Response:
        """
        从上下文创建响应

        Args:
            context: 请求上下文
            background_tasks: 可选的后台任务

        Returns:
            FastAPI响应
        """
        # 如果请求失败，返回错误响应
        if context.state == RequestState.FAILED:
            status_code = 500

            # 根据错误消息确定状态码
            if context.error_message:
                if "not found" in context.error_message.lower():
                    status_code = 404
                elif "invalid" in context.error_message.lower():
                    status_code = 400
                elif "unauthorized" in context.error_message.lower():
                    status_code = 401

            return JSONResponse(
                status_code=status_code,
                content={"error": context.error_message or "Unknown error"}
            )

        # 处理流式响应
        if context.is_streaming and context.response_chunks:
            async def stream_generator():
                for chunk in context.response_chunks:
                    yield chunk

            return StreamingResponse(
                stream_generator(),
                media_type=context.response_headers.get("content-type", "text/event-stream")
            )

        # 处理常规响应
        if context.final_response is not None:
            if isinstance(context.final_response, dict):
                return JSONResponse(content=context.final_response)
            elif isinstance(context.final_response, bytes):
                return Response(
                    content=context.final_response,
                    media_type=context.response_headers.get("content-type", "application/octet-stream")
                )
            elif isinstance(context.final_response, str):
                return Response(
                    content=context.final_response,
                    media_type=context.response_headers.get("content-type", "text/plain")
                )

        # 如果没有显式响应但有响应块
        if context.response_chunks:
            content = b"".join(context.response_chunks)

            # 尝试解析为JSON
            try:
                json_content = json.loads(content)
                return JSONResponse(content=json_content)
            except:
                # 返回原始内容
                return Response(
                    content=content,
                    media_type=context.response_headers.get("content-type", "application/octet-stream")
                )

        # 默认成功响应
        return JSONResponse(
            content={"status": "ok", "request_id": context.request_id}
        )
