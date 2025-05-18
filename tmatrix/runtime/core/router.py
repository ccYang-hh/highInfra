from typing import Dict
from fastapi import APIRouter, FastAPI

from tmatrix.components.logging import init_logger
from tmatrix.runtime.pipeline import Pipeline

from .dispatcher import RequestDispatcher

logger = init_logger("runtime/core")


class RouterManager:
    """
    路由管理器
    整合Pipeline和插件路由，简化路由注册和分发
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = RouterManager()
        return cls._instance

    def __init__(self):
        """初始化路由管理器"""
        self.dispatcher = RequestDispatcher.get_instance()

        # 插件提供的路由器
        self.plugin_routers: Dict[str, APIRouter] = {}

        # 已注册的路由 {path: {method: pipeline_name}}
        self.registered_routes: Dict[str, Dict[str, str]] = {}

    def register_pipeline_routes(self, pipeline: Pipeline) -> None:
        """
        注册Pipeline的所有路由

        Args:
            pipeline: Pipeline实例
        """
        if not pipeline.routes:
            logger.debug(f"Pipeline没有定义路由: {pipeline.name}")
            return

        for route in pipeline.routes:
            path = route.path
            method = route.method

            # 注册到分发器
            self.dispatcher.register_route(path, pipeline, [method])

            # 记录注册信息
            if path not in self.registered_routes:
                self.registered_routes[path] = {}
            self.registered_routes[path][method] = pipeline.name

        logger.info(f"注册了Pipeline '{pipeline.name}'的{len(pipeline.routes)}个路由")

    def register_plugin_router(self, plugin_name: str, router: APIRouter) -> None:
        """
        注册插件提供的路由器

        Args:
            plugin_name: 插件名称
            router: API路由器
        """
        if not router:
            return

        # 重复的Plugin注册
        if self.plugin_routers.get(plugin_name) is not None:
            logger.info(f"当前插件路由已注册: {plugin_name}")
            return

        self.plugin_routers[plugin_name] = router
        logger.info(f"注册插件路由: {plugin_name}")

    def apply_to_app(self, app: FastAPI) -> None:
        """
        将所有路由应用到FastAPI应用

        TODO
            1.公用的Plugin需要注册多个Routes，prefix为: /{pipeline_name}/plugins/{plugin_name}

        Args:
            app: FastAPI应用实例
        """
        # 注册插件路由
        for plugin_name, router in self.plugin_routers.items():
            app.include_router(
                router,
                prefix=f"/plugins/{plugin_name}",
                tags=[plugin_name]
            )

        # 注册通用处理函数以处理Pipeline路由
        handler = self.dispatcher.get_handler()

        # 为每个注册的路由路径创建路由
        for path, methods_dict in self.registered_routes.items():
            methods = list(methods_dict.keys())
            app.add_api_route(
                path=path,
                endpoint=handler,
                methods=methods,
                response_model=None,  # 动态响应类型
                tags=["api"]
            )

        logger.info(f"应用了{len(self.registered_routes)}个Pipeline路由和{len(self.plugin_routers)}个插件路由")
