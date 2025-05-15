import os
import asyncio
import importlib
import pkgutil
import inspect
from fastapi import APIRouter
from typing import Any, Dict, List, Optional, Set, TypeVar

from tmatrix.components.logging import init_logger
from ..pipeline.stages import PipelineStage
from ..pipeline.hooks import PipelineHook, StageHook
from ..pipeline.pipeline import Pipeline
from .interface import Plugin

logger = init_logger("runtime/plugins")

P = TypeVar('P', bound=Plugin)


class PluginManager:
    """
    插件管理器

    负责插件的加载、初始化、关闭和交互
    """

    def __init__(self, config_dir: Optional[str] = None):
        """初始化插件管理器"""
        self.plugins: Dict[str, Plugin] = {}
        self.initialized_plugins: Set[str] = set()
        self.extension_points: Dict[str, Dict[str, Any]] = {}
        self.plugin_routers: Dict[str, APIRouter] = {}
        self.config_dir = config_dir or os.path.join("config", "plugins")

        # 确保插件配置目录存在
        os.makedirs(self.config_dir, exist_ok=True)

    async def register_plugin(self, plugin: Plugin) -> None:
        """
        注册插件

        Args:
            plugin: 插件实例

        Raises:
            ValueError: 如果插件已注册
        """
        name = plugin.get_name()
        if name in self.plugins:
            raise ValueError(f"Plugin '{name}' is already registered")

        self.plugins[name] = plugin
        logger.info(f"Registered plugin: {name} v{plugin.get_version()}")

    async def load_plugins_from_modules(self, module_names: List[str]) -> List[str]:
        """
        从模块加载插件

        Args:
            module_names: 模块名称列表

        Returns:
            加载的插件名称列表
        """
        loaded_plugins = []

        for module_name in module_names:
            try:
                # 导入模块
                module = importlib.import_module(module_name)

                # 查找插件类
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and
                            issubclass(obj, Plugin) and
                            obj != Plugin and
                            not inspect.isabstract(obj)):

                        # 创建插件实例
                        plugin = obj()
                        await self.register_plugin(plugin)
                        loaded_plugins.append(plugin.get_name())

            except Exception as e:
                logger.error(f"Error loading plugins from module {module_name}: {e}")

        return loaded_plugins

    async def discover_plugins(self, package_name: str) -> List[str]:
        """
        在包中发现并加载插件

        Args:
            package_name: 包名称

        Returns:
            加载的插件名称列表
        """
        loaded_plugins = []

        try:
            package = importlib.import_module(package_name)

            for _, name, is_pkg in pkgutil.iter_modules(package.__path__, package.__name__ + '.'):
                if is_pkg:
                    # 递归加载子包
                    sub_plugins = await self.discover_plugins(name)
                    loaded_plugins.extend(sub_plugins)
                else:
                    # 加载模块
                    module_plugins = await self.load_plugins_from_modules([name])
                    loaded_plugins.extend(module_plugins)

        except Exception as e:
            logger.error(f"Error discovering plugins in package {package_name}: {e}")

        return loaded_plugins

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """
        获取插件实例

        Args:
            name: 插件名称

        Returns:
            插件实例或None
        """
        return self.plugins.get(name)

    def get_all_plugins(self) -> Dict[str, Plugin]:
        """
        获取所有插件

        Returns:
            插件字典，键为插件名称，值为插件实例
        """
        return self.plugins.copy()

    def get_initialized_plugins(self) -> Dict[str, Plugin]:
        """
        获取所有已初始化的插件

        Returns:
            插件字典，键为插件名称，值为插件实例
        """
        return {name: self.plugins[name] for name in self.initialized_plugins}

    def _resolve_plugin_order(self) -> List[str]:
        """
        解析插件初始化顺序

        Returns:
            按依赖顺序排序的插件名称列表
        """
        # 创建依赖图
        graph = {}
        for name, plugin in self.plugins.items():
            graph[name] = plugin.get_dependencies()

        # 检查依赖是否存在
        for name, deps in graph.items():
            for dep in deps:
                if dep not in self.plugins:
                    raise ValueError(f"Plugin '{name}' depends on '{dep}' which is not registered")

        # 拓扑排序
        result = []
        visited = set()
        temp_visited = set()

        def visit(name):
            if name in temp_visited:
                raise ValueError(f"Circular dependency detected for plugin '{name}'")

            if name in visited:
                return

            temp_visited.add(name)

            for dep in graph[name]:
                visit(dep)

            temp_visited.remove(name)
            visited.add(name)
            result.append(name)

        # 访问所有节点
        for name in graph:
            if name not in visited:
                visit(name)

        return result

    async def initialize_plugins(self, bootstrap_configs: Dict[str, Dict[str, Any]] = None) -> None:
        """
        初始化所有已注册的插件

        Args:
            bootstrap_configs: 引导配置，包含必要的初始化参数
        """
        bootstrap_configs = bootstrap_configs or {}

        # 解析插件顺序
        plugin_order = self._resolve_plugin_order()

        # 按顺序初始化
        for name in plugin_order:
            plugin = self.plugins[name]

            try:
                logger.info(f"Initializing plugin: {name}")

                # 创建插件配置实例
                plugin_config = plugin.create_config(self.config_dir)

                # 使用引导配置初始化插件
                bootstrap_config = bootstrap_configs.get(name, {})
                await plugin.initialize(bootstrap_config)

                # 如果有配置实例，开始监视配置文件
                if plugin_config:
                    # 添加配置变更监听器
                    def listener(key, value, p=plugin):
                        # 捕获异常并创建协程任务
                        try:
                            asyncio.create_task(self._on_plugin_config_change(p, key, value))
                        except RuntimeError as e:
                            # 事件循环未开启（如主线程不是async环境），可按需警告或处理
                            logger.error(f"No running event loop: {e}")
                    plugin_config.add_change_listener(listener)
                    # 开始监视配置
                    plugin_config.start_watching()

                self.initialized_plugins.add(name)

                # 注册扩展点
                for ext_name, ext_obj in plugin.get_extension_points().items():
                    if ext_name not in self.extension_points:
                        self.extension_points[ext_name] = {}
                    self.extension_points[ext_name][name] = ext_obj

                # 获取API路由
                if router := plugin.get_api_router():
                    self.plugin_routers[name] = router

            except Exception as e:
                logger.error(f"Failed to initialize plugin '{name}': {e}", exc_info=True)

    async def _on_plugin_config_change(self, plugin: Plugin, key: str, value: Any) -> None:
        """插件配置变更处理"""
        try:
            # 获取插件完整配置
            config_instance = plugin.create_config(self.config_dir)
            if config_instance:
                # 通知插件配置已更新
                await plugin.update_config(config_instance.config)
        except Exception as e:
            logger.error(f"Error handling plugin config change: {e}")

    async def shutdown_plugins(self) -> None:
        """关闭所有已初始化的插件"""
        # 按照初始化的相反顺序关闭
        for name in reversed(list(self.initialized_plugins)):
            try:
                plugin = self.plugins[name]

                # 停止配置监视
                config_instance = plugin.create_config(self.config_dir)
                if config_instance:
                    config_instance.stop_watching()

                logger.info(f"Shutting down plugin: {name}")
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down plugin '{name}': {e}")

    def get_all_pipeline_stages(self) -> List[PipelineStage]:
        """
        获取所有已初始化插件提供的管道阶段

        Returns:
            管道阶段列表
        """
        stages = []
        for name in self.initialized_plugins:
            stages.extend(self.plugins[name].get_pipeline_stages())
        return stages

    def get_all_stage_hooks(self) -> List[StageHook]:
        """
        获取所有已初始化插件提供的阶段钩子

        Returns:
            阶段钩子列表
        """
        hooks = []
        for name in self.initialized_plugins:
            hooks.extend(self.plugins[name].get_stage_hooks())
        return hooks

    def get_all_pipeline_hooks(self) -> List[PipelineHook]:
        """
        获取所有已初始化插件提供的管道钩子

        Returns:
            管道钩子列表
        """
        hooks = []
        for name in self.initialized_plugins:
            hooks.extend(self.plugins[name].get_pipeline_hooks())
        return hooks

    def get_all_api_routers(self) -> Dict[str, APIRouter]:
        """
        获取所有插件提供的API路由

        Returns:
            API路由字典，键为插件名称，值为路由器
        """
        return self.plugin_routers.copy()

    def configure_pipelines(self, pipelines: Dict[str, Pipeline]) -> None:
        """
        配置管道

        允许每个插件自定义管道配置

        Args:
            pipelines: 管道字典，键为管道名称，值为管道实例
        """
        for name in self.initialized_plugins:
            plugin = self.plugins[name]
            for pipeline_name, pipeline in pipelines.items():
                try:
                    plugin.configure_pipeline(pipeline)
                except Exception as e:
                    logger.error(f"Error in plugin '{name}' while configuring pipeline '{pipeline_name}': {e}")

    def get_extension_point(self, name: str) -> Dict[str, Any]:
        """
        获取扩展点

        Args:
            name: 扩展点名称

        Returns:
            扩展点字典，键为插件名称，值为实现对象
        """
        return self.extension_points.get(name, {})
