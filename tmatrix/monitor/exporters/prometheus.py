"""
Prometheus导出器
将收集的指标转换为Prometheus格式并提供HTTP接口
"""
import asyncio
import time
from typing import Dict, List, Any, Optional
from aiohttp import web, ClientSession
import prometheus_client
from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily

from ..core.base import MetricExporter, MetricValue, MetricType

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/exporters")


class PrometheusExporter(MetricExporter):
    """
    Prometheus导出器
    提供HTTP接口，以Prometheus格式暴露指标

    ┌─────────────────┐    定期调用export()      ┌──────────────────┐
    │   指标收集器      │ ─────────────────────→ │PrometheusExporter│
    │  (Collector)    │                        │                  │
    └─────────────────┘                        └──────────────────┘
                                                        │
                                                   启动HTTP服务器
                                                  暴露 /metrics 端点
                                                        │
                                                        ▼
    ┌─────────────────┐      HTTP GET请求      ┌──────────────────┐
    │ Prometheus服务器 │ ◄───────────────────── │     HTTP服务器    │
    │                 │      /metrics          │     (aiohttp)    │
    └─────────────────┘                        └──────────────────┘
    """

    def __init__(self, port: int = 8090, host: str = '0.0.0.0',
                 path: str = '/metrics', export_interval: float = 10.0):
        """
        初始化Prometheus导出器

        Args:
            port: HTTP服务端口
            host: 绑定地址
            path: 指标路径
            export_interval: 导出间隔（秒）
        """
        super().__init__(name='prometheus_exporter', export_interval=export_interval)
        self.port = port
        self.host = host
        self.path = path

        # HTTP服务相关
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # Prometheus注册表
        self._registry = CollectorRegistry()

        # 存储最新的指标数据
        self._latest_metrics: Dict[str, List[MetricValue]] = {}
        self._metrics_lock = asyncio.Lock()

        # 上次导出时间
        self._last_export_time = None

    async def start(self):
        """启动导出器和HTTP服务"""
        # 启动HTTP服务器
        await self._start_http_server()

        # 启动导出循环
        await super().start()

    async def stop(self):
        """停止导出器和HTTP服务"""
        # 停止导出循环
        await super().stop()

        # 停止HTTP服务器
        await self._stop_http_server()

    async def export(self, metrics: Dict[str, List[MetricValue]]) -> None:
        """
        导出指标（更新内部存储）

        Args:
            metrics: 按收集器分组的指标数据
        """
        async with self._metrics_lock:
            self._latest_metrics = metrics.copy()
            self._last_export_time = time.time()

    async def _start_http_server(self):
        """启动HTTP服务器"""
        if self._app is not None:
            return

        self._app = web.Application()
        self._app.router.add_get(self.path, self._metrics_handler)
        self._app.router.add_get('/health', self._health_handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        logger.info(f"Prometheus exporter HTTP server started at http://{self.host}:{self.port}{self.path}")

    async def _stop_http_server(self):
        """停止HTTP服务器"""
        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._app = None

    async def _metrics_handler(self, request) -> web.Response:
        """处理指标请求"""
        try:
            # 获取最新的指标数据
            async with self._metrics_lock:
                metrics = self._latest_metrics.copy()

            # 转换为Prometheus格式
            output = self._convert_to_prometheus_format(metrics)

            return web.Response(text=output, content_type='text/plain')

        except Exception as e:
            logger.error(f"Error handling metrics request: {e}")
            return web.Response(status=500, text=str(e))

    async def _health_handler(self, request) -> web.Response:
        """健康检查处理器"""
        return web.json_response({
            'status': 'ok',
            'last_export_time': self._last_export_time,
            'metrics_count': len(self._latest_metrics.items()) if self._latest_metrics else 0
        })

    @staticmethod
    def _convert_to_prometheus_format(metrics: Dict[str, List[MetricValue]]) -> str:
        """
        将指标转换为Prometheus文本格式

        Args:
            metrics: 指标数据

        Returns:
            Prometheus格式的文本
        """
        # 创建临时注册表
        registry = CollectorRegistry()

        # 按指标名称分组
        metrics_by_name: Dict[str, List[MetricValue]] = {}
        for collector_metrics in metrics.values():
            for metric in collector_metrics:
                if metric.name not in metrics_by_name:
                    metrics_by_name[metric.name] = []
                metrics_by_name[metric.name].append(metric)

        # 转换每个指标
        for metric_name, metric_values in metrics_by_name.items():
            if not metric_values:
                continue

            # 使用第一个值确定类型和描述
            first_metric = metric_values[0]
            metric_type = first_metric.metric_type
            description = first_metric.description or metric_name

            # 根据类型创建相应的Prometheus指标
            if metric_type == MetricType.GAUGE:
                gauge = Gauge(metric_name, description,
                              list(first_metric.labels.keys()),
                              registry=registry)
                for mv in metric_values:
                    gauge.labels(**mv.labels).set(mv.value)

            elif metric_type == MetricType.COUNTER:
                counter = Counter(metric_name, description,
                                  list(first_metric.labels.keys()),
                                  registry=registry)
                for mv in metric_values:
                    # Counter只能增加，所以我们直接设置内部值
                    counter.labels(**mv.labels)._value.set(mv.value)

            elif metric_type == MetricType.HISTOGRAM:
                from prometheus_client.registry import Collector
                from prometheus_client.core import Metric

                histogram_configs = {
                    'vllm:time_to_first_token_seconds': [0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5,
                                                         0.75,
                                                         1.0, 2.5, 5.0, 7.5, 10.0, 20.0, 40.0, 80.0, 160.0, 640.0,
                                                         2560.0],
                    'vllm:time_per_output_token_seconds': [0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5,
                                                           0.75,
                                                           1.0, 2.5, 5.0, 7.5, 10.0, 20.0, 40.0, 80.0],
                    'vllm:e2e_request_latency_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0,
                                                         40.0, 50.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
                    'vllm:request_queue_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0,
                                                        40.0,
                                                        50.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
                    'vllm:request_inference_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0,
                                                            30.0,
                                                            40.0, 50.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1920.0,
                                                            7680.0],
                    'vllm:request_prefill_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0,
                                                          30.0,
                                                          40.0, 50.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
                    'vllm:request_decode_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0,
                                                         40.0, 50.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0]
                }

                # 获取基础指标名称
                base_metric_name = metric_name
                for suffix in ['_bucket', '_sum', '_count']:
                    if base_metric_name.endswith(suffix):
                        base_metric_name = base_metric_name[:-len(suffix)]
                        break

                # prometheus_name = base_metric_name.replace(':', '_')

                # 只在处理_bucket指标时创建histogram，跳过_sum和_count
                if not metric_name.endswith('_bucket'):
                    continue  # 跳过，只处理bucket指标

                label_names = [k for k in first_metric.labels.keys() if k != 'le']

                # 收集所有相关指标的数据（包括_bucket, _sum, _count）
                data_by_labels = {}
                for mv in metric_values:
                    if '_bucket' in mv.name:
                        clean_labels = {k: v for k, v in mv.labels.items() if k != 'le'}
                        labels_key = tuple(sorted(clean_labels.items()))

                        if labels_key not in data_by_labels:
                            data_by_labels[labels_key] = {'buckets': {}, 'sum': 0, 'count': 0}

                        le_value = mv.labels.get('le', '+Inf')
                        data_by_labels[labels_key]['buckets'][le_value] = mv.value

                # ========== 新增：收集_sum指标的值 ==========
                sum_metric_name = base_metric_name + '_sum'
                if sum_metric_name in metrics_by_name:
                    for mv in metrics_by_name[sum_metric_name]:
                        if mv.metric_type == MetricType.HISTOGRAM:
                            clean_labels = {k: v for k, v in mv.labels.items()}
                            labels_key = tuple(sorted(clean_labels.items()))

                            if labels_key in data_by_labels:
                                data_by_labels[labels_key]['sum'] = mv.value
                # ========== 新增结束 ==========

                # 从+Inf bucket自动计算count，sum设为0
                for labels_tuple, data in data_by_labels.items():
                    # 从+Inf bucket获取总count
                    data['count'] = data['buckets'].get('+Inf', 0)
                    # sum设为0（如果需要真实sum值，需要从原始数据中获取）
                    data['sum'] = 0

                # 创建自定义的Histogram collector
                class CustomHistogram(Collector):
                    def __init__(self, name, documentation, labelnames, data_by_labels, buckets):
                        self._name = name
                        self._documentation = documentation
                        self._labelnames = labelnames or []
                        self._data = data_by_labels
                        self._buckets = buckets

                    def collect(self):
                        metric = Metric(self._name, self._documentation, 'histogram')

                        # 添加bucket样本
                        for labels_tuple, data in self._data.items():
                            labels_dict = dict(labels_tuple)

                            # 添加bucket样本
                            for bucket_le in self._buckets:
                                le_str = '+Inf' if bucket_le == float('inf') else str(bucket_le)
                                bucket_value = data['buckets'].get(le_str, 0)

                                bucket_labels = dict(labels_dict)
                                bucket_labels['le'] = le_str
                                metric.add_sample(f'{self._name}_bucket', labels=bucket_labels, value=bucket_value)

                            # 添加count和sum样本
                            metric.add_sample(f'{self._name}_count', labels=labels_dict, value=data['count'])
                            metric.add_sample(f'{self._name}_sum', labels=labels_dict, value=data['sum'])

                        yield metric

                    def describe(self):
                        return []

                # 获取buckets配置
                buckets = histogram_configs.get(base_metric_name)
                if not buckets:
                    buckets = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]

                # 确保包含+inf
                if float('inf') not in buckets:
                    buckets = list(buckets) + [float('inf')]

                # 创建并注册自定义histogram
                custom_histogram = CustomHistogram(
                    base_metric_name,
                    description,
                    label_names,
                    data_by_labels,
                    buckets
                )

                try:
                    registry.register(custom_histogram)
                except ValueError as e:
                    if "Duplicated timeseries" in str(e):
                        logger.warning(f"Histogram {base_metric_name} already registered, skipping")
                    else:
                        logger.error(f"Failed to register histogram {base_metric_name}: {e}")

        # 生成Prometheus格式文本
        return generate_latest(registry).decode('utf-8')
