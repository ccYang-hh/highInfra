"""
指标注册中心
统一管理所有收集器收集到的指标
"""
import asyncio
import time
from typing import Dict, List, Any
from collections import defaultdict

from .base import MetricValue

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/core")


class MetricRegistry:
    """
    指标注册中心（单例模式）
    负责存储和管理所有收集到的指标
    """
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MetricRegistry, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # 按收集器名称分组存储指标
        self._metrics: Dict[str, List[MetricValue]] = defaultdict(list)

        # 指标更新时间戳
        self._update_times: Dict[str, float] = {}

        # 保护并发访问的锁
        self._metrics_lock = asyncio.Lock()

        self._initialized = True
        logger.info("MetricRegistry initialized")

    async def update_metrics(self, collector_name: str, metrics: List[MetricValue]):
        """
        更新指定收集器的指标

        Args:
            collector_name: 收集器名称
            metrics: 指标列表
        """
        async with self._metrics_lock:
            # 替换该收集器的所有指标（而不是追加）
            self._metrics[collector_name] = metrics
            self._update_times[collector_name] = time.time()

            logger.debug(f"Updated {len(metrics)} metrics from collector {collector_name}")

    async def get_all_metrics(self) -> Dict[str, List[MetricValue]]:
        """
        获取所有指标

        Returns:
            按收集器名称分组的指标字典
        """
        async with self._metrics_lock:
            # 返回深拷贝，避免外部修改
            return dict(self._metrics)

    async def get_collector_metrics(self, collector_name: str) -> List[MetricValue]:
        """
        获取指定收集器的指标

        Args:
            collector_name: 收集器名称

        Returns:
            指标列表
        """
        async with self._metrics_lock:
            return self._metrics.get(collector_name, []).copy()

    async def get_metric_by_name(self, metric_name: str) -> List[MetricValue]:
        """
        根据指标名称获取所有匹配的指标

        Args:
            metric_name: 指标名称

        Returns:
            匹配的指标列表
        """
        async with self._metrics_lock:
            result = []
            for metrics in self._metrics.values():
                for metric in metrics:
                    if metric.name == metric_name:
                        result.append(metric)
            return result

    async def clear(self):
        """清空所有指标"""
        async with self._metrics_lock:
            self._metrics.clear()
            self._update_times.clear()
            logger.info("Cleared all metrics from registry")

    def get_stats(self) -> Dict[str, Any]:
        """获取注册中心的统计信息"""
        total_metrics = sum(len(metrics) for metrics in self._metrics.values())
        return {
            "total_collectors": len(self._metrics),
            "total_metrics": total_metrics,
            "update_times": dict(self._update_times)
        }
