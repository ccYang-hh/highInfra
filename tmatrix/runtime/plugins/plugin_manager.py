import os
import sys
import enum
import time
import asyncio
import pkgutil
import inspect
import threading
import traceback
import importlib
import importlib.util
from pathlib import Path
from enum import Enum, auto
from fastapi import APIRouter
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Set, Optional, Any, Type, Callable, TypeVar, Tuple, Generic

from fastapi import APIRouter

from tmatrix.components.logging import init_logger
from tmatrix.runtime.pipeline.stages import PipelineStage
from tmatrix.runtime.pipeline.hooks import PipelineHook, StageHook
from tmatrix.runtime.pipeline.pipeline import Pipeline
from tmatrix.runtime.plugins import Plugin, IPluginConfig
from tmatrix.runtime.config import PluginInfo, PluginRegistryConfig
from tmatrix.runtime.utils import EventBus, PluginEvent, PluginEventType


logger = init_logger("runtime/plugins")

TConfig = TypeVar('TConfig', bound='IPluginConfig')


class PluginState(Enum):
    """插件状态"""
    NOT_LOADED = auto()   # 未加载
    LOADED = auto()       # 已加载（但未初始化）
    INITIALIZED = auto()  # 已初始化（但未运行）
    RUNNING = auto()      # 运行中
    STOPPED = auto()      # 已停止
    ERROR = auto()        # 错误状态


class PluginManager:
    """
    插件管理器

    负责插件的加载、初始化、启动、停止和资源获取
    """

    def __init__(self, registry: 'PluginRegistryConfig', event_bus: Optional['EventBus'] = None):
        """
        初始化插件管理器

        Args:
            registry: 插件注册表配置
            event_bus: 事件总线（可选）
        """
        self.registry = registry
        self.event_bus = event_bus or EventBus()

        # 插件实例和状态
        self._plugins: Dict[str, 'Plugin'] = {}             # 名称 -> 插件实例
        self._plugin_states: Dict[str, 'PluginState'] = {}  # 名称 -> 插件状态
        self._plugin_configs: Dict[str, Any] = {}           # 名称 -> 插件配置实例

        # 资源缓存
        self._stages_cache: Dict[str, List[PipelineStage]] = {}  # 名称 -> 管道阶段列表
        self._stage_hooks_cache: Dict[str, List[StageHook]] = {}  # 名称 -> 阶段钩子列表
        self._pipeline_hooks_cache: Dict[str, List[PipelineHook]] = {}  # 名称 -> 管道钩子列表
        self._routers_cache: Dict[str, APIRouter] = {}  # 名称 -> API路由

        # 线程安全
        self._lock = threading.RLock()

        # 初始化状态
        self._initialized = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        设置事件循环

        Args:
            loop: 异步事件循环
        """
        self._loop = loop
        self.event_bus.set_event_loop(loop)

    async def initialize(self) -> None:
        """
        初始化插件管理器

        - 发现并加载插件
        - 订阅配置更新事件
        """
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            # 发现插件
            if self.registry.auto_discovery:
                self.registry.discover_plugins()

            # 加载所有启用的插件
            for plugin_name, plugin_info in self.registry.get_enabled_plugins():
                try:
                    await self._load_plugin(plugin_name, plugin_info)
                except Exception as e:
                    logger.error(f"加载插件 {plugin_name} 失败: {e}")
                    self._publish_error_event(plugin_name, f"加载失败: {e}")

            # 订阅配置更新事件
            await self.event_bus.subscribe_async(
                PluginEventType.CONFIG_UPDATED,
                self._handle_config_update
            )

            self._initialized = True

    async def _load_plugin(self, plugin_name: str, plugin_info: 'PluginInfo') -> None:
        """
        加载单个插件

        Args:
            plugin_name: 插件名称
            plugin_info: 插件信息
        """
        # 检查插件是否已加载
        if plugin_name in self._plugins:
            return

        # 导入插件模块
        plugin_cls = self._import_plugin_class(plugin_name, plugin_info)
        if not plugin_cls:
            return

        # 检查是否提供正确插件名称
        if plugin_cls.get_name() != plugin_name:
            error_message = f"插件名称不匹配: 预期 '{plugin_name}', 实际为 '{plugin_cls.get_name()}'"
            logger.error(error_message)
            raise RuntimeError(error_message)

        # 创建插件实例
        plugin = plugin_cls()

        # 初始化状态
        self._plugin_states[plugin_name] = PluginState.LOADED
        self._plugins[plugin_name] = plugin

        # 发布加载事件
        self._publish_event(
            plugin_name,
            PluginEventType.LOADED,
            {"version": plugin.get_version()}
        )

        # 初始化插件
        try:
            await plugin.initialize(config_dir=plugin_info.config_path)
            self._plugin_states[plugin_name] = PluginState.INITIALIZED
            self._plugin_configs[plugin_name] = plugin.config

            # 发布初始化事件
            self._publish_event(
                plugin_name,
                PluginEventType.INITIALIZED,
                {"version": plugin.get_version()}
            )
        except Exception as e:
            logger.error(f"初始化插件 {plugin_name} 失败: {e}")
            self._plugin_states[plugin_name] = PluginState.ERROR
            self._publish_error_event(plugin_name, f"初始化失败: {e}")

    def _import_plugin_class(self, plugin_name: str, plugin_info: 'PluginInfo') -> Optional[Type['Plugin']]:
        """
        导入插件类

        Args:
            plugin_name: 插件名称
            plugin_info: 插件信息

        Returns:
            插件类或None
        """
        try:
            # 基于模块方式导入
            if plugin_info.module:
                module = importlib.import_module(plugin_info.module)
            # 基于路径方式导入
            elif plugin_info.path:
                module_path = Path(plugin_info.path)
                module_name = module_path.stem
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                if not spec or not spec.loader:
                    logger.error(f"无法加载插件模块: {module_path}")
                    return None

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            else:
                logger.error(f"插件 {plugin_name} 未指定模块或路径")
                return None

            # 查找插件类
            plugin_cls = None
            for name, obj in inspect.getmembers(module):
                if inspect.isclass(obj):
                    try:
                        if issubclass(obj, Plugin) and obj != Plugin and not inspect.isabstract(obj):
                            plugin_cls = obj
                            break
                    except TypeError:
                        # 忽略不是类的情况
                        continue

            if not plugin_cls:
                logger.error(f"在模块中未找到有效的插件类: {plugin_name}")
                return None

            return plugin_cls

        except Exception as e:
            logger.error(f"导入插件 {plugin_name} 失败: {e}")
            self._publish_error_event(plugin_name, f"导入失败: {e}")
            return None

    async def start_plugins(self) -> None:
        """启动所有已初始化的插件"""
        if not self._initialized:
            await self.initialize()

        for plugin_name, state in list(self._plugin_states.items()):
            if state == PluginState.INITIALIZED:
                await self.start_plugin(plugin_name)

    async def start_plugin(self, plugin_name: str) -> bool:
        """
        启动指定插件

        Args:
            plugin_name: 插件名称

        Returns:
            是否成功启动
        """
        with self._lock:
            if plugin_name not in self._plugins:
                logger.warning(f"插件 {plugin_name} 不存在，无法启动")
                return False

            if self._plugin_states.get(plugin_name) != PluginState.INITIALIZED:
                logger.warning(f"插件 {plugin_name} 状态不是已初始化，无法启动")
                return False

            plugin = self._plugins[plugin_name]

            # 检查依赖插件是否启动
            dependencies = plugin.get_dependencies()
            for dep_name in dependencies:
                if dep_name not in self._plugin_states or self._plugin_states[dep_name] != PluginState.RUNNING:
                    logger.warning(f"插件 {plugin_name} 依赖的插件 {dep_name} 未启动")
                    return False

        # 开始文件监控（如果有）
        config = self._plugin_configs.get(plugin_name)
        if config and hasattr(config, "start_file_watcher"):
            try:
                config.start_file_watcher()
            except Exception as e:
                logger.error(f"启动插件 {plugin_name} 配置监控失败: {e}")

        # 更新状态
        with self._lock:
            self._plugin_states[plugin_name] = PluginState.RUNNING

        # 发布事件
        self._publish_event(
            plugin_name,
            PluginEventType.STARTED,
            {"version": plugin.get_version()}
        )

        return True

    async def stop_plugin(self, plugin_name: str) -> bool:
        """
        停止指定插件

        Args:
            plugin_name: 插件名称

        Returns:
            是否成功停止
        """
        with self._lock:
            if plugin_name not in self._plugins:
                logger.warning(f"插件 {plugin_name} 不存在，无法停止")
                return False

            plugin = self._plugins[plugin_name]

            # 检查是否有依赖此插件的其他插件正在运行
            for other_name, other_plugin in self._plugins.items():
                if (other_name != plugin_name and
                        self._plugin_states.get(other_name) == PluginState.RUNNING and
                        plugin_name in other_plugin.get_dependencies()):
                    logger.warning(f"插件 {other_name} 依赖 {plugin_name}，无法停止")
                    return False

        # 停止文件监控（如果有）
        config = self._plugin_configs.get(plugin_name)
        if config and hasattr(config, "stop_file_watcher"):
            try:
                config.stop_file_watcher()
            except Exception as e:
                logger.error(f"停止插件 {plugin_name} 配置监控失败: {e}")

        # 清除资源缓存
        with self._lock:
            if plugin_name in self._stages_cache:
                del self._stages_cache[plugin_name]
            if plugin_name in self._stage_hooks_cache:
                del self._stage_hooks_cache[plugin_name]
            if plugin_name in self._pipeline_hooks_cache:
                del self._pipeline_hooks_cache[plugin_name]
            if plugin_name in self._routers_cache:
                del self._routers_cache[plugin_name]

            # 更新状态
            self._plugin_states[plugin_name] = PluginState.INITIALIZED

        # 发布事件
        self._publish_event(
            plugin_name,
            PluginEventType.STOPPED,
            {"version": plugin.get_version()}
        )

        return True

    async def unload_plugin(self, plugin_name: str) -> bool:
        """
        卸载指定插件

        Args:
            plugin_name: 插件名称

        Returns:
            是否成功卸载
        """
        with self._lock:
            if plugin_name not in self._plugins:
                logger.warning(f"插件 {plugin_name} 不存在，无法卸载")
                return False

            # 如果插件正在运行，先停止它
            if self._plugin_states.get(plugin_name) == PluginState.RUNNING:
                success = await self.stop_plugin(plugin_name)
                if not success:
                    return False

            plugin = self._plugins[plugin_name]

            # 关闭插件
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"关闭插件 {plugin_name} 失败: {e}")

            # 移除插件和状态
            with self._lock:
                del self._plugins[plugin_name]
                del self._plugin_states[plugin_name]
                if plugin_name in self._plugin_configs:
                    del self._plugin_configs[plugin_name]

            # 发布事件
            self._publish_event(
                plugin_name,
                PluginEventType.UNLOADED,
                {"version": plugin.get_version()}
            )

            return True

    async def reload_plugin(self, plugin_name: str) -> bool:
        """
        重新加载插件

        Args:
            plugin_name: 插件名称

        Returns:
            是否成功重新加载
        """
        # 卸载插件
        await self.unload_plugin(plugin_name)

        # 查找插件信息
        plugin_info = self.registry.plugins.get(plugin_name)
        if not plugin_info:
            logger.error(f"插件 {plugin_name} 不存在于注册表中")
            return False

        # 重新加载插件
        await self._load_plugin(plugin_name, plugin_info)

        # 如果注册表中标记为启用，则启动插件
        if plugin_info.enabled:
            return await self.start_plugin(plugin_name)

        return True

    async def shutdown(self) -> None:
        """关闭插件管理器，停止并卸载所有插件"""
        # 停止所有运行中的插件
        for plugin_name in list(self._plugins.keys()):
            if self._plugin_states.get(plugin_name) == PluginState.RUNNING:
                await self.stop_plugin(plugin_name)

        # 卸载所有插件
        for plugin_name in list(self._plugins.keys()):
            await self.unload_plugin(plugin_name)

        # 取消事件订阅
        await self.event_bus.unsubscribe_async(
            PluginEventType.CONFIG_UPDATED,
            self._handle_config_update
        )

        self._initialized = False

    # 资源获取方法

    def get_plugin(self, plugin_name: str) -> Optional['Plugin']:
        """获取指定名称的插件实例"""
        return self._plugins.get(plugin_name)

    def get_plugin_state(self, plugin_name: str) -> Optional['PluginState']:
        """获取指定插件的状态"""
        return self._plugin_states.get(plugin_name)

    def get_all_plugins(self) -> Dict[str, 'Plugin']:
        """获取所有插件实例"""
        return self._plugins.copy()

    def get_all_plugin_states(self) -> Dict[str, 'PluginState']:
        """获取所有插件状态"""
        return self._plugin_states.copy()

    def get_pipeline_stages(self, plugin_name: str) -> List[PipelineStage]:
        """获取指定插件的管道阶段"""
        # 使用缓存
        if plugin_name in self._stages_cache:
            return self._stages_cache[plugin_name]

        plugin = self._plugins.get(plugin_name)
        if not plugin or self._plugin_states.get(plugin_name) != PluginState.RUNNING:
            return []

        stages = plugin.get_pipeline_stages()
        self._stages_cache[plugin_name] = stages
        return stages

    def get_stage_hooks(self, plugin_name: str) -> List[StageHook]:
        """获取指定插件的阶段钩子"""
        # 使用缓存
        if plugin_name in self._stage_hooks_cache:
            return self._stage_hooks_cache[plugin_name]

        plugin = self._plugins.get(plugin_name)
        if not plugin or self._plugin_states.get(plugin_name) != PluginState.RUNNING:
            return []

        hooks = plugin.get_stage_hooks()
        self._stage_hooks_cache[plugin_name] = hooks
        return hooks

    def get_pipeline_hooks(self, plugin_name: str) -> List[PipelineHook]:
        """获取指定插件的管道钩子"""
        # 使用缓存
        if plugin_name in self._pipeline_hooks_cache:
            return self._pipeline_hooks_cache[plugin_name]

        plugin = self._plugins.get(plugin_name)
        if not plugin or self._plugin_states.get(plugin_name) != PluginState.RUNNING:
            return []

        hooks = plugin.get_pipeline_hooks()
        self._pipeline_hooks_cache[plugin_name] = hooks
        return hooks

    def get_api_router(self, plugin_name: str) -> Optional[APIRouter]:
        """获取指定插件的API路由"""
        # 使用缓存
        if plugin_name in self._routers_cache:
            return self._routers_cache[plugin_name]

        plugin = self._plugins.get(plugin_name)
        if not plugin or self._plugin_states.get(plugin_name) != PluginState.RUNNING:
            return None

        router = plugin.get_api_router()
        if router:
            self._routers_cache[plugin_name] = router
        return router

    def get_all_pipeline_stages(self) -> List[Tuple[str, PipelineStage]]:
        """获取所有启用插件的管道阶段"""
        result = []
        for plugin_name in self._plugins:
            if self._plugin_states.get(plugin_name) == PluginState.RUNNING:
                stages = self.get_pipeline_stages(plugin_name)
                for stage in stages:
                    result.append((plugin_name, stage))
        return result

    def get_all_stage_hooks(self) -> List[Tuple[str, StageHook]]:
        """获取所有启用插件的阶段钩子"""
        result = []
        for plugin_name in self._plugins:
            if self._plugin_states.get(plugin_name) == PluginState.RUNNING:
                hooks = self.get_stage_hooks(plugin_name)
                for hook in hooks:
                    result.append((plugin_name, hook))
        return result

    def get_all_pipeline_hooks(self) -> List[Tuple[str, PipelineHook]]:
        """获取所有启用插件的管道钩子"""
        result = []
        for plugin_name in self._plugins:
            if self._plugin_states.get(plugin_name) == PluginState.RUNNING:
                hooks = self.get_pipeline_hooks(plugin_name)
                for hook in hooks:
                    result.append((plugin_name, hook))
        return result

    def get_all_api_routers(self) -> Dict[str, APIRouter]:
        """获取所有启用插件的API路由"""
        result = {}
        for plugin_name in self._plugins:
            if self._plugin_states.get(plugin_name) == PluginState.RUNNING:
                router = self.get_api_router(plugin_name)
                if router:
                    result[plugin_name] = router
        return result

    # 事件处理方法

    def _publish_event(self, plugin_name: str, event_type: PluginEventType,
                       data: Optional[Dict[str, Any]] = None) -> None:
        """发布插件事件"""
        event = PluginEvent(plugin_name=plugin_name, event_type=event_type, data=data)
        self.event_bus.publish(event_type, event)

    def _publish_error_event(self, plugin_name: str, error_message: str) -> None:
        """发布插件错误事件"""
        self._publish_event(
            plugin_name,
            PluginEventType.ERROR,
            {"error": error_message}
        )

    async def _handle_config_update(self, event: PluginEvent) -> None:
        """处理插件配置更新事件"""
        if event.event_type != PluginEventType.CONFIG_UPDATED or not event.data:
            return

        plugin_name = event.plugin_name
        config_data = event.data.get("config", {})

        # 获取插件
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            logger.warning(f"无法更新不存在的插件配置: {plugin_name}")
            return

        # 更新配置
        try:
            await plugin.update_config(config_data)
            logger.info(f"已更新插件 {plugin_name} 的配置")
        except Exception as e:
            logger.error(f"更新插件 {plugin_name} 配置时出错: {e}")
            self._publish_error_event(plugin_name, f"配置更新失败: {e}")

    # PluginRegistry 便捷方法

    def register_plugin(self, plugin_name: str, plugin_info: 'PluginInfo') -> None:
        """注册新插件"""
        with self._lock:
            if plugin_name in self.registry.plugins:
                # 更新已有信息
                self.registry.plugins[plugin_name] = plugin_info
            else:
                # 添加新插件
                self.registry.plugins[plugin_name] = plugin_info

            # 发布事件
            self._publish_event(
                plugin_name,
                PluginEventType.REGISTERED,
                {"info": plugin_info.to_dict()}
            )

    def unregister_plugin(self, plugin_name: str) -> bool:
        """取消注册插件"""
        with self._lock:
            if plugin_name not in self.registry.plugins:
                return False

            # 如果插件已加载，先卸载它
            if plugin_name in self._plugins:
                asyncio.create_task(self.unload_plugin(plugin_name))

            # 从注册表中移除
            del self.registry.plugins[plugin_name]
            return True

    def set_plugin_enabled(self, plugin_name: str, enabled: bool) -> bool:
        """设置插件启用状态"""
        with self._lock:
            if plugin_name not in self.registry.plugins:
                return False

            # 更新注册表
            self.registry.plugins[plugin_name].enabled = enabled

            # 根据状态加载或卸载插件
            if enabled:
                if plugin_name not in self._plugins:
                    asyncio.create_task(self._load_plugin(
                        plugin_name, self.registry.plugins[plugin_name]
                    ))
            else:
                if plugin_name in self._plugins:
                    asyncio.create_task(self.unload_plugin(plugin_name))

            return True

    def discover_plugins(self) -> Dict[str, 'PluginInfo']:
        """发现新插件"""
        return self.registry.discover_plugins()



