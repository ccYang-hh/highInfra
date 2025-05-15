from pathlib import Path
from typing import Any, Dict, List, get_type_hints, Optional, Tuple
from dataclasses import dataclass, field, asdict, fields

from tmatrix.components.logging import init_logger
from tmatrix.runtime.service_discovery import ServiceDiscoveryType

logger = init_logger("runtime/config")


@dataclass
class PluginInfo:
    """插件信息类 - 简化了职责"""
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
class PipelineConfig:
    """管道配置类"""
    max_parallelism: int = 1
    stages: List[str] = field(default_factory=list)
    connections: List[Dict[str, str]] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)
    exit_points: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """将Pipeline配置对象转换为字典"""
        return asdict(self)


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
    pipelines: Dict[str, PipelineConfig] = field(
        default_factory=lambda: {"main": PipelineConfig(max_parallelism=4)}
    )

    # 性能配置
    worker_threads: int = 8
    max_batch_size: int = 32
    request_timeout: int = 60

    # 监控配置
    enable_metrics: bool = True
    metrics_port: int = 8001

    # 安全配置
    enable_auth: bool = False
    auth_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将配置对象转换为字典"""
        return asdict(self) # 直接使用asdict, 这会递归地将所有嵌套的对象也转换为字典

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
        pipelines_data = data.get("pipelines", {})
        pipelines = {}
        for name, config in pipelines_data.items():
            pipelines[name] = PipelineConfig(**config)
        processed_data["pipelines"] = pipelines

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
