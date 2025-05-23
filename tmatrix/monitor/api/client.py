# metrics_system/api/client.py
import asyncio
import aiohttp
import time
import json
from typing import Dict, List, Any, Optional, Union
import logging
import contextlib

logger = logging.getLogger('metrics_system.client')


class MetricsClient:
    """指标客户端，用于主业务进程与监控进程通信"""

    def __init__(self, server_url: str):
        """
        初始化指标客户端

        Args:
            server_url: 监控服务器URL，例如 http://localhost:8080
        """
        self.server_url = server_url.rstrip('/')
        self._session = None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.close()

    async def connect(self):
        """连接到监控服务器"""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            # 测试连接
            try:
                await self.get_health()
            except Exception as e:
                await self._session.close()
                self._session = None
                raise ConnectionError(f"Failed to connect to metrics server at {self.server_url}: {e}")

    async def close(self):
        """关闭与监控服务器的连接"""
        if self._session:
            await self._session.close()
            self._session = None

    async def get_health(self) -> Dict[str, Any]:
        """获取服务器健康状态"""
        if not self._session:
            await self.connect()

        async with self._session.get(f"{self.server_url}/health") as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Health check failed: {response.status} - {text}")
            return await response.json()

    async def get_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        if not self._session:
            await self.connect()

        async with self._session.get(f"{self.server_url}/metrics") as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Failed to get metrics: {response.status} - {text}")
            return await response.json()

    async def record_request_event(self, event_type: str, engine_url: str, request_id: str,
                                   timestamp: Optional[float] = None) -> Dict[str, Any]:
        """
        记录请求事件

        Args:
            event_type: 事件类型，可以是 'new', 'response', 'complete', 'swapped'
            engine_url: 服务引擎URL
            request_id: 请求ID
            timestamp: 时间戳，如果为None则使用当前时间

        Returns:
            服务器响应
        """
        if not self._session:
            await self.connect()

        data = {
            "event_type": event_type,
            "engine_url": engine_url,
            "request_id": request_id,
            "timestamp": timestamp or time.time()
        }

        async with self._session.post(f"{self.server_url}/request_event", json=data) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Failed to record request event: {response.status} - {text}")
            return await response.json()

    async def record_custom_event(self, category: str, event_type: str,
                                  count: int = 1) -> Dict[str, Any]:
        """
        记录自定义事件

        Args:
            category: 事件类别
            event_type: 事件类型
            count: 事件计数

        Returns:
            服务器响应
        """
        if not self._session:
            await self.connect()

        data = {
            "category": category,
            "event_type": event_type,
            "count": count
        }

        async with self._session.post(f"{self.server_url}/custom_event", json=data) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Failed to record custom event: {response.status} - {text}")
            return await response.json()

    async def record_task_event(self, event_type: str, task_id: str,
                                timestamp: Optional[float] = None,
                                success: bool = True) -> Dict[str, Any]:
        """
        记录任务事件

        Args:
            event_type: 事件类型，可以是 'submitted', 'started', 'completed'
            task_id: 任务ID
            timestamp: 时间戳，如果为None则使用当前时间
            success: 对于'completed'事件，指示任务是否成功

        Returns:
            服务器响应
        """
        if not self._session:
            await self.connect()

        data = {
            "event_type": event_type,
            "task_id": task_id,
            "timestamp": timestamp or time.time(),
            "success": success
        }

        async with self._session.post(f"{self.server_url}/task_event", json=data) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Failed to record task event: {response.status} - {text}")
            return await response.json()

    async def update_queue_depth(self, queue_name: str, depth: int) -> Dict[str, Any]:
        """
        更新队列深度

        Args:
            queue_name: 队列名称
            depth: 队列深度

        Returns:
            服务器响应
        """
        if not self._session:
            await self.connect()

        data = {
            "queue_name": queue_name,
            "depth": depth
        }

        async with self._session.post(f"{self.server_url}/queue_depth", json=data) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Failed to update queue depth: {response.status} - {text}")
            return await response.json()


# 装饰器和上下文管理器
class MetricsDecorators:
    """指标装饰器和上下文管理器集合"""

    def __init__(self, client: MetricsClient):
        """
        初始化指标装饰器集合

        Args:
            client: 指标客户端实例
        """
        self.client = client

    def time_task(self, task_name: str):
        """
        任务计时装饰器

        Args:
            task_name: 任务名称，用作任务ID前缀

        Returns:
            装饰器函数
        """

        def decorator(func):
            async def wrapper(*args, **kwargs):
                task_id = f"{task_name}_{time.time()}"

                # 记录任务提交
                await self.client.record_task_event('submitted', task_id)

                try:
                    # 记录任务开始
                    await self.client.record_task_event('started', task_id)

                    # 执行函数
                    result = await func(*args, **kwargs)

                    # 记录任务成功完成
                    await self.client.record_task_event('completed', task_id, success=True)

                    return result
                except Exception as e:
                    # 记录任务失败
                    await self.client.record_task_event('completed', task_id, success=False)
                    raise

            return wrapper

        return decorator

    @contextlib.asynccontextmanager
    async def track_custom_event(self, category: str, event_type: str):
        """
        自定义事件跟踪上下文管理器

        Args:
            category: 事件类别
            event_type: 事件类型
        """
        # 记录开始事件
        await self.client.record_custom_event(category, f"{event_type}_start")

        try:
            yield
        except Exception as e:
            # 记录错误事件
            await self.client.record_custom_event(category, f"{event_type}_error")
            raise
        finally:
            # 记录结束事件
            await self.client.record_custom_event(category, f"{event_type}_end")
