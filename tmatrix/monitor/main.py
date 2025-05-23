# metrics_system/main.py
import asyncio
import argparse
import logging
import signal
import sys
import os
from typing import List, Dict, Any, Optional

from .core.registry import get_metric_registry
from .collectors.engine_collector import EngineStatsCollector, EndpointInfo
from .collectors.request_collector import RequestStatsCollector
from .collectors.event_collector import EventStatsCollector
from .collectors.scheduler_collector import SchedulerStatsCollector
from .exporters.prometheus_exporter import PrometheusExporter
from .exporters.log_exporter import LogExporter
from .exporters.http_server import MetricsServer
from .api.server import MetricsApiServer

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("metrics_system.log")
    ]
)
logger = logging.getLogger('metrics_system')


class MetricsService:
    """指标监控服务主类"""

    def __init__(self):
        """初始化监控服务"""
        self.registry = get_metric_registry()
        self.collectors = []
        self.exporters = []
        self.api_server = None
        self.running = False

    async def setup_collectors(self, config: Dict[str, Any]):
        """设置指标收集器"""
        # 引擎指标收集器
        if config.get('engine_collector', True):
            engine_endpoints = []
            for endpoint in config.get('engine_endpoints', []):
                engine_endpoints.append(EndpointInfo(
                    url=endpoint.get('url'),
                    name=endpoint.get('name', '')
                ))

            engine_collector = EngineStatsCollector(
                endpoints=engine_endpoints,
                scrape_interval=config.get('engine_scrape_interval', 5.0)
            )
            await self.registry.register_collector(engine_collector)
            self.collectors.append(engine_collector)
            logger.info("Engine stats collector registered")

        # 业务进程指标收集器
        if config.get('business_collector', True):
            business_endpoints = []
            for endpoint in config.get('business_endpoints', []):
                business_endpoints.append(BusinessEndpoint(
                    url=endpoint.get('url'),
                    name=endpoint.get('name', '')
                ))

            business_collector = BusinessProcessCollector(
                endpoints=business_endpoints,
                scrape_interval=config.get('business_scrape_interval', 5.0)
            )
            await self.registry.register_collector(business_collector)
            self.collectors.append(business_collector)
            logger.info("Business process collector registered")

        # 请求指标收集器
        if config.get('request_collector', True):
            request_collector = RequestStatsCollector(
                sliding_window_size=config.get('request_window_size', 60.0)
            )
            await self.registry.register_collector(request_collector)
            self.collectors.append(request_collector)
            logger.info("Request stats collector registered")

        # 事件指标收集器
        if config.get('event_collector', True):
            event_collector = EventStatsCollector()
            await self.registry.register_collector(event_collector)
            self.collectors.append(event_collector)
            logger.info("Event stats collector registered")

        # 调度器指标收集器
        if config.get('scheduler_collector', True):
            scheduler_collector = SchedulerStatsCollector()
            await self.registry.register_collector(scheduler_collector)
            self.collectors.append(scheduler_collector)
            logger.info("Scheduler stats collector registered")

    async def setup_exporters(self, config: Dict[str, Any]):
        """设置指标导出器"""
        # Prometheus导出器
        if config.get('prometheus_exporter', True):
            try:
                prometheus_exporter = PrometheusExporter(
                    port=config.get('prometheus_port', 8000),
                    addr=config.get('prometheus_host', ''),
                    export_interval=config.get('prometheus_interval', 10.0)
                )
                await self.registry.register_backend(prometheus_exporter)
                self.exporters.append(prometheus_exporter)
                logger.info(f"Prometheus exporter registered on port {config.get('prometheus_port', 8000)}")
            except ImportError:
                logger.warning("Prometheus client library not available, skipping Prometheus exporter")

        # 日志导出器
        if config.get('log_exporter', True):
            log_exporter = LogExporter(
                interval=config.get('log_interval', 60.0)
            )
            await self.registry.register_backend(log_exporter)
            self.exporters.append(log_exporter)
            logger.info("Log exporter registered")

        # HTTP指标服务器
        if config.get('metrics_server', True):
            metrics_server = MetricsServer(
                port=config.get('metrics_port', 8080),
                host=config.get('metrics_host', '0.0.0.0'),
                export_interval=config.get('metrics_interval', 10.0)
            )
            await self.registry.register_backend(metrics_server)
            self.exporters.append(metrics_server)
            logger.info(f"Metrics HTTP server registered on port {config.get('metrics_port', 8080)}")

    async def setup_api_server(self, config: Dict[str, Any]):
        """设置API服务器"""
        if config.get('api_server', True):
            self.api_server = MetricsApiServer(
                port=config.get('api_port', 5000),
                host=config.get('api_host', '0.0.0.0')
            )
            await self.api_server.start()
            logger.info(f"Metrics API server started on port {config.get('api_port', 5000)}")

    async def start(self, config: Dict[str, Any]):
        """启动监控服务"""
        if self.running:
            return

        self.running = True

        # 设置各组件
        await self.setup_collectors(config)
        await self.setup_exporters(config)
        await self.setup_api_server(config)

        # 启动所有组件
        await self.registry.start_all()

        logger.info("Metrics monitoring service started")

    async def stop(self):
        """停止监控服务"""
        if not self.running:
            return

        # 停止API服务器
        if self.api_server:
            await self.api_server.stop()
            self.api_server = None

        # 停止所有组件
        await self.registry.stop_all()

        self.running = False
        logger.info("Metrics monitoring service stopped")


def load_config(config_file: Optional[str] = None) -> Dict[str, Any]:
    """
    加载配置文件

    Args:
        config_file: 配置文件路径，如果为None则使用默认配置

    Returns:
        配置字典
    """
    # 默认配置
    default_config = {
        # 收集器配置
        'engine_collector': True,
        'engine_endpoints': [],
        'engine_scrape_interval': 5.0,
        'request_collector': True,
        'request_window_size': 60.0,
        'event_collector': True,
        'scheduler_collector': True,

        # 导出器配置
        'prometheus_exporter': True,
        'prometheus_port': 8000,
        'prometheus_host': '',
        'prometheus_interval': 10.0,
        'log_exporter': True,
        'log_interval': 60.0,
        'metrics_server': True,
        'metrics_port': 8080,
        'metrics_host': '0.0.0.0',
        'metrics_interval': 10.0,

        # API服务器配置
        'api_server': True,
        'api_port': 5000,
        'api_host': '0.0.0.0',
    }

    # 如果提供了配置文件，则加载它
    if config_file:
        try:
            import json
            with open(config_file, 'r') as f:
                user_config = json.load(f)
                default_config.update(user_config)
        except Exception as e:
            logger.error(f"Error loading config file {config_file}: {e}")

    return default_config


async def main_async(args):
    """异步主函数"""
    # 加载配置
    config = load_config(args.config)

    # 创建服务
    service = MetricsService()

    # 设置信号处理
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received termination signal, shutting down...")
        asyncio.create_task(service.stop())

    # 注册信号处理器
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # 启动服务
        await service.start(config)

        # 保持运行，直到收到停止信号
        while service.running:
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Error in metrics service: {e}")
    finally:
        # 确保服务停止
        await service.stop()


def main():
    """主函数入口点"""
    parser = argparse.ArgumentParser(description='Metrics Monitoring System')
    parser.add_argument('--config', type=str, help='Configuration file path')
    args = parser.parse_args()

    # 在异步事件循环中运行
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nReceived keyboard interrupt, exiting")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
