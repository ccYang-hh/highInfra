"""
配置管理模块
负责加载和验证配置文件
"""
import json
import os
from typing import Dict, Any, List
from dataclasses import dataclass, field

from .base import CollectedEndpoint
from tmatrix.common.logging import init_logger
logger = init_logger("monitor/core")


@dataclass
class CollectorConfig:
    """收集器配置"""
    type: str                      # 收集器类型
    enabled: bool = True           # 是否启用
    scrape_interval: float = 10.0  # 抓取间隔（秒）
    endpoints: List[CollectedEndpoint] = field(default_factory=list)  # 端点配置
    extra: Dict[str, Any] = field(default_factory=dict)  # 额外配置


@dataclass
class ExporterConfig:
    """导出器配置"""
    type: str                      # 导出器类型
    enabled: bool = True           # 是否启用
    export_interval: float = 10.0  # 导出间隔（秒）
    extra: Dict[str, Any] = field(default_factory=dict)  # 额外配置


@dataclass
class MonitorConfig:
    """监控系统总体配置"""
    # 收集器配置
    collectors: List[CollectorConfig] = field(default_factory=list)

    # 导出器配置
    exporters: List[ExporterConfig] = field(default_factory=list)

    # 日志配置
    # log_level: str = "INFO"
    # log_file: Optional[str] = None

    # HTTP服务配置（用于健康检查等）
    http_enabled: bool = True
    http_host: str = "0.0.0.0"
    http_port: int = 9090

    @classmethod
    def from_file(cls, config_file: str) -> 'MonitorConfig':
        """
        从配置文件加载配置

        Args:
            config_file: 配置文件路径

        Returns:
            配置对象
        """
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file not found: {config_file}")

        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        return cls.from_dict(config_data)

    @classmethod
    def from_dict(cls, config_data: Dict[str, Any]) -> 'MonitorConfig':
        """
        从字典加载配置

        Args:
            config_data: 配置字典

        Returns:
            配置对象
        """
        config = cls()

        # 解析收集器配置
        for collector_data in config_data.get('collectors', []):
            # 解析endpoints
            endpoints_data = collector_data.get('endpoints', [])
            endpoints: List[CollectedEndpoint] = []
            for item in endpoints_data:
                endpoints.append(CollectedEndpoint(**item))

            collector_config = CollectorConfig(
                type=collector_data['type'],
                enabled=collector_data.get('enabled', True),
                scrape_interval=collector_data.get('scrape_interval', 10.0),
                endpoints=endpoints,
                extra=collector_data.get('extra', {})
            )
            config.collectors.append(collector_config)

        # 解析导出器配置
        for exporter_data in config_data.get('exporters', []):
            exporter_config = ExporterConfig(
                type=exporter_data['type'],
                enabled=exporter_data.get('enabled', True),
                export_interval=exporter_data.get('export_interval', 10.0),
                extra=exporter_data.get('extra', {})
            )
            config.exporters.append(exporter_config)

        # 解析其他配置
        config.http_enabled = config_data.get('http_enabled', True)
        config.http_host = config_data.get('http_host', '0.0.0.0')
        config.http_port = config_data.get('http_port', 9090)

        return config

    def validate(self) -> None:
        """验证配置的合法性"""
        # 验证收集器配置
        collector_types = {'vllm', 'runtime'}
        for collector in self.collectors:
            if collector.type not in collector_types:
                raise ValueError(f"Unknown collector type: {collector.type}")
            if collector.scrape_interval <= 0:
                raise ValueError(f"Invalid scrape_interval for collector {collector.type}: {collector.scrape_interval}")

        # 验证导出器配置
        exporter_types = {'prometheus', 'log'}
        for exporter in self.exporters:
            if exporter.type not in exporter_types:
                raise ValueError(f"Unknown exporter type: {exporter.type}")
            if exporter.export_interval <= 0:
                raise ValueError(f"Invalid export_interval for exporter {exporter.type}: {exporter.export_interval}")

        # 验证端口
        if self.http_enabled and not (0 < self.http_port < 65536):
            raise ValueError(f"Invalid HTTP port: {self.http_port}")


def get_default_config() -> MonitorConfig:
    """获取默认配置"""
    return MonitorConfig(
        collectors=[
            CollectorConfig(
                type='vllm',
                enabled=True,
                scrape_interval=5.0,
                endpoints=[
                    CollectedEndpoint(
                        address='http://0.0.0.0:8001',
                        instance_name='vllm-worker',
                    )
                ]
            ),
            CollectorConfig(
                type='runtime',
                enabled=True,
                scrape_interval=5.0,
                endpoints=[
                    CollectedEndpoint(
                        address='http://0.0.0.0:8002',
                        instance_name='runtime',
                    )
                ]
            )
        ],
        exporters=[
            ExporterConfig(
                type='prometheus',
                enabled=True,
                export_interval=10.0,
                extra={
                    'host': "0.0.0.0",
                    'port': 8090,
                    'path': '/metrics'
                }
            ),
            ExporterConfig(
                type='log',
                enabled=True,
                export_interval=60.0,
                extra={
                    'log_file': 'metrics.log'
                }
            )
        ]
    )
