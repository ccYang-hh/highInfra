import time
import asyncio
import contextlib
import functools
from typing import Dict, List, Any, Optional, Callable, Union, TypeVar, Awaitable

from .metric_core import MetricType, MetricValue, LabelValues, logger

T = TypeVar('T')
F = TypeVar('F', bound=Callable[..., Any])


class Counter(MetricValue[float]):
    """计数器指标，只能增加不能减少"""

    def __init__(self, name: str, description: str, label_names: List[str] = None):
        super().__init__(name, description, label_names)

    def get_type(self) -> MetricType:
        return MetricType.COUNTER

    async def inc(self, amount: float = 1.0, labels: Optional[LabelValues] = None) -> None:
        """异步递增计数器"""
        if amount < 0:
            raise ValueError("Counter can only be incremented by non-negative amounts")

        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            if label_tuple not in self._values:
                self._values[label_tuple] = 0.0
            self._values[label_tuple] += amount

    # 同步版本，方便在非异步环境中使用
    def inc_sync(self, amount: float = 1.0, labels: Optional[LabelValues] = None) -> None:
        """同步递增计数器（为非异步环境提供）"""
        if amount < 0:
            raise ValueError("Counter can only be incremented by non-negative amounts")

        label_tuple = self._get_label_tuple(labels)
        if label_tuple not in self._values:
            self._values[label_tuple] = 0.0
        self._values[label_tuple] += amount

    async def get_value(self, labels: Optional[LabelValues] = None) -> float:
        """异步获取当前计数"""
        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            return self._values.get(label_tuple, 0.0)


class Gauge(MetricValue[float]):
    """仪表盘指标，可以任意增加或减少"""

    def __init__(self, name: str, description: str, label_names: List[str] = None):
        super().__init__(name, description, label_names)

    def get_type(self) -> MetricType:
        return MetricType.GAUGE

    async def set(self, value: float, labels: Optional[LabelValues] = None) -> None:
        """异步设置仪表盘值"""
        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            self._values[label_tuple] = value

    # 同步版本，方便在非异步环境中使用
    def set_sync(self, value: float, labels: Optional[LabelValues] = None) -> None:
        """同步设置仪表盘值（为非异步环境提供）"""
        label_tuple = self._get_label_tuple(labels)
        self._values[label_tuple] = value

    async def inc(self, amount: float = 1.0, labels: Optional[LabelValues] = None) -> None:
        """异步递增仪表盘值"""
        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            if label_tuple not in self._values:
                self._values[label_tuple] = 0.0
            self._values[label_tuple] += amount

    # 同步版本
    def inc_sync(self, amount: float = 1.0, labels: Optional[LabelValues] = None) -> None:
        """同步递增仪表盘值"""
        label_tuple = self._get_label_tuple(labels)
        if label_tuple not in self._values:
            self._values[label_tuple] = 0.0
        self._values[label_tuple] += amount

    async def dec(self, amount: float = 1.0, labels: Optional[LabelValues] = None) -> None:
        """异步递减仪表盘值"""
        await self.inc(-amount, labels)

    # 同步版本
    def dec_sync(self, amount: float = 1.0, labels: Optional[LabelValues] = None) -> None:
        """同步递减仪表盘值"""
        self.inc_sync(-amount, labels)

    async def set_to_current_time(self, labels: Optional[LabelValues] = None) -> None:
        """异步设置为当前时间戳"""
        await self.set(time.time(), labels)

    # 异步上下文管理器，用于跟踪进行中操作
    @contextlib.asynccontextmanager
    async def track_inprogress(self, labels: Optional[LabelValues] = None):
        """异步跟踪进行中的操作"""
        await self.inc(1, labels)
        try:
            yield
        finally:
            await self.dec(1, labels)

    async def get_value(self, labels: Optional[LabelValues] = None) -> float:
        """异步获取当前值"""
        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            return self._values.get(label_tuple, 0.0)


class Histogram(MetricValue[Dict[str, Any]]):
    """直方图指标，观察值分布"""

    def __init__(self, name: str, description: str, buckets: List[float] = None,
                 label_names: List[str] = None):
        super().__init__(name, description, label_names)
        # 默认桶
        self.buckets = sorted(buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10])
        # 确保有无穷大桶
        if self.buckets[-1] != float('inf'):
            self.buckets.append(float('inf'))

    def get_type(self) -> MetricType:
        return MetricType.HISTOGRAM

    async def observe(self, value: float, labels: Optional[LabelValues] = None) -> None:
        """异步记录一个观察值"""
        label_tuple = self._get_label_tuple(labels)

        async with self._lock:
            if label_tuple not in self._values:
                # 初始化所有桶和统计值
                self._values[label_tuple] = {
                    'count': 0,
                    'sum': 0.0,
                    'buckets': {b: 0 for b in self.buckets}
                }

            data = self._values[label_tuple]
            data['count'] += 1
            data['sum'] += value

            # 更新落入各个桶的计数
            for bucket in self.buckets:
                if value <= bucket:
                    data['buckets'][bucket] += 1

    # 同步版本
    def observe_sync(self, value: float, labels: Optional[LabelValues] = None) -> None:
        """同步记录一个观察值（为非异步环境提供）"""
        label_tuple = self._get_label_tuple(labels)

        if label_tuple not in self._values:
            # 初始化所有桶和统计值
            self._values[label_tuple] = {
                'count': 0,
                'sum': 0.0,
                'buckets': {b: 0 for b in self.buckets}
            }

        data = self._values[label_tuple]
        data['count'] += 1
        data['sum'] += value

        # 更新落入各个桶的计数
        for bucket in self.buckets:
            if value <= bucket:
                data['buckets'][bucket] += 1

    # 异步上下文管理器，用于测量时间
    @contextlib.asynccontextmanager
    async def time(self, labels: Optional[LabelValues] = None):
        """异步测量代码块执行时间"""
        start_time = time.time()
        try:
            yield
        finally:
            await self.observe(time.time() - start_time, labels)

    async def get_value(self, labels: Optional[LabelValues] = None) -> Dict[str, Any]:
        """异步获取当前直方图数据"""
        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            if label_tuple not in self._values:
                return {
                    'count': 0,
                    'sum': 0.0,
                    'buckets': {b: 0 for b in self.buckets}
                }
            return self._values[label_tuple].copy()


class Summary(MetricValue[Dict[str, float]]):
    """摘要指标，跟踪值的分布（不带桶）"""

    def __init__(self, name: str, description: str, label_names: List[str] = None):
        super().__init__(name, description, label_names)

    def get_type(self) -> MetricType:
        return MetricType.SUMMARY

    async def observe(self, value: float, labels: Optional[LabelValues] = None) -> None:
        """异步记录一个观察值"""
        label_tuple = self._get_label_tuple(labels)

        async with self._lock:
            if label_tuple not in self._values:
                self._values[label_tuple] = {'count': 0, 'sum': 0.0}

            self._values[label_tuple]['count'] += 1
            self._values[label_tuple]['sum'] += value

    # 同步版本
    def observe_sync(self, value: float, labels: Optional[LabelValues] = None) -> None:
        """同步记录一个观察值"""
        label_tuple = self._get_label_tuple(labels)

        if label_tuple not in self._values:
            self._values[label_tuple] = {'count': 0, 'sum': 0.0}

        self._values[label_tuple]['count'] += 1
        self._values[label_tuple]['sum'] += value

    # 异步上下文管理器，用于测量时间
    @contextlib.asynccontextmanager
    async def time(self, labels: Optional[LabelValues] = None):
        """异步测量代码块执行时间"""
        start_time = time.time()
        try:
            yield
        finally:
            await self.observe(time.time() - start_time, labels)

    async def get_value(self, labels: Optional[LabelValues] = None) -> Dict[str, float]:
        """异步获取当前摘要数据"""
        label_tuple = self._get_label_tuple(labels)
        async with self._lock:
            if label_tuple not in self._values:
                return {'count': 0, 'sum': 0.0}
            return self._values[label_tuple].copy()


class Timer(MetricValue[Dict[str, Any]]):
    """计时器指标，专门用于测量执行时间，内部使用直方图"""

    def __init__(self, name: str, description: str, buckets: List[float] = None,
                 label_names: List[str] = None):
        super().__init__(name, description, label_names)
        self._histogram = Histogram(f"{name}_seconds", description, buckets, label_names)

    def get_type(self) -> MetricType:
        return MetricType.TIMER

    async def record(self, seconds: float, labels: Optional[LabelValues] = None) -> None:
        """异步记录执行时间"""
        await self._histogram.observe(seconds, labels)

    # 同步版本
    def record_sync(self, seconds: float, labels: Optional[LabelValues] = None) -> None:
        """同步记录执行时间"""
        self._histogram.observe_sync(seconds, labels)

    # 异步上下文管理器，用于测量时间
    @contextlib.asynccontextmanager
    async def time(self, labels: Optional[LabelValues] = None):
        """异步测量代码块执行时间"""
        async with self._histogram.time(labels):
            yield

    # 装饰器，用于测量异步函数执行时间
    def time_function(self, labels: Optional[LabelValues] = None):
        """异步函数计时装饰器"""

        def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
            @functools.wraps(func)
            async def wrapper(*args, **kwargs) -> T:
                start_time = time.time()
                try:
                    return await func(*args, **kwargs)
                finally:
                    await self.record(time.time() - start_time, labels)

            return wrapper

        return decorator

    # 装饰器，用于测量同步函数执行时间
    def time_function_sync(self, labels: Optional[LabelValues] = None):
        """同步函数计时装饰器"""

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @functools.wraps(func)
            def wrapper(*args, **kwargs) -> T:
                start_time = time.time()
                try:
                    return func(*args, **kwargs)
                finally:
                    self.record_sync(time.time() - start_time, labels)

            return wrapper

        return decorator

    async def get_value(self, labels: Optional[LabelValues] = None) -> Dict[str, Any]:
        """异步获取计时器数据"""
        return await self._histogram.get_value(labels)
