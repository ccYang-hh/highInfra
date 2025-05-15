from .manager import ConfigManager
from .loader import ConfigLoader
from .validator import ConfigValidator
from .config import RuntimeConfig

__all__ = [
    'ConfigManager',
    'ConfigLoader',
    'ConfigValidator',
    'RuntimeConfig',
    'get_config_manager'
]


# 导出获取配置管理器的便捷函数
def get_config_manager(config_path=None):
    """
    获取配置管理器实例

    Args:
        config_path: 可选的配置文件路径

    Returns:
        ConfigManager实例
    """
    return ConfigManager.get_instance(config_path)


