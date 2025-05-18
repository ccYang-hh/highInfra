import sys
import importlib.util
from typing import Optional, Any, Dict, TypeVar

from tmatrix.components.logging import init_logger

logger = init_logger("runtime/plugins")

T = TypeVar("T")


class PluginLoader:
    """
    TODO, 分离Loader
    插件加载器
    """
    @staticmethod
    def load_plugin(config: T, name: str) -> Optional[Any]:
        """加载指定的插件"""
        location = config.locate_plugin(name)
        if not location:
            return None

        location_type, location_value = location

        # 从模块加载
        if location_type == "module":
            try:
                return importlib.import_module(location_value)
            except ImportError as e:
                logger.error(f"无法加载插件模块 {location_value}: {e}")

        # 从文件路径加载
        elif location_type == "path":
            try:
                spec = importlib.util.spec_from_file_location(name, location_value)
                if spec is None:
                    logger.error(f"无法为 {location_value} 创建模块规范")
                    return None

                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                return module
            except Exception as e:
                logger.error(f"无法从路径 {location_value} 加载插件 {name}: {e}")

        return None

    @staticmethod
    def load_enabled_plugins(config: T) -> Dict[str, Any]:
        """加载所有启用的插件"""
        result = {}
        for name, _ in config.plugin_registry.get_enabled_plugins():
            plugin = PluginLoader.load_plugin(config, name)
            if plugin:
                result[name] = plugin
        return result
    