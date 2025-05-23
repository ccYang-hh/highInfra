from enum import Enum
from typing import Dict
from fastapi import APIRouter, FastAPI

from tmatrix.common.logging import init_logger
from tmatrix.runtime.pipeline import Pipeline

from .dispatcher import RequestDispatcher

logger = init_logger("runtime/core")


class RouteSource(Enum):
    SYSTEM = "system"
    PLUGIN = "plugin"
    PIPELINE = "pipeline"


class RouterManager:
    """
    路由管理器
    整合Pipeline和插件路由，简化路由注册和分发

    TODO
        1. 提供路由冲突策略（优先级 or 拒绝）
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

        # 系统路由器集合 {module_name: router}
        self.system_routes_count: int = 0
        self.system_routers: Dict[str, APIRouter] = {}

        # 插件提供的路由器
        self.plugin_routes_count: int = 0
        self.plugin_routers: Dict[str, APIRouter] = {}

        # 已注册的路由 {path: {method: pipeline_name}}
        self.registered_routes: Dict[str, Dict[str, str]] = {}

        # 已注册路由的源 {path: {method: source_type}}
        # source_type可以是 "system", "plugin", "pipeline"
        self.route_sources: Dict[str, Dict[str, RouteSource]] = {}

    def register_system_router(self, module_name: str, router: APIRouter, prefix: str = None) -> None:
        """
        注册系统级API路由器

        Args:
            module_name: 模块名称
            router: API路由器
            prefix: 路由前缀（可选），该值应该与具体的module路由前缀保持一致
        """
        if router is None:
            return

        # 重复的系统路由注册
        if module_name in self.system_routers:
            logger.info(f"当前系统路由已注册: {module_name}")
            return

        self.system_routers[module_name] = router
        self.system_routes_count += len(router.routes)
        for route in router.routes:
            logger.info(f"注册路由: {route.path} [{route.methods}] -> {module_name}")

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
        self.plugin_routes_count += len(router.routes)
        for route in router.routes:
            logger.info(f"注册路由: {route.path} [{route.methods}] -> {plugin_name}")

    def apply_to_app(self, app: FastAPI) -> None:
        """
        将所有路由应用到FastAPI应用
        应用顺序按优先级：系统路由 > 插件路由 > Pipeline路由

        TODO
            1.公用的Plugin需要注册多个Routes，prefix为: /{pipeline_name}/plugins/{plugin_name}

        Args:
            app: FastAPI应用实例
        """
        # 1. 应用系统路由（最高优先级）
        for module_name, router in self.system_routers.items():
            app.include_router(
                router,
                tags=[f"system:{module_name}", "source:system"]
            )

        # 2. 注册插件路由（中等优先级）
        for plugin_name, router in self.plugin_routers.items():
            app.include_router(
                router,
                prefix=f"/plugins/{plugin_name}",
                tags=[f"plugin:{plugin_name}", "source:plugin"]
            )

        # 3. 注册Pipeline路由（最低优先级）
        # 获取通用处理函数以处理Pipeline路由
        handler = self.dispatcher.get_handler()

        # 为每个注册的路由路径创建路由
        for path, methods_dict in self.registered_routes.items():
            methods = list(methods_dict.keys())
            app.add_api_route(
                path=path,
                endpoint=handler,
                methods=methods,
                response_model=None,  # 动态响应类型
                tags=["source:pipeline"]
            )

        logger.info(f"注册系统路由: {self.system_routes_count}, "
                    f"注册插件路由: {self.plugin_routes_count}, "
                    f"注册Pipeline路由: {len(self.registered_routes)}")

