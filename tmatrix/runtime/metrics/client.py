import asyncio
import httpx
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from tmatrix.common.logging import init_logger
logger = init_logger("metrics/client")


@dataclass
class MetricsClientConfig:
    """监控客户端配置"""
    base_url: str
    endpoint: str = "/stats"
    timeout: float = 10.0
    retry_times: int = 3
    retry_delay: float = 1.0


class AsyncMetricsClient:
    """异步监控指标客户端（单例模式）"""

    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._client: Optional[httpx.AsyncClient] = None
            self._config: Optional[MetricsClientConfig] = None
            self._initialized = True

    async def initialize(self, config: MetricsClientConfig):
        """初始化客户端"""
        async with self._lock:
            if self._client is None:
                self._config = config
                self._client = httpx.AsyncClient(
                    timeout=config.timeout,
                    headers={"Content-Type": "application/json"}
                )
                logger.info(f"Metrics client initialized with base_url: {config.base_url}")

    async def close(self):
        """关闭客户端"""
        async with self._lock:
            if self._client:
                await self._client.aclose()
                self._client = None
                logger.info("Metrics client closed")

    async def fetch_metrics(self, metrics: List[str]) -> Dict[str, Any]:
        """
        获取指定的监控指标

        Args:
            metrics: 指标名称列表，支持 ["all"] 获取所有指标

        Returns:
            解析后的指标数据字典

        Raises:
            Exception: 请求失败或解析失败时抛出异常
        """
        if not self._client or not self._config:
            raise RuntimeError("Client not initialized. Call initialize() first.")

        url = f"{self._config.base_url.rstrip('/')}{self._config.endpoint}"
        payload = {"metrics": metrics}

        for attempt in range(self._config.retry_times):
            try:
                logger.debug(f"Fetching metrics: {metrics} (attempt {attempt + 1})")

                response = await self._client.post(url, json=payload)
                response.raise_for_status()

                data = response.json()
                logger.debug(f"Successfully fetched {len(data.get('metrics', {}))} metric types")

                return data

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
                if attempt == self._config.retry_times - 1:
                    raise Exception(f"HTTP error after {self._config.retry_times} attempts: {e}")

            except httpx.RequestError as e:
                logger.error(f"Request error: {e}")
                if attempt == self._config.retry_times - 1:
                    raise Exception(f"Request error after {self._config.retry_times} attempts: {e}")

            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
                if attempt == self._config.retry_times - 1:
                    raise Exception(f"JSON decode error: {e}")

            if attempt < self._config.retry_times - 1:
                await asyncio.sleep(self._config.retry_delay)

        raise Exception("Max retry attempts reached")


class MetricsPoller:
    """监控指标定时轮询器"""

    def __init__(self, client: AsyncMetricsClient, interval: float = 30.0):
        """
        初始化轮询器

        Args:
            client: 异步监控客户端实例
            interval: 轮询间隔（秒）
        """
        self._client = client
        self._interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._latest_data: Dict[str, Any] = {}
        self._last_update: Optional[datetime] = None

    async def start_polling(self, metrics: List[str]):
        """
        开始定时轮询

        Args:
            metrics: 要轮询的指标列表
        """
        if self._running:
            logger.warning("Poller is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop(metrics))
        logger.info(f"Started polling metrics: {metrics} with interval {self._interval}s")

    async def stop_polling(self):
        """停止轮询"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Stopped polling")

    async def _poll_loop(self, metrics: List[str]):
        """轮询循环"""
        while self._running:
            try:
                data = await self._client.fetch_metrics(metrics)
                self._latest_data = data
                self._last_update = datetime.now()

                logger.debug(f"Updated metrics data at {self._last_update}")

            except Exception as e:
                logger.error(f"Failed to fetch metrics: {e}")

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    def get_latest_data(self) -> Dict[str, Any]:
        """获取最新的指标数据"""
        return self._latest_data.copy()

    def get_last_update(self) -> Optional[datetime]:
        """获取最后更新时间"""
        return self._last_update

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running


# 使用示例
# async def main():
#     """使用示例"""
#     # 配置客户端
#     config = MetricsClientConfig(
#         base_url="http://localhost:8080",
#         endpoint="/stats",
#         timeout=10.0,
#         retry_times=3,
#         retry_delay=1.0
#     )
#
#     # 获取单例客户端并初始化
#     client = AsyncMetricsClient()
#     await client.initialize(config)
#
#     try:
#         # 一次性获取指标
#         data = await client.fetch_metrics(["cpu_usage", "memory_usage"])
#         print("Fetched data:", json.dumps(data, indent=2))
#
#         # 或者获取所有指标
#         all_data = await client.fetch_metrics(["all"])
#         print("All metrics count:", len(all_data.get('metrics', {})))
#
#         # 使用轮询器
#         poller = MetricsPoller(client, interval=10.0)  # 每10秒轮询一次
#
#         # 开始轮询特定指标
#         await poller.start_polling(["cpu_usage", "memory_usage"])
#
#         # 模拟运行一段时间
#         await asyncio.sleep(35)  # 运行35秒，应该会轮询3-4次
#
#         # 获取最新数据
#         latest = poller.get_latest_data()
#         print("Latest data:", json.dumps(latest, indent=2))
#         print("Last update:", poller.get_last_update())
#
#         # 停止轮询
#         await poller.stop_polling()
#
#     finally:
#         # 清理资源
#         await client.close()
