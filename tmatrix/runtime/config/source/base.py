from typing import Any, Callable, Dict
from tmatrix.common.logging import init_logger

logger = init_logger("runtime/config")


class ConfigSource:
    """配置源抽象基类"""

    def __init__(self, source_id: str):
        """
        初始化配置源

        Args:
            source_id: 配置源标识
        """
        self.source_id = source_id

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        raise NotImplementedError("子类必须实现此方法")

    def start_watcher(self, on_change_callback: Callable[[], None]) -> None:
        """
        开始监控配置变化

        Args:
            on_change_callback: 配置变化时的回调函数
        """
        raise NotImplementedError("子类必须实现此方法")

    def stop_watcher(self) -> None:
        """停止监控配置变化"""
        raise NotImplementedError("子类必须实现此方法")
