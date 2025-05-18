import os
import time
import json
import asyncio
from http import HTTPMethod
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Union, cast

from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from tmatrix.components.logging import init_logger
from tmatrix.runtime.pipeline import Pipeline, PipelineBuilder, PipelineBuilderConfig
from tmatrix.runtime.plugins import PluginManager
from tmatrix.runtime.config import get_config_manager, PipelineConfig, PipelineRoute
from tmatrix.runtime.utils import EventBus
from .context import RequestContext, RequestState
from .router import RouterManager

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
        # 基础组件
        self.event_bus = EventBus()

        # 加载配置
        self.config_manager = get_config_manager(config_path, self.event_bus)
        self.config = self.config_manager.get_config()

        # TODO 注册配置文件更新后的回调函数
        # self.config_manager.subscribe_async(self._on_config_change)

        # 创建或使用已有的FastAPI应用
        self.app = app or FastAPI(
            title=self.config.app_name,
            description=self.config.app_description,
            version=self.config.app_version,
            lifespan=self.lifespan,
        )

        # 添加中间件
        self._add_middlewares()

        # 创建组件
        self.router_manager = RouterManager.get_instance()
        self.plugin_manager: Optional[PluginManager] = None
        self.pipeline_builder: Optional[PipelineBuilder] = None

        # 运行状态
        self._is_initialized = False
        self._startup_time = None

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        await self.startup()
        yield
        await self.shutdown()

    async def startup(self) -> None:
        """启动运行时核心"""
        if self._is_initialized:
            logger.warning("运行时已经初始化")
            return

        logger.info(f"启动 {self.config.app_name} v{self.config.app_version}")
        self._startup_time = time.time()

        # 初始化插件管理器
        await self._init_plugin_manager()

        # 构建所有Pipeline
        self._build_pipelines()

        # 应用所有路由
        self._setup_routes()

        # 注册默认路由
        self._register_default_routes()

        # 标记为已初始化
        self._is_initialized = True

        # 注册全局state
        self.app.state.runtime = self  # type: ignore
        self.app.state.event_bus = self.event_bus  # type: ignore
        self.app.state.config_manager = self.config_manager  # type: ignore
        self.app.state.config = self.config  # type: ignore
        self.app.state.router_manager = self.router_manager  # type: ignore

        logger.info(f"{self.config.app_name} 初始化成功")

    async def shutdown(self) -> None:
        """关闭运行时核心"""
        if not self._is_initialized:
            return

        logger.info("关闭系统...")

        # 关闭插件
        if self.plugin_manager:
            await self.plugin_manager.shutdown()

        self._is_initialized = False
        logger.info("系统已关闭")

    def _add_middlewares(self) -> None:
        """配置FastAPI应用"""
        # 配置CORS
        from fastapi.middleware.cors import CORSMiddleware
        self.app.add_middleware(
            CORSMiddleware,  # type: ignore
            allow_origins=self.config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    async def _init_plugin_manager(self) -> None:
        """初始化插件管理器"""
        self.plugin_manager = PluginManager(self.config.plugin_registry, self.event_bus)

        # 设置事件循环
        self.plugin_manager.set_event_loop(asyncio.get_running_loop())

        # 初始化插件管理器
        await self.plugin_manager.initialize()

        # 启动所有已启用的插件
        await self.plugin_manager.start_plugins()

    def _build_pipelines(self) -> None:
        """构建所有Pipeline"""
        logger.info("构建Pipelines...")

        # 构建Pipeline配置
        pipeline_configs = self._create_pipeline_configs()

        # 创建Pipeline构建器
        self.pipeline_builder = PipelineBuilder(
            plugin_manager=self.plugin_manager,
            pipeline_build_config=pipeline_configs
        )

        # 构建所有Pipeline
        pipelines = self.pipeline_builder.build_pipelines()

        # 注册到路由管理器
        for name, pipeline in pipelines.items():
            self.router_manager.register_pipeline_routes(pipeline)

        logger.info(f"构建了 {len(pipelines)} 个Pipelines")

    def _create_pipeline_configs(self) -> PipelineBuilderConfig:
        """
        从配置创建Pipeline构建配置

        Returns:
            Pipeline构建配置
        """
        # 默认Pipeline
        default_pipeline = PipelineConfig(
            pipeline_name="default",
            plugins=["vllm_router", "request_stream_processor"],
            routes=[
                PipelineRoute(path="/v1/models", method=HTTPMethod.GET),
                PipelineRoute(path="/v1/embeddings", method=HTTPMethod.POST),
                PipelineRoute(path="/v1/completions", method=HTTPMethod.POST),
                PipelineRoute(path="/v1/chat/completions", method=HTTPMethod.POST),
            ]
        )

        return PipelineBuilderConfig(
            pipelines=self.config.pipelines,
            default=default_pipeline
        )

    def _setup_routes(self) -> None:
        """设置所有路由"""
        # 从插件获取路由器
        for plugin_name in self.plugin_manager.get_all_plugins().keys():
            router = self.plugin_manager.get_api_router(plugin_name)
            if router:
                self.router_manager.register_plugin_router(plugin_name, router)

        # 应用所有路由到FastAPI应用
        self.router_manager.apply_to_app(self.app)

    def _register_default_routes(self) -> None:
        """注册默认API路由"""

        # 添加健康检查端点
        @self.app.get("/health")
        async def health_check():
            uptime = time.time() - self._startup_time if self._startup_time else 0
            plugin_info = {}
            if self.plugin_manager:
                plugin_info = {
                    name: {
                        "state": self.plugin_manager.get_plugin_state(name).name,
                        "version": plugin.get_version() if plugin else None
                    }
                    for name, plugin in self.plugin_manager.get_all_plugins().items()
                }

            return {
                "status": "ok",
                "uptime": uptime,
                "plugins": plugin_info,
                "initialized": self._is_initialized,
                "pipelines": list(self.pipeline_builder.pipelines.keys()) if self.pipeline_builder else []
            }
