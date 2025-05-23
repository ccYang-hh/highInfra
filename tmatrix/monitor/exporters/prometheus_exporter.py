import asyncio
import aiohttp
from aiohttp import web
import time
import logging
from typing import Dict, Any, Optional

from ..core.metric_core import MetricBackend, MetricValue, MetricType, logger

try:
    from prometheus_client import (
        Counter as PrometheusCounter,
        Gauge as PrometheusGauge,
        Histogram as PrometheusHistogram,
        Summary as PrometheusSummary,
        REGISTRY as DEFAULT_REGISTRY,
        generate_latest
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client library not available. PrometheusExporter will be disabled.")


class PrometheusExporter(MetricBackend):
    """
    Prometheus指标导出后端，将指标导出为Prometheus格式
    """

    def __init__(self, port: int = 8000, addr: str = '', path: str = '/metrics',
                 registry=None, export_interval: float = 10.0):
        """
        初始化Prometheus导出器

        Args:
            port: HTTP服务器端口
            addr: 绑定地址
            path: 指标暴露路径
            registry: Prometheus注册表，如果为None则使用默认注册表
            export_interval: 导出间隔，单位为秒
        """
        if not PROMETHEUS_AVAILABLE:
            raise ImportError("prometheus_client library is required for PrometheusExporter")

        super().__init__()
        self.port = port
        self.addr = addr
        self.path = path
        self.prometheus_registry = registry or DEFAULT_REGISTRY
        self._export_interval = export_interval
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._app = None
        self._runner = None
        self._site = None
        self._lock = asyncio.Lock()

    async def register_metric(self, metric: MetricValue) -> Any:
        """异步注册一个指标到Prometheus"""
        async with self._lock:
            name = metric.name
            if name in self._metrics:
                return self._metrics[name]['prometheus_metric']

            # 创建对应的Prometheus指标
            if metric.get_type() == MetricType.COUNTER:
                prometheus_metric = PrometheusCounter(
                    name, metric.description, metric.label_names, registry=self.prometheus_registry
                )
            elif metric.get_type() == MetricType.GAUGE:
                prometheus_metric = PrometheusGauge(
                    name, metric.description, metric.label_names, registry=self.prometheus_registry
                )
            elif metric.get_type() == MetricType.HISTOGRAM:
                # 如果是直方图，获取桶设置
                buckets = [float('inf')]
                if hasattr(metric, 'buckets'):
                    buckets = metric.buckets

                prometheus_metric = PrometheusHistogram(
                    name, metric.description, metric.label_names, buckets=buckets,
                    registry=self.prometheus_registry
                )
            elif metric.get_type() == MetricType.SUMMARY:
                prometheus_metric = PrometheusSummary(
                    name, metric.description, metric.label_names, registry=self.prometheus_registry
                )
            elif metric.get_type() == MetricType.TIMER:
                # 对于Timer，使用直方图
                prometheus_metric = PrometheusHistogram(
                    f"{name}_seconds", metric.description, metric.label_names,
                    registry=self.prometheus_registry
                )
            else:
                raise ValueError(f"Unsupported metric type: {metric.get_type()}")

            # 存储信息
            self._metrics[name] = {
                'metric': metric,
                'prometheus_metric': prometheus_metric,
                'type': metric.get_type()
            }

            return prometheus_metric

    async def _update_prometheus_metric(self, name: str, prometheus_metric: Any, metric: MetricValue):
        """异步更新Prometheus指标值"""
        metric_type = metric.get_type()
        metric_value = await metric.get_value()

        # 对于没有标签的指标，直接设置值
        if not metric.label_names:
            if metric_type == MetricType.COUNTER:
                # 对于计数器，重置为当前值
                prometheus_metric._value.set(metric_value)
            elif metric_type == MetricType.GAUGE:
                prometheus_metric.set(metric_value)
            elif metric_type == MetricType.HISTOGRAM or metric_type == MetricType.SUMMARY:
                # 对于直方图和摘要，不直接更新，而是通过observe更新
                pass
            elif metric_type == MetricType.TIMER:
                # Timer通过直方图实现，不直接更新
                pass
            return

        # 对于有标签的指标，遍历所有标签组合
        for label_tuple, value in metric._values.items():
            if not label_tuple:
                continue

            # 如果指标对象提供了标签名，使用它们创建标签值字典
            label_values = label_tuple

            if metric_type == MetricType.COUNTER:
                prometheus_metric.labels(*label_values)._value.set(value)
            elif metric_type == MetricType.GAUGE:
                prometheus_metric.labels(*label_values).set(value)
            elif metric_type == MetricType.HISTOGRAM:
                # 对于直方图，值是一个字典
                if isinstance(value, dict) and 'buckets' in value:
                    # 不直接更新桶，而是在采集时更新
                    pass
            elif metric_type == MetricType.SUMMARY:
                # 同样不直接更新
                pass
            elif metric_type == MetricType.TIMER:
                # Timer通过直方图实现
                pass

    async def export_metrics(self, metrics: Dict[str, MetricValue]) -> None:
        """异步导出指标到Prometheus"""
        async with self._lock:
            for name, metric in metrics.items():
                # 确保指标已注册
                if name not in self._metrics:
                    await self.register_metric(metric)

                # 更新指标值
                prometheus_metric = self._metrics[name]['prometheus_metric']
                await self._update_prometheus_metric(name, prometheus_metric, metric)

    async def _metrics_handler(self, request):
        """处理指标请求的HTTP处理器"""
        content = generate_latest(self.prometheus_registry)
        return web.Response(body=content, content_type='text/plain')

    async def start(self) -> None:
        """异步启动Prometheus HTTP服务器"""
        if self._app:
            return

        self._app = web.Application()
        self._app.router.add_get(self.path, self._metrics_handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.addr, self.port)
        await self._site.start()

        logger.info(f"Prometheus exporter started at http://{self.addr or 'localhost'}:{self.port}{self.path}")

        # 启动导出循环
        await super().start()

    async def stop(self) -> None:
        """异步停止Prometheus HTTP服务器"""
        # 停止导出循环
        await super().stop()

        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._app = None

    async def _export_loop(self):
        """异步导出循环"""
        while self._running:
            try:
                # 不需要在这里执行导出，因为每次收集器运行时都会调用export_metrics
                await asyncio.sleep(self.export_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Prometheus export loop: {e}")
                await asyncio.sleep(1)

    @property
    def export_interval(self) -> float:
        """导出间隔，秒"""
        return self._export_interval
