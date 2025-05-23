import json
import yaml
import threading
from pathlib import Path
from typing import Any, Callable, Dict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from tmatrix.common.logging import init_logger
from .base import ConfigSource


logger = init_logger("runtime/config")


class FileConfigSource(ConfigSource):
    """基于文件系统的配置源实现"""

    def __init__(self, source_id: str, config_path: Path):
        """
        初始化文件配置源

        Args:
            source_id: 配置源标识
            config_path: 配置文件路径
        """
        super().__init__(source_id)
        self.config_path = config_path
        self._observer = None
        self._handler = None
        self._on_change = None
        self._lock = threading.RLock()

    def get_config(self) -> Dict[str, Any]:
        """从文件加载配置"""
        if not self.config_path or not self.config_path.exists():
            logger.warning(f"配置文件不存在: {self.config_path}")
            return {}

        try:
            if self.config_path.suffix in ('.yaml', '.yml'):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
            elif self.config_path.suffix == '.json':
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            else:
                logger.warning(f"不支持的配置文件格式: {self.config_path}")
                return {}

            return config or {}

        except Exception as e:
            logger.error(f"加载配置文件出错: {e}")
            return {}

    def start_watcher(self, on_change_callback: Callable[[], None]) -> None:
        """
        开始监控配置文件变化

        Args:
            on_change_callback: 配置变化时的回调函数
        """
        with self._lock:
            if self._observer:
                return  # 已在监控中

            if not self.config_path:
                logger.warning(f"配置文件路径无效，不启动监控")
                return

            try:
                self._on_change = on_change_callback
                self._observer = Observer()

                # 创建文件事件处理器
                self._handler = _FileEventHandler(
                    file_path=self.config_path,
                    on_change=self._on_change
                )

                # 监控文件所在目录
                self._observer.schedule(
                    self._handler,
                    str(self.config_path.parent),
                    recursive=False
                )

                self._observer.start()
                logger.debug(f"开始监控配置文件: {self.config_path}")

            except Exception as e:
                logger.error(f"启动配置文件监控失败: {e}")
                self._cleanup_resources()

    def stop_watcher(self) -> None:
        """停止监控配置文件变化"""
        with self._lock:
            self._cleanup_resources()
            logger.debug(f"停止监控配置文件: {self.config_path}")

    def _cleanup_resources(self) -> None:
        """清理监控资源"""
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception as e:
                logger.error(f"停止文件监控时出错: {e}")
            finally:
                self._observer = None
                self._handler = None
                self._on_change = None


class _FileEventHandler(FileSystemEventHandler):
    """Watchdog文件事件处理器"""

    def __init__(self, file_path: Path, on_change: Callable[[], None]):
        self.file_path = file_path
        self.on_change = on_change

    def on_modified(self, event):
        if not event.is_directory and Path(event.src_path) == self.file_path:
            logger.info(f"检测到文件变化: {event.src_path}")
            self.on_change()
