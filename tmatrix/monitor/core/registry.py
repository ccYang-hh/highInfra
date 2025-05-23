import asyncio
import json
import time
import logging
from typing import Dict, List, Any, Optional, Set

from .metric_core import MetricValue, MetricCollector, MetricBackend, Singleton
from .metric_types import Counter, Gauge, Histogram, Summary, Timer

logger = logging.getLogger('metrics_system')


@Singleton
class MetricRegistry:
    """指标注册中心，统一管理所有指标"""

    def __init__(self, default_namespace: str = ""):
        self.default_namespace = default_namespace
        self._metrics: Dict[str, MetricValue] = {}
        self._collectors: List[MetricCollector] = []
        self._backends: List[MetricBackend] = []
        self._lock = asyncio.Lock()

    async def register_metric(self, metric: MetricValue) -> MetricValue:
        """异步注册一个指标"""
        full_name = f"{self.default_namespace}_{metric.name}" if self.default_namespace else metric.name

        async with self._lock:
            if full_name in self._metrics:
                return self._metrics[full_name]

            # 将指标注册到所有后端
            for backend in self._backends:
                await backend.register_metric(metric)

            self._metrics[full_name] = metric
            return metric

    # 同步版本，方便在初始化阶段使用
    def register_metric_sync(self, metric: MetricValue) -> MetricValue:
        """同步注册一个指标（为非异步环境提供）"""
        full_name = f"{self.default_namespace}_{metric.name}" if self.default_namespace else metric.name

        if full_name in self._metrics:
            return self._metrics[full_name]

        self._metrics[full_name] = metric
        return metric

    async def register_collector(self, collector: MetricCollector) -> None:
        """异步注册一个指标收集器"""
        async with self._lock:
            collector.registry = self
            self._collectors.append(collector)
            await collector.start()

    # 同步版本
    def register_collector_sync(self, collector: MetricCollector) -> None:
        """同步注册一个指标收集器"""
        collector.registry = self
        self._collectors.append(collector)

    async def register_backend(self, backend: MetricBackend) -> None:
        """异步注册一个指标后端"""
        async with self._lock:
            self._backends.append(backend)

            # 将所有已注册的指标注册到新后端
            for metric in self._metrics.values():
                await backend.register_metric(metric)

            await backend.start()

    # 同步版本
    def register_backend_sync(self, backend: MetricBackend) -> None:
        """同步注册一个指标后端"""
        self._backends.append(backend)

    async def get_metric(self, name: str) -> Optional[MetricValue]:
        """异步获取指定名称的指标"""
        full_name = f"{self.default_namespace}_{name}" if self.default_namespace else name
        async with self._lock:
            return self._metrics.get(full_name)

    # 同步版本
    def get_metric_sync(self, name: str) -> Optional[MetricValue]:
        """同步获取指定名称的指标"""
        full_name = f"{self.default_namespace}_{name}" if self.default_namespace else name
        return self._metrics.get(full_name)

    async def get_all_metrics(self) -> Dict[str, MetricValue]:
        """异步获取所有注册的指标"""
        async with self._lock:
            return self._metrics.copy()

    async def export_all_metrics(self) -> None:
        """异步将所有指标导出到所有后端"""
        metrics = await self.get_all_metrics()
        for backend in self._backends:
            await backend.export_metrics(metrics)

    async def clear_all_metrics(self) -> None:
        """异步清除所有指标的值"""
        async with self._lock:
            for metric in self._metrics.values():
                await metric.clear()

    async def start_all(self) -> None:
        """异步启动所有收集器和后端"""
        async with self._lock:
            # 启动所有收集器
            for collector in self._collectors:
                await collector.start()

            # 启动所有后端
            for backend in self._backends:
                # 注册所有指标到后端
                for metric in self._metrics.values():
                    await backend.register_metric(metric)

                await backend.start()

    async def stop_all(self) -> None:
        """异步关闭所有收集器和后端"""
        async with self._lock:
            # 停止所有收集器
            for collector in self._collectors:
                await collector.stop()

            # 停止所有后端
            for backend in self._backends:
                await backend.stop()

    async def to_dict(self) -> Dict[str, Any]:
        """异步将整个注册中心转换为字典表示"""
        async with self._lock:
            metrics_dict = {}
            for name, metric in self._metrics.items():
                metrics_dict[name] = await metric.to_dict()

            return {
                "metrics": metrics_dict,
                "collectors": [collector.__class__.__name__ for collector in self._collectors],
                "backends": [backend.__class__.__name__ for backend in self._backends]
            }


# 工厂函数，获取全局注册中心
def get_metric_registry(namespace: str = ""):
    """获取或创建全局指标注册中心"""
    return MetricRegistry(namespace)


# 辅助函数，用于创建不同类型的指标
def create_counter(name: str, description: str, label_names: List[str] = None) -> Counter:
    """创建计数器指标"""
    counter = Counter(name, description, label_names)
    registry = get_metric_registry()
    return registry.register_metric_sync(counter)


def create_gauge(name: str, description: str, label_names: List[str] = None) -> Gauge:
    """创建仪表盘指标"""
    gauge = Gauge(name, description, label_names)
    registry = get_metric_registry()
    return registry.register_metric_sync(gauge)


def create_histogram(name: str, description: str, buckets: List[float] = None,
                     label_names: List[str] = None) -> Histogram:
    """创建直方图指标"""
    histogram = Histogram(name, description, buckets, label_names)
    registry = get_metric_registry()
    return registry.register_metric_sync(histogram)


def create_summary(name: str, description: str, label_names: List[str] = None) -> Summary:
    """创建摘要指标"""
    summary = Summary(name, description, label_names)
    registry = get_metric_registry()
    return registry.register_metric_sync(summary)


def create_timer(name: str, description: str, buckets: List[float] = None,
                 label_names: List[str] = None) -> Timer:
    """创建计时器指标"""
    timer = Timer(name, description, buckets, label_names)
    registry = get_metric_registry()
    return registry.register_metric_sync(timer)
