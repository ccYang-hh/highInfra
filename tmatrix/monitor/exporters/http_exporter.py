import asyncio
import json
import time
from typing import Dict, Any, Optional
import aiohttp
from aiohttp import web

from ..core.metric_core import MetricBackend, MetricValue, logger


class MetricsServer(MetricBackend):
    """HTTP指标服务器，提供JSON格式的指标数据"""

    def __init__(self, port: int = 8080, host: str = '0.0.0.0', export_interval: float = 10.0):
        """
        初始化指标服务器

        Args:
            port: 服务器端口
            host: 绑定地址
            export_interval: 导出间隔，单位为秒
        """
        super().__init__()
        self.port = port
        self.host = host
        self._export_interval = export_interval
        self._metrics: Dict[str, MetricValue] = {}
        self._app = None
        self._runner = None
        self._site = None
        self._lock = asyncio.Lock()

    async def register_metric(self, metric: MetricValue) -> Any:
        """异步注册一个指标"""
        async with self._lock:
            name = metric.name
            self._metrics[name] = metric
            return metric

    async def export_metrics(self, metrics: Dict[str, MetricValue]) -> None:
        """异步导出指标"""
        async with self._lock:
            self._metrics.update(metrics)

    async def get_metrics(self) -> Dict[str, Dict[str, Any]]:
        """异步获取所有指标的JSON表示"""
        async with self._lock:
            result = {}
            for name, metric in self._metrics.items():
                result[name] = await metric.to_dict()
            return result

    async def _metrics_handler(self, request):
        """处理指标请求的HTTP处理器"""
        metrics = await self.get_metrics()
        return web.json_response(metrics)

    async def _health_handler(self, request):
        """健康检查处理器"""
        return web.json_response({"status": "ok"})

    async def start(self) -> None:
        """异步启动HTTP服务器"""
        if self._app:
            return

        self._app = web.Application()
        self._app.router.add_get('/metrics', self._metrics_handler)
        self._app.router.add_get('/', self._metrics_handler)
        self._app.router.add_get('/health', self._health_handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        logger.info(f"Metrics HTTP server started at http://{self.host}:{self.port}/metrics")

        # 启动导出循环
        await super().start()

    async def stop(self) -> None:
        """异步停止HTTP服务器"""
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
                logger.error(f"Error in HTTP server export loop: {e}")
                await asyncio.sleep(1)

    @property
    def export_interval(self) -> float:
        """导出间隔，秒"""
        return self._export_interval
