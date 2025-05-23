import time
import asyncio
import threading
from enum import Enum
from dataclasses import dataclass
from typing import Any, Callable, Optional, Dict

from tmatrix.common.logging import init_logger

logger = init_logger("runtime/utils")


class ConfigEvent:
    """配置变更事件类"""

    def __init__(self, source: str, config: Any):
        """
        初始化配置变更事件

        Args:
            source: 事件源标识 (如 "runtime" 或 "plugins/router")
            config: 新的配置对象
        """
        self.source = source
        self.config = config
        self.timestamp = time.time()

    def __str__(self) -> str:
        return f"ConfigEvent(source={self.source}, timestamp={self.timestamp})"


class PluginEventType(str, Enum):
    """插件相关事件类型"""
    REGISTERED = "plugin.registered"    # 插件注册
    LOADED = "plugin.loaded"            # 插件加载
    INITIALIZED = "plugin.initialized"  # 插件初始化
    STARTED = "plugin.started"          # 插件启动
    STOPPED = "plugin.stopped"          # 插件停止
    UNLOADED = "plugin.unloaded"        # 插件卸载
    CONFIG_UPDATED = "plugin.config_updated"  # 插件配置更新
    ERROR = "plugin.error"              # 插件错误


@dataclass
class PluginEvent:
    """插件事件数据"""
    plugin_name: str
    event_type: PluginEventType
    data: Optional[Dict[str, Any]] = None


class EventBus:
    """事件总线，用于跨模块事件分发"""

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(EventBus, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        """初始化事件总线"""
        self._subscribers = {}  # 类型: {事件类型: [回调函数, ...]}
        self._callbacks_lock = threading.RLock()

        # 异步支持
        self._loop = None  # 异步事件循环
        self._async_subscribers = {}  # 异步订阅者

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """
        订阅事件

        Args:
            event_type: 事件类型，支持通配符 "*"
            callback: 回调函数，接收一个事件对象作为参数
        """
        with self._callbacks_lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """
        取消订阅

        Args:
            event_type: 事件类型
            callback: 要取消的回调函数
        """
        with self._callbacks_lock:
            if event_type in self._subscribers:
                if callback in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(callback)
                # 如果没有更多订阅者，删除事件类型
                if not self._subscribers[event_type]:
                    del self._subscribers[event_type]

    def publish(self, event_type: str, event: Any) -> None:
        """
        发布事件

        Args:
            event_type: 事件类型
            event: 事件对象
        """
        # 获取所有匹配的回调函数
        callbacks = []

        with self._callbacks_lock:
            # 精确匹配
            if event_type in self._subscribers:
                callbacks.extend(self._subscribers[event_type])

            # 通配符匹配
            if "*" in self._subscribers:
                callbacks.extend(self._subscribers["*"])

        # 调用所有匹配的回调
        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"事件处理器执行异常: {e}")

        # 处理异步订阅者
        self._publish_async(event_type, event)

    # ====== 异步支持 ======

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置异步事件循环"""
        self._loop = loop

    async def subscribe_async(self, event_type: str, callback: Callable) -> None:
        """异步订阅事件"""
        if not asyncio.iscoroutinefunction(callback):
            raise TypeError("回调必须是协程函数")

        if self._loop is None:
            raise RuntimeError("没有设置事件循环，请先调用 set_event_loop")

        if event_type not in self._async_subscribers:
            self._async_subscribers[event_type] = []
        if callback not in self._async_subscribers[event_type]:
            self._async_subscribers[event_type].append(callback)

    async def unsubscribe_async(self, event_type: str, callback: Callable) -> None:
        """取消异步订阅"""
        if event_type in self._async_subscribers:
            if callback in self._async_subscribers[event_type]:
                self._async_subscribers[event_type].remove(callback)
            if not self._async_subscribers[event_type]:
                del self._async_subscribers[event_type]

    def _publish_async(self, event_type: str, event: Any) -> None:
        """发布到异步订阅者"""
        if self._loop is None:
            return

        # 获取匹配的异步回调
        async_callbacks = []

        # 精确匹配
        if event_type in self._async_subscribers:
            async_callbacks.extend(self._async_subscribers[event_type])

        # 通配符匹配
        if "*" in self._async_subscribers:
            async_callbacks.extend(self._async_subscribers["*"])

        # 调度异步回调
        for callback in async_callbacks:
            asyncio.run_coroutine_threadsafe(
                self._safe_async_callback(callback, event),
                self._loop
            )

    async def _safe_async_callback(self, callback: Callable, event: Any) -> None:
        """安全执行异步回调"""
        try:
            await callback(event)
        except Exception as e:
            logger.error(f"异步事件处理器异常: {e}")
