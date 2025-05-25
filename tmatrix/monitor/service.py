"""
监控服务主类
负责协调所有收集器和导出器的运行
"""
import asyncio
import signal
import time
from typing import Dict, List, Optional

from .core.base import MetricCollector, MetricExporter, CollectedEndpoint, MetricValue
from .core.config import MonitorConfig
from .core.registry import MetricRegistry
from .collectors.vllm_collector import VLLMCollector
from .collectors.runtime_collector import RuntimeCollector
from .exporters.prometheus import PrometheusExporter
from .exporters.log import LogExporter
from .web_server import MonitorWebServer

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/core")


class MonitorService:
    """
    监控服务主类
    管理所有收集器和导出器的生命周期
    """

    def __init__(self, config: MonitorConfig):
        """
        初始化监控服务

        Args:
            config: 监控配置对象
        """
        self.config = config
        self.registry = MetricRegistry()

        # 组件列表
        self.collectors: List[MetricCollector] = []
        self.exporters: List[MetricExporter] = []

        # 运行状态
        self._running = False
        self._stop_event = asyncio.Event()
        self._start_time = None

        # Web服务器（解耦后）
        self.web_server: Optional[MonitorWebServer] = None
        if self.config.http_enabled:
            self.web_server = MonitorWebServer(
                host=self.config.http_host,
                port=self.config.http_port
            )
            # 注入依赖
            self.web_server.set_monitor_service(self)

    async def initialize(self):
        """初始化所有组件"""
        logger.info("Initializing monitor service...")

        # 创建收集器
        await self._create_collectors()

        # 创建导出器
        await self._create_exporters()

        logger.info(f"Initialized {len(self.collectors)} collectors and {len(self.exporters)} exporters")

    async def _create_collectors(self):
        """根据配置创建收集器"""
        for collector_config in self.config.collectors:
            if not collector_config.enabled:
                continue

            try:
                if collector_config.type == 'vllm':
                    collector = VLLMCollector(
                        endpoints=collector_config.endpoints,
                        scrape_interval=collector_config.scrape_interval
                    )

                elif collector_config.type == 'runtime':
                    collector = RuntimeCollector(
                        endpoints=collector_config.endpoints,
                        scrape_interval=collector_config.scrape_interval
                    )

                else:
                    logger.error(f"Unknown collector type: {collector_config.type}")
                    continue

                if collector:
                    self.collectors.append(collector)
                    logger.info(f"Created collector: {collector.name}")

            except Exception as e:
                logger.error(f"Failed to create collector {collector_config.type}: {e}")

    async def _add_endpoint(self, endpoint: CollectedEndpoint):
        for collector in self.collectors:
            # 当新增了一个Endpoint时，需要同步更新VLLMCollector
            if isinstance(collector, VLLMCollector):
                # 检查是否已存在（避免重复添加）
                existing = any(
                    ep.address == endpoint.address and ep.instance_name == endpoint.instance_name
                    for ep in collector.endpoints
                )
                if not existing:
                    collector.endpoints.append(endpoint)

    async def _del_endpoint(self, endpoint: CollectedEndpoint) -> bool:
        deleted = False

        for collector in self.collectors:
            if isinstance(collector, VLLMCollector):
                # 方法1：使用列表推导式重新构建列表
                original_count = len(collector.endpoints)
                collector.endpoints[:] = [
                    ep for ep in collector.endpoints
                    if not (ep.address == endpoint.address and ep.instance_name == endpoint.instance_name)
                ]

                # 检查是否有删除
                if len(collector.endpoints) < original_count:
                    deleted = True

        return deleted

    async def _create_exporters(self):
        """根据配置创建导出器"""
        for exporter_config in self.config.exporters:
            if not exporter_config.enabled:
                continue

            try:
                if exporter_config.type == 'prometheus':
                    exporter = PrometheusExporter(
                        port=exporter_config.extra.get('port', 8090),
                        host=exporter_config.extra.get('host', '0.0.0.0'),
                        path=exporter_config.extra.get('path', '/metrics'),
                        export_interval=exporter_config.export_interval
                    )

                elif exporter_config.type == 'log':
                    exporter = LogExporter(
                        log_file=exporter_config.extra.get('log_file', 'metrics.log'),
                        export_interval=exporter_config.export_interval
                    )

                else:
                    logger.error(f"Unknown exporter type: {exporter_config.type}")
                    continue

                if exporter:
                    self.exporters.append(exporter)
                    logger.info(f"Created exporter: {exporter.name}")

            except Exception as e:
                logger.error(f"Failed to create exporter {exporter_config.type}: {e}")

    async def start(self):
        """启动监控服务"""
        if self._running:
            logger.warning("Monitor service is already running")
            return

        logger.info("Starting monitor service...")
        self._running = True
        self._start_time = time.time()

        try:
            # 启动所有收集器
            for collector in self.collectors:
                await collector.start()

            # 启动所有导出器
            for exporter in self.exporters:
                await exporter.start()

            # 启动Web服务器
            if self.web_server:
                await self.web_server.start()

            logger.info("Monitor service started successfully")

        except Exception as e:
            logger.error(f"Failed to start monitor service: {e}")
            await self.stop()
            raise

    async def stop(self):
        """停止监控服务"""
        if not self._running:
            return

        logger.info("Stopping monitor service...")
        self._running = False

        # 停止Web服务器
        if self.web_server:
            await self.web_server.stop()

        # 停止所有导出器
        for exporter in self.exporters:
            try:
                await exporter.stop()
            except Exception as e:
                logger.error(f"Error stopping exporter {exporter.name}: {e}")

        # 停止所有收集器
        for collector in self.collectors:
            try:
                await collector.stop()
            except Exception as e:
                logger.error(f"Error stopping collector {collector.name}: {e}")

        # 清空注册中心
        await self.registry.clear()

        # 设置停止事件
        self._stop_event.set()

        logger.info("Monitor service stopped")

    async def wait_until_stop(self):
        """运行监控服务（阻塞直到收到停止信号）"""
        # 设置信号处理
        def signal_handler():
            logger.info("Received stop signal")
            asyncio.create_task(self.stop())

        loop = asyncio.get_event_loop()
        for sig in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(sig, signal_handler)

        # 等待停止事件
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            await self.stop()

    def get_stats(self) -> Dict[str, any]:
        """获取服务统计信息"""
        return {
            'running': self._running,
            'collectors': [c.get_stats() for c in self.collectors],
            'exporters': [e.get_stats() for e in self.exporters],
            'registry': self.registry.get_stats()
        }

    async def get_all_metrics(self) -> Dict[str, List[MetricValue]]:
        data = await self.wait_until_stop()
        return data
