"""
日志导出器
将指标数据输出到日志文件，便于后续分析和调试
"""
import asyncio
import json
import time
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

from ..core.base import MetricExporter, MetricValue

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/exporters")


class LogExporter(MetricExporter):
    """
    日志导出器
    将指标数据格式化后写入日志文件
    """

    def __init__(self, log_file: str = 'metrics.log',
                 export_interval: float = 60.0,
                 max_file_size: int = 100 * 1024 * 1024,  # 100MB
                 backup_count: int = 5):
        """
        初始化日志导出器

        Args:
            log_file: 日志文件路径
            export_interval: 导出间隔（秒）
            max_file_size: 最大文件大小（字节）
            backup_count: 保留的备份文件数量
        """
        super().__init__(name='log_exporter', export_interval=export_interval)
        self.log_file = Path(log_file)
        self.max_file_size = max_file_size
        self.backup_count = backup_count

        # 创建专用的日志器
        self._metrics_logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """设置专用的指标日志器"""
        # 创建日志器
        metrics_logger = logging.getLogger('monitor/metrics/data')
        metrics_logger.setLevel(logging.INFO)

        # 避免重复添加处理器
        if metrics_logger.handlers:
            return metrics_logger

        # 创建日志目录
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        # 创建轮转文件处理器
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(
            self.log_file,
            maxBytes=self.max_file_size,
            backupCount=self.backup_count,
            encoding='utf-8'
        )

        # 设置格式
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)

        metrics_logger.addHandler(handler)

        # 防止传播到根日志器
        metrics_logger.propagate = False

        return metrics_logger

    async def export(self, metrics: Dict[str, List[MetricValue]]) -> None:
        """
        导出指标到日志文件

        Args:
            metrics: 按收集器分组的指标
        """
        try:
            # 构建导出数据
            export_data = {
                'timestamp': time.time(),
                'export_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                'collectors': {}
            }

            # 按收集器组织数据
            total_metrics = 0
            for collector_name, metric_list in metrics.items():
                collector_data = {
                    'metric_count': len(metric_list),
                    'metrics': []
                }

                # 转换每个指标
                for metric in metric_list:
                    metric_data = {
                        'name': metric.name,
                        'value': metric.value,
                        'type': metric.metric_type.value,
                        'labels': metric.labels,
                        'timestamp': metric.timestamp,
                        'description': metric.description,
                        'unit': metric.unit
                    }
                    collector_data['metrics'].append(metric_data)

                export_data['collectors'][collector_name] = collector_data
                total_metrics += len(metric_list)

            export_data['total_metrics'] = total_metrics

            # 写入日志
            log_message = f"METRICS_EXPORT: {json.dumps(export_data, ensure_ascii=False, separators=(',', ':'))}"
            self._metrics_logger.info(log_message)

            logger.debug(f"Exported {total_metrics} metrics to log file")

        except Exception as e:
            logger.error(f"Failed to export metrics to log: {e}", exc_info=True)

    async def export_summary(self, metrics: Dict[str, List[MetricValue]]) -> None:
        """
        导出指标摘要（仅关键统计信息）

        Args:
            metrics: 按收集器分组的指标
        """
        try:
            summary_data = {
                'timestamp': time.time(),
                'export_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                'summary': {}
            }

            total_metrics = 0
            for collector_name, metric_list in metrics.items():
                # 按指标类型统计
                type_counts = {}
                for metric in metric_list:
                    metric_type = metric.metric_type.value
                    type_counts[metric_type] = type_counts.get(metric_type, 0) + 1

                summary_data['summary'][collector_name] = {
                    'total_metrics': len(metric_list),
                    'by_type': type_counts
                }
                total_metrics += len(metric_list)

            summary_data['total_metrics'] = total_metrics

            # 写入摘要日志
            log_message = f"METRICS_SUMMARY: {json.dumps(summary_data, ensure_ascii=False, separators=(',', ':'))}"
            self._metrics_logger.info(log_message)

        except Exception as e:
            logger.error(f"Failed to export metrics summary: {e}", exc_info=True)
