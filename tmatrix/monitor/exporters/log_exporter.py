import asyncio
import json
import time
import logging
from typing import Dict, Any, Optional

from ..core.metric_core import MetricBackend, MetricValue, logger


class LogExporter(MetricBackend):
    """日志导出后端，将指标输出到日志系统"""

    def __init__(self, logger: Optional[logging.Logger] = None,
                 interval: float = 60.0, level: int = logging.INFO):
        """
        初始化日志导出器

        Args:
            logger: 日志记录器，如果为None则使用根记录器
            interval: 日志记录间隔，单位为秒
            level: 日志级别
        """
        super().__init__()
        self.logger = logger or logging.getLogger("metrics_system.log_exporter")
        self._export_interval = interval
        self.level = level
        self._metrics: Dict[str, MetricValue] = {}
        self._last_export_time = 0
        self._lock = asyncio.Lock()

    async def register_metric(self, metric: MetricValue) -> Any:
        """异步注册一个指标"""
        async with self._lock:
            name = metric.name
            self._metrics[name] = metric
            return metric

    async def _format_metric_value(self, metric: MetricValue) -> Dict[str, Any]:
        """异步格式化指标值为可记录格式"""
        result = {
            'name': metric.name,
            'type': metric.get_type().value,
            'description': metric.description,
            'timestamp': time.time()
        }

        # 如果没有标签，直接记录值
        if not metric.label_names:
            result['value'] = await metric.get_value()
            return result

        # 对于有标签的指标，记录所有标签组合的值
        values = {}
        for label_tuple, value in metric._values.items():
            if not label_tuple:
                continue

            # 使用标签值作为键
            label_key = ','.join(str(v) for v in label_tuple)
            values[label_key] = value

        result['values'] = values
        return result

    async def export_metrics(self, metrics: Dict[str, MetricValue]) -> None:
        """异步导出指标到日志"""
        current_time = time.time()

        async with self._lock:
            # 更新本地指标记录
            self._metrics.update(metrics)

            # 检查是否需要导出
            if current_time - self._last_export_time < self.export_interval:
                return

            # 格式化所有指标
            formatted_metrics = {}
            for name, metric in self._metrics.items():
                formatted_metrics[name] = await self._format_metric_value(metric)

            # 记录到日志
            self.logger.log(
                self.level,
                f"Metrics Export: {json.dumps(formatted_metrics, default=str)}"
            )

            self._last_export_time = current_time

    async def _export_loop(self):
        """异步指标导出循环"""
        while self._running:
            try:
                async with self._lock:
                    # 导出当前所有指标
                    await self.export_metrics(self._metrics)

                await asyncio.sleep(self.export_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error exporting metrics to log: {e}")
                await asyncio.sleep(1)

    @property
    def export_interval(self) -> float:
        """导出间隔，秒"""
        return self._export_interval
