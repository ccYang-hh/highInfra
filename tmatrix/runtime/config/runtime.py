import copy
import asyncio
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict, fields
from typing import Any, Dict, List, get_type_hints, Tuple, Optional, Callable

from tmatrix.common.logging import init_logger
from tmatrix.common.errors import AppError
from tmatrix.runtime.service_discovery import ServiceDiscoveryType
from tmatrix.runtime.utils import ConfigEvent, EventBus
from tmatrix.runtime.config import ConfigValidator, FileConfigSource

logger = init_logger("runtime/config")


# 导出获取配置管理器的便捷函数
def get_config_manager(config_path = None, event_bus: Optional[EventBus] = None):
    """
    获取配置管理器实例

    Args:
        config_path: 可选的配置文件路径
        event_bus: 全局事件总线

    Returns:
        ConfigManager实例
    """
    return RuntimeConfigManager.get_instance(config_path, event_bus)


@dataclass
class PluginInfo:
    """插件信息类"""
    enabled: bool = True
    module: Optional[str] = None
    path: Optional[str] = None
    priority: int = 100
    config_path: Optional[str] = None  # 指向插件配置文件的路径

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "enabled": self.enabled,
            "priority": self.priority
        }
        if self.module:
            result["module"] = self.module
        if self.path:
            result["path"] = self.path
        if self.config_path:
            result["config_path"] = self.config_path
        return result


@dataclass
class PluginRegistryConfig:
    """插件注册表配置类"""
    plugins: Dict[str, PluginInfo] = field(default_factory=dict)
    discovery_paths: List[str] = field(default_factory=list)
    auto_discovery: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """将配置转换为字典"""
        return {
            "plugins": {name: plugin.to_dict() for name, plugin in self.plugins.items()},
            "discovery_paths": self.discovery_paths,
            "auto_discovery": self.auto_discovery
        }

    def get_enabled_plugins(self) -> List[Tuple[str, PluginInfo]]:
        """获取所有启用的插件"""
        return sorted(
            [(name, info) for name, info in self.plugins.items() if info.enabled],
            key=lambda x: x[1].priority
        )

    def discover_plugins(self) -> Dict[str, PluginInfo]:
        """从发现路径中发现插件，返回新发现的插件"""
        discovered_plugins = {}

        for path_str in self.discovery_paths:
            path = Path(path_str)
            if not path.exists():
                logger.warning(f"插件发现路径不存在: {path}")
                continue

            # 搜索插件文件
            for file_path in path.glob("*.py"):
                if file_path.stem.startswith("__"):
                    continue

                plugin_name = file_path.stem

                # 如果插件尚未注册，则自动添加
                if plugin_name not in self.plugins and self.auto_discovery:
                    plugin_info = PluginInfo(
                        enabled=True,
                        path=str(file_path)
                    )
                    self.plugins[plugin_name] = plugin_info
                    discovered_plugins[plugin_name] = plugin_info

            # 搜索插件包
            for dir_path in path.glob("*/"):
                if dir_path.is_dir() and (dir_path / "__init__.py").exists():
                    plugin_name = dir_path.name

                    # 如果插件尚未注册，则自动添加
                    if plugin_name not in self.plugins and self.auto_discovery:
                        plugin_info = PluginInfo(
                            enabled=True,
                            module=f"{dir_path.name}"
                        )
                        self.plugins[plugin_name] = plugin_info
                        discovered_plugins[plugin_name] = plugin_info

        return discovered_plugins


@dataclass
class PipelineRoute:
    path: str
    method: str


@dataclass
class PipelineConfig:
    """管道配置类"""
    pipeline_name: str
    plugins: List[str] = field(default_factory=list)
    routes: List[PipelineRoute] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """将Pipeline配置对象转换为字典"""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PipelineConfig":
        routes = [
            PipelineRoute(
                path=route["path"],
                method=route["method"]
            )
            for route in data.get("routes", [])
        ]
        return PipelineConfig(
            pipeline_name=data["pipeline_name"],
            plugins=data.get("plugins", []),
            routes=routes
        )

@dataclass
class ETCDConfig:
    host: str = "localhost"
    port: int = 2379


@dataclass
class RuntimeConfig:
    """运行时配置类"""
    # 应用基本配置
    app_name: str = "tMatrix Inference System"
    app_version: str = "1.0.0"
    app_description: str = ("Intelligent Scheduling and Control Center "
                            "Based on Multi-Instance Heterogeneous Inference Engine")

    host: str = "0.0.0.0"
    port: int = 30088
    log_level: str = "info"
    service_discovery: str = ServiceDiscoveryType.ETCD.value

    # CORS配置
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

    # 静态路径
    static_path: str = field(default="./static", metadata={"optional": True})

    # 插件配置
    plugin_registry: PluginRegistryConfig = field(default_factory=PluginRegistryConfig)

    # 管道配置
    pipelines: List[PipelineConfig] = field(default_factory=list[PipelineConfig])

    # ETCD配置
    etcd: Optional[ETCDConfig] = None

    # 性能配置
    max_batch_size: int = 128
    request_timeout: int = 60

    # 监控配置
    enable_monitor: bool = True
    monitor_config: str = field(default_factory=str)

    # 安全配置
    enable_auth: bool = False
    auth_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将配置对象转换为字典"""
        return asdict(self)  # 直接使用asdict, 这会递归地将所有嵌套的对象也转换为字典

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeConfig":
        """从字典创建配置对象"""
        # 创建一个新的字典来存储处理后的数据
        processed_data = {}

        # 处理插件注册表配置
        plugin_registry = PluginRegistryConfig()
        registry_data = data.get("plugin_registry", {})

        # 处理插件
        plugins_data = registry_data.get("plugins", {})
        for name, plugin_config in plugins_data.items():
            plugin_registry.plugins[name] = PluginInfo(**plugin_config)

        # 处理发现路径
        plugin_registry.discovery_paths = registry_data.get("discovery_paths", [])
        plugin_registry.auto_discovery = registry_data.get("auto_discovery", True)

        # 存储处理后的插件注册表
        processed_data["plugin_registry"] = plugin_registry

        # 处理管道配置
        pipelines_data = data.get("pipelines", [])
        pipelines = [PipelineConfig.from_dict(item) for item in pipelines_data]
        processed_data["pipelines"] = pipelines

        # 处理ETCD配置
        etcd_config = ETCDConfig()
        etcd_config.host = data.get("etcd", {}).get("host", "localhost")
        etcd_config.port = data.get("etcd", {}).get("port", 2379)
        processed_data["etcd"] = etcd_config

        # 其他RuntimeConfig字段
        field_names = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in field_names and key not in ["pipelines", "plugin_registry"]:
                processed_data[key] = value

        # 处理类型转换
        field_types = get_type_hints(cls)
        for field_name, field_type in field_types.items():
            if field_name in processed_data and field_name not in ["pipelines", "plugin_registry"]:
                # 基本类型转换
                if field_type in (int, float, bool, str) and not isinstance(processed_data[field_name], field_type):
                    try:
                        if field_type is bool and isinstance(processed_data[field_name], str):
                            processed_data[field_name] = processed_data[field_name].lower() == "true"
                        else:
                            processed_data[field_name] = field_type(processed_data[field_name])
                    except (ValueError, TypeError):
                        logger.warning(f"无法将 {field_name} 转换为 {field_type.__name__}")

        return cls(**processed_data)

    def locate_plugin(self, name: str) -> Optional[Tuple[str, Any]]:
        """获取插件的位置信息（模块路径或文件路径）"""
        if name not in self.plugin_registry.plugins:
            logger.error(f"插件 {name} 未注册")
            return None

        plugin_info = self.plugin_registry.plugins[name]

        if not plugin_info.enabled:
            logger.warning(f"插件 {name} 已禁用")
            return None

        # 优先返回模块路径
        if plugin_info.module:
            return ("module", plugin_info.module)

        # 其次返回文件路径
        if plugin_info.path:
            return ("path", plugin_info.path)

        logger.error(f"插件 {name} 未指定模块或路径")
        return None

    def discover_plugins(self) -> Dict[str, PluginInfo]:
        """发现所有可用插件"""
        return self.plugin_registry.discover_plugins()

    def get_plugin_config_path(self, name: str) -> Optional[str]:
        """获取插件的配置文件路径"""
        if name not in self.plugin_registry.plugins:
            return None
        return self.plugin_registry.plugins[name].config_path


class RuntimeConfigManager:
    """运行时配置管理器单例"""

    _instance = None
    _lock = threading.RLock()

    @classmethod
    def get_instance(cls, config_path: Optional[str] = None,
                     event_bus: Optional[EventBus] = None) -> 'RuntimeConfigManager':
        """获取单例实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(config_path, event_bus)
            return cls._instance

    def __init__(self, config_path: Optional[str] = None, event_bus: Optional[EventBus] = None):
        """
        初始化运行时配置管理器

        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path or "config.yaml"
        self.validator = ConfigValidator()

        # 创建事件总线
        self._event_bus = event_bus

        # 配置源
        config_file = Path(self.config_path)
        self._config_source = FileConfigSource("runtime", config_file)

        # 配置管理锁
        self._config_lock = threading.RLock()

        # 加载配置
        raw_config = self._config_source.get_config()

        # 验证配置
        try:
            self.validator.validate_config(raw_config, "runtime")
        except AppError as app_error:
            logger.warning(f"运行时配置验证发现问题: {app_error}")
            raise app_error

        self._config = RuntimeConfig.from_dict(raw_config)

        # 启动文件监控
        self._config_source.start_watcher(self._on_config_change)

        self._closed = False

    @property
    def closed(self):
        return self._closed

    def get_config(self):
        """获取完整配置对象"""
        if self._closed:
            raise RuntimeError("Runtime进程已退出")

        with self._config_lock:
            return copy.deepcopy(self._config)

    def reload(self):
        """重新加载配置（手动触发）"""
        self._on_config_change()
        return self.get_config()

    def _on_config_change(self):
        """配置文件变更处理"""
        with self._config_lock:
            logger.info("检测到变更，Runtime配置热更新......")

            # 加载新配置
            raw_config = self._config_source.get_config()

            # 验证配置
            try:
                self.validator.validate_config(raw_config, "runtime")
            except AppError as app_error:
                logger.warning(f"运行时配置验证发现问题: {app_error}")
                raise app_error


            # 更新当前配置
            new_config = RuntimeConfig.from_dict(raw_config)
            self._config = new_config

            # 发布变更事件
            self._publish_config_change()

    def _publish_config_change(self):
        """发布配置变更事件"""
        # 创建事件
        event = ConfigEvent(
            source="runtime",
            config=copy.deepcopy(self._config)
        )

        # 发布事件
        self._event_bus.publish("config.runtime.changed", event)

    def subscribe_events(self, callback: Callable[[ConfigEvent], None]) -> None:
        """
        订阅配置变更事件

        Args:
            callback: 回调函数，接收ConfigEvent参数
        """
        if self._closed:
            raise RuntimeError("Runtime进程已退出")

        self._event_bus.subscribe("config.runtime.changed", callback)

    def unsubscribe_events(self, callback: Callable[[ConfigEvent], None]) -> None:
        """
        取消订阅配置变更事件

        Args:
            callback: 要取消的回调函数
        """
        if self._closed:
            raise RuntimeError("Runtime进程已退出")

        self._event_bus.unsubscribe("config.runtime.changed", callback)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置异步事件循环"""
        self._event_bus.set_event_loop(loop)

    async def subscribe_async(self, callback: Callable[[ConfigEvent], None]) -> None:
        """
        异步订阅配置事件

        Args:
            callback: 异步回调函数
        """
        if self._closed:
            raise RuntimeError("Runtime进程已退出")

        await self._event_bus.subscribe_async("config.runtime.changed", callback)

    async def unsubscribe_async(self, callback: Callable[[ConfigEvent], None]) -> None:
        """
        取消异步订阅

        Args:
            callback: 要取消的异步回调函数
        """
        if self._closed:
            raise RuntimeError("Runtime进程已退出")

        await self._event_bus.unsubscribe_async("config.runtime.changed", callback)

    def close(self):
        with self._config_lock:
            try:
                if hasattr(self._config_source, "stop_watcher"):
                    self._config_source.stop_watcher()
            except Exception as e:
                logger.warning(f"关闭文件监控时异常: {e}")
            try:
                if hasattr(self._event_bus, "close"):
                    self._event_bus.close()
            except Exception as e:
                logger.warning(f"事件总线关闭异常: {e}")
            self._closed = True

    async def aclose(self):
        with self._config_lock:
            try:
                if hasattr(self._config_source, "stop_watcher"):
                    self._config_source.stop_watcher()
            except Exception as e:
                logger.warning(f"关闭文件监控时异常: {e}")
            try:
                if hasattr(self._event_bus, "aclose"):
                    await self._event_bus.aclose()
                elif hasattr(self._event_bus, "close"):
                    self._event_bus.close()
            except Exception as e:
                logger.warning(f"事件总线关闭异常: {e}")
            self._closed = True
