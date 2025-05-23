# metrics_system/api/server.py
import asyncio
import json
import time
from typing import Dict, Any, Optional
import aiohttp
from aiohttp import web
import logging

from ..core.registry import get_metric_registry
from ..collectors.request_collector import RequestStatsCollector
from ..collectors.event_collector import EventStatsCollector
from ..collectors.scheduler_collector import SchedulerStatsCollector

logger = logging.getLogger('metrics_system.server')


class MetricsApiServer:
    """指标API服务器，处理来自主业务进程的指标请求"""

    def __init__(self, port: int = 5000, host: str = '0.0.0.0'):
        """
        初始化API服务器

        Args:
            port: 服务器端口
            host: 绑定地址
        """
        self.port = port
        self.host = host
        self._app = None
        self._runner = None
        self._site = None

        # 获取指标收集器
        self.registry = get_metric_registry()
        self.request_collector = None
        self.event_collector = None
        self.scheduler_collector = None

        # 查找已注册的收集器
        for collector in self.registry._collectors:
            if isinstance(collector, RequestStatsCollector):
                self.request_collector = collector
            elif isinstance(collector, EventStatsCollector):
                self.event_collector = collector
            elif isinstance(collector, SchedulerStatsCollector):
                self.scheduler_collector = collector

    async def _health_handler(self, request):
        """健康检查处理器"""
        return web.json_response({"status": "ok"})

    async def _request_event_handler(self, request):
        """请求事件处理器"""
        if not self.request_collector:
            return web.json_response(
                {"error": "Request collector not found"}, status=500
            )

        try:
            data = await request.json()
            event_type = data.get('event_type')
            engine_url = data.get('engine_url')
            request_id = data.get('request_id')
            timestamp = data.get('timestamp', time.time())

            if not all([event_type, engine_url, request_id]):
                return web.json_response(
                    {"error": "Missing required fields"}, status=400
                )

            # 根据事件类型调用不同的方法
            if event_type == 'new':
                await self.request_collector.on_new_request(engine_url, request_id, timestamp)
            elif event_type == 'response':
                await self.request_collector.on_request_response(engine_url, request_id, timestamp)
            elif event_type == 'complete':
                await self.request_collector.on_request_complete(engine_url, request_id, timestamp)
            elif event_type == 'swapped':
                await self.request_collector.on_request_swapped(engine_url, request_id, timestamp)
            else:
                return web.json_response(
                    {"error": f"Unknown event type: {event_type}"}, status=400
                )

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Error handling request event: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _custom_event_handler(self, request):
        """自定义事件处理器"""
        if not self.event_collector:
            return web.json_response(
                {"error": "Event collector not found"}, status=500
            )

        try:
            data = await request.json()
            category = data.get('category')
            event_type = data.get('event_type')
            count = data.get('count', 1)

            if not all([category, event_type]):
                return web.json_response(
                    {"error": "Missing required fields"}, status=400
                )

            await self.event_collector.track_custom_event(category, event_type, count)

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Error handling custom event: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _task_event_handler(self, request):
        """任务事件处理器"""
        if not self.scheduler_collector:
            return web.json_response(
                {"error": "Scheduler collector not found"}, status=500
            )

        try:
            data = await request.json()
            event_type = data.get('event_type')
            task_id = data.get('task_id')
            timestamp = data.get('timestamp', time.time())
            success = data.get('success', True)

            if not all([event_type, task_id]):
                return web.json_response(
                    {"error": "Missing required fields"}, status=400
                )

            # 根据事件类型调用不同的方法
            if event_type == 'submitted':
                await self.scheduler_collector.on_task_submitted(task_id, timestamp)
            elif event_type == 'started':
                await self.scheduler_collector.on_task_started(task_id, timestamp)
            elif event_type == 'completed':
                await self.scheduler_collector.on_task_completed(task_id, timestamp, success)
            else:
                return web.json_response(
                    {"error": f"Unknown event type: {event_type}"}, status=400
                )

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Error handling task event: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _queue_depth_handler(self, request):
        """队列深度处理器"""
        if not self.scheduler_collector:
            return web.json_response(
                {"error": "Scheduler collector not found"}, status=500
            )

        try:
            data = await request.json()
            queue_name = data.get('queue_name')
            depth = data.get('depth')

            if not all([queue_name, depth is not None]):
                return web.json_response(
                    {"error": "Missing required fields"}, status=400
                )

            await self.scheduler_collector.update_queue_depth(queue_name, depth)

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Error handling queue depth update: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def start(self):
        """启动API服务器"""
        if self._app:
            return

        self._app = web.Application()
        self._app.router.add_get('/health', self._health_handler)
        self._app.router.add_post('/request_event', self._request_event_handler)
        self._app.router.add_post('/custom_event', self._custom_event_handler)
        self._app.router.add_post('/task_event', self._task_event_handler)
        self._app.router.add_post('/queue_depth', self._queue_depth_handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        logger.info(f"Metrics API server started at http://{self.host}:{self.port}")

    async def stop(self):
        """停止API服务器"""
        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._app = None
