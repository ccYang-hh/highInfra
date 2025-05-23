from abc import ABC, abstractmethod
from enum import Enum
import asyncio
import time
from typing import Dict, List, Any, Optional, Set, TypeVar, Generic, Tuple, Union
from dataclasses import dataclass, field
import json
import logging
import traceback

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('metrics_system')


# 单例模式装饰器
class Singleton:
    """单例模式装饰器"""

    def __init__(self, cls):
        self._cls = cls
        self._instance = None

    def __call__(self, *args, **kwargs):
        if self._instance is None:
            self._instance = self._cls(*args, **kwargs)
        return self._instance


class MetricType(Enum):
    """指标类型枚举"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    TIMER = "timer"


@dataclass
class LabelValues:
    """标签值容器，提供高效的标签值管理"""
    values: Dict[str, str] = field(default_factory=dict)

    def as_tuple(self, keys: List[str]) -> tuple:
        """将标签值转换为有序元组，用于高效查找"""
        return tuple(self.values.get(k, "") for k in keys)

    def with_additional(self, **kwargs) -> 'LabelValues':
        """创建包含额外标签的新实例"""
        new_values = self.values.copy()
        new_values.update(kwargs)
        return LabelValues(new_values)

    def to_dict(self) -> Dict[str, str]:
        """返回标签值字典"""
        return self.values.copy()


T = TypeVar('T')


class MetricValue(ABC, Generic[T]):
    """
    指标值抽象基类，所有指标类型的基础
    """

    def __init__(self, name: str, description: str, label_names: List[str] = None):
        self.name = name
        self.description = description
        self.label_names = label_names or []
        self._values: Dict[tuple, T] = {}
        self._lock = asyncio.Lock()  # 异步锁保证线程安全

    @abstractmethod
    def get_type(self) -> MetricType:
        """获取指标类型"""
        pass

    @abstractmethod
    async def get_value(self, labels: Optional[LabelValues] = None) -> Any:
        """异步获取当前指标值"""
        pass

    def _get_label_tuple(self, labels: Optional[LabelValues]) -> tuple:
        """将标签值转换为元组作为字典键"""
        if not labels:
            return tuple()
        return labels.as_tuple(self.label_names)

    async def clear(self):
        """异步清除所有指标值"""
        async with self._lock:
            self._values.clear()

    async def to_dict(self) -> Dict[str, Any]:
        """异步将指标转换为字典表示"""
        async with self._lock:
            result = {
                "name": self.name,
                "type": self.get_type().value,
                "description": self.description,
                "label_names": self.label_names
            }

            values_dict = {}
            for label_tuple, value in self._values.items():
                # 创建标签字典用于JSON
                if self.label_names:
                    labels = {
                        name: label_tuple[i] if i < len(label_tuple) else ""
                        for i, name in enumerate(self.label_names)
                    }
                    key = json.dumps(labels)
                else:
                    key = "default"

                # 根据值类型进行处理
                if isinstance(value, dict):
                    values_dict[key] = value
                else:
                    values_dict[key] = value

            result["values"] = values_dict
            return result


class MetricCollector(ABC):
    """指标收集器抽象基类"""

    def __init__(self, registry=None):
        self.registry = registry
        self._running = False
        self._task = None

    @abstractmethod
    async def collect(self) -> Dict[str, Any]:
        """异步收集指标并返回"""
        pass

    async def start(self):
        """异步启动收集器"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._collection_loop())
        logger.info(f"Started collector: {self.__class__.__name__}")

    async def stop(self):
        """异步停止收集器"""
        if not self._running:
            return

        self._running = False
        if self._task:
            try:
                self._task.cancel()
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error stopping collector {self.__class__.__name__}: {e}")
                logger.debug(traceback.format_exc())

        logger.info(f"Stopped collector: {self.__class__.__name__}")

    async def _collection_loop(self):
        """收集循环的默认实现，子类可以覆盖"""
        while self._running:
            try:
                await self.collect()
                await asyncio.sleep(self.collection_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in collection loop for {self.__class__.__name__}: {e}")
                logger.debug(traceback.format_exc())
                await asyncio.sleep(1)  # 出错后短暂暂停，避免过快重试

    @property
    def collection_interval(self) -> float:
        """收集间隔，秒"""
        return 5.0  # 默认5秒，子类应该覆盖


class MetricBackend(ABC):
    """指标后端抽象类"""

    def __init__(self):
        self._running = False
        self._task = None

    @abstractmethod
    async def register_metric(self, metric: MetricValue) -> Any:
        """异步注册一个指标"""
        pass

    @abstractmethod
    async def export_metrics(self, metrics: Dict[str, MetricValue]) -> None:
        """异步导出所有指标"""
        pass

    async def start(self) -> None:
        """异步启动后端服务"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._export_loop())
        logger.info(f"Started backend: {self.__class__.__name__}")

    async def stop(self) -> None:
        """异步停止后端服务"""
        if not self._running:
            return

        self._running = False
        if self._task:
            try:
                self._task.cancel()
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error stopping backend {self.__class__.__name__}: {e}")
                logger.debug(traceback.format_exc())

        logger.info(f"Stopped backend: {self.__class__.__name__}")

    async def _export_loop(self):
        """导出循环的默认实现，子类可以覆盖"""
        while self._running:
            try:
                # 在子类中实现具体的导出逻辑
                await asyncio.sleep(self.export_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in export loop for {self.__class__.__name__}: {e}")
                logger.debug(traceback.format_exc())
                await asyncio.sleep(1)  # 出错后短暂暂停，避免过快重试

    @property
    def export_interval(self) -> float:
        """导出间隔，秒"""
        return 10.0  # 默认10秒，子类应该覆盖
