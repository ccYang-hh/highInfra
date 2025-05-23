import threading
from abc import ABC
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional, Any, Callable, List

from tmatrix.common.logging import init_logger
from tmatrix.runtime.config import FileConfigSource


logger = init_logger("runtime/plugins")


class IPluginConfig(ABC):
    """
    插件配置接口

    所有插件配置类的基础接口，无论是复杂配置还是简单配置
    """

    def __init__(self, plugin_name: str):
        """
        初始化配置

        Args:
            plugin_name: 插件名称
            TODO 优化plugin_name 不强制一致
        """
        self.plugin_name = plugin_name


class PluginConfig(IPluginConfig):
    """
    插件配置类

    适用于不需要外部配置文件的插件
    """

    def __init__(self, plugin_name: str):
        """
        初始化简单插件配置

        Args:
            plugin_name: 插件名称
        """
        super().__init__(plugin_name)


class FileSourcePluginConfig(IPluginConfig):
    """
    文件插件配置基类

        1.需要接入外部配置源的插件配置需要继承此基类
        2.其余场景，可以使用PluginConfig
    """

    def __init__(self, plugin_name: str, config_dir: Optional[str] = None):
        """
        初始化插件配置

        Args:
            plugin_name: 插件名称
            config_dir: 配置目录
        """
        super().__init__(plugin_name)
        self.config_dir = config_dir or "./configs"
        self._lock = threading.RLock()
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []

        # 初始化配置
        self._config_file_path = self._find_config_file()

        # 创建配置源
        self._config_source = None
        if self._config_file_path:
            self._config_source = FileConfigSource(
                source_id=f"plugin:{plugin_name}",
                config_path=self._config_file_path
            )
        else:
            logger.warning(f"插件 {plugin_name} 配置文件不存在")

        # 加载初始配置
        self._config_data = self.load_config()

        # 应用到实例
        self.update_from_dict(self._config_data)

    def _find_config_file(self) -> Optional[Path]:
        """查找配置文件路径"""
        for ext in ['yaml', 'yml', 'json']:
            path = Path(self.config_dir) / f"{self.plugin_name}.{ext}"
            if path.exists():
                return path
        return None

    def load_config(self) -> Dict[str, Any]:
        """
        加载当前配置

        Returns:
            配置字典
        """
        with self._lock:
            if not self._config_source:
                return {}

            try:
                return self._config_source.get_config()
            except Exception as e:
                logger.error(f"加载插件 {self.plugin_name} 配置出错: {e}")
                return {}

    def start_file_watcher(self) -> None:
        """开始监控配置变化"""
        with self._lock:
            if not self._config_source:
                logger.warning(f"插件 {self.plugin_name} 没有可用的配置源，不启动监控")
                return

            try:
                self._config_source.start_watcher(self._on_config_change)
                logger.debug(f"插件 {self.plugin_name} 配置监控已启动")
            except Exception as e:
                logger.error(f"启动插件 {self.plugin_name} 配置监控失败: {e}")

    def stop_file_watcher(self) -> None:
        """停止监控配置变化"""
        with self._lock:
            if not self._config_source:
                return

            try:
                self._config_source.stop_watcher()
                logger.debug(f"插件 {self.plugin_name} 配置监控已停止")
            except Exception as e:
                logger.error(f"停止插件 {self.plugin_name} 配置监控时出错: {e}")

    def _on_config_change(self) -> None:
        """配置变更内部处理方法"""
        try:
            # 加载新配置
            new_config = self.load_config()

            # 更新实例属性
            self.update_from_dict(new_config)

            # 通知回调函数
            self._notify_config_change(new_config)
        except Exception as e:
            logger.error(f"处理配置变更时出错: {e}")

    def add_config_change_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        添加配置变化回调

        Args:
            callback: 当配置变化时调用的回调函数
        """
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_config_change_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        移除配置变化回调

        Args:
            callback: 要移除的回调函数
        """
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _notify_config_change(self, config: Dict[str, Any]) -> None:
        """
        通知配置变化

        Args:
            config: 新配置
        """
        callbacks = None
        with self._lock:
            # 复制回调列表，避免回调期间修改列表引发问题
            callbacks = list(self._callbacks)

        # 在锁外执行回调，避免死锁
        for callback in callbacks:
            try:
                callback(config)
            except Exception as e:
                logger.error(f"执行配置变化回调时出错: {e}")

    def update_from_dict(self, config_dict: Dict[str, Any]) -> None:
        """
        从字典更新配置属性（子类应重写此方法）

        Args:
            config_dict: 配置字典
        """
        pass


@dataclass
class TypedFileSourcePluginConfig(FileSourcePluginConfig):
    """强类型的插件配置示例基类"""
    plugin_name: str
    config_dir: Optional[str] = None

    def __post_init__(self):
        super().__init__(self.plugin_name, self.config_dir)

    def update_from_dict(self, config_dict: Dict[str, Any]) -> None:
        """
        从字典更新配置属性

        Args:
            config_dict: 配置字典
        """
        with self._lock:
            for key, value in config_dict.items():
                if hasattr(self, key):
                    setattr(self, key, value)
