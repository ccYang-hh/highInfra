import asyncio
import aiohttp
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from ..core.metric_core import MetricCollector, logger
from ..core.registry import get_metric_registry, create_gauge, create_counter, create_histogram


@dataclass
class BusinessEndpoint:
    """业务进程端点信息"""
    url: str
    name: str = ""


class BusinessProcessCollector(MetricCollector):
    """业务进程指标收集器"""

    def __init__(self, endpoints: List[BusinessEndpoint] = None,
                 scrape_interval: float = 5.0, registry=None):
        """
        初始化业务进程指标收集器

        Args:
            endpoints: 要抓取的业务进程端点列表
            scrape_interval: 抓取间隔，单位为秒
            registry: 指标注册中心
        """
        super().__init__(registry or get_metric_registry())
        self.endpoints = endpoints or []
        self._scrape_interval = scrape_interval
        self.process_metrics = {}  # 存储最后抓取的指标

        # 注册指标
        self._register_metrics()

    def _register_metrics(self):
        """注册业务进程相关指标"""
        # KV缓存相关指标
        self.kv_blocks_stored = create_counter(
            "business_kv_blocks_stored_total",
            "Total number of KV blocks stored",
            ["process_url", "process_name"]
        )

        self.kv_blocks_removed = create_counter(
            "business_kv_blocks_removed_total",
            "Total number of KV blocks removed",
            ["process_url", "process_name"]
        )

        self.kv_blocks_cleared = create_counter(
            "business_kv_blocks_cleared_total",
            "Total number of times all KV blocks cleared",
            ["process_url", "process_name"]
        )

        self.kv_active_blocks = create_gauge(
            "business_kv_active_blocks",
            "Number of active KV blocks",
            ["process_url", "process_name"]
        )

        self.kv_tokens_processed = create_counter(
            "business_kv_tokens_processed_total",
            "Total number of tokens processed",
            ["process_url", "process_name"]
        )

        # 请求相关指标
        self.pending_requests = create_gauge(
            "business_pending_requests",
            "Number of pending requests",
            ["process_url", "process_name"]
        )

        self.active_requests = create_gauge(
            "business_active_requests",
            "Number of active requests",
            ["process_url", "process_name"]
        )

        self.completed_requests = create_counter(
            "business_completed_requests_total",
            "Total number of completed requests",
            ["process_url", "process_name"]
        )

        self.failed_requests = create_counter(
            "business_failed_requests_total",
            "Total number of failed requests",
            ["process_url", "process_name"]
        )

        self.avg_ttft = create_gauge(
            "business_avg_ttft_seconds",
            "Average time to first token in seconds",
            ["process_url", "process_name"]
        )

        self.avg_latency = create_gauge(
            "business_avg_latency_seconds",
            "Average request latency in seconds",
            ["process_url", "process_name"]
        )

    def add_endpoint(self, endpoint: BusinessEndpoint):
        """添加要抓取的端点"""
        self.endpoints.append(endpoint)

    def remove_endpoint(self, url: str):
        """移除指定URL的端点"""
        self.endpoints = [e for e in self.endpoints if e.url != url]

    async def _scrape_endpoint(self, endpoint: BusinessEndpoint) -> Optional[Dict[str, Any]]:
        """从单个端点抓取指标"""
        url = endpoint.url
        metrics_url = f"{url}/metrics"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(metrics_url, timeout=self._scrape_interval) as response:
                    if response.status != 200:
                        logger.error(f"Failed to scrape metrics from {url}: HTTP {response.status}")
                        return None

                    return await response.json()
        except asyncio.TimeoutError:
            logger.error(f"Timeout scraping metrics from {url}")
            return None
        except Exception as e:
            logger.error(f"Failed to scrape metrics from {url}: {e}")
            return None

    async def _update_metrics_from_scraped_data(self, endpoint: BusinessEndpoint, data: Dict[str, Any]):
        """从抓取的数据更新指标"""
        url = endpoint.url
        name = endpoint.name
        labels = {"process_url": url, "process_name": name}

        # 更新KV缓存指标
        if "event_stats" in data:
            event_stats = data["event_stats"]

            # 使用gauge设置当前值
            await self.kv_active_blocks.set(
                event_stats.get("active_blocks_count", 0),
                labels
            )

            # 更新计数器前需要获取当前值，计算增量
            current_stored = await self.kv_blocks_stored.get_value(labels)
            current_removed = await self.kv_blocks_removed.get_value(labels)
            current_cleared = await self.kv_blocks_cleared.get_value(labels)
            current_tokens = await self.kv_tokens_processed.get_value(labels)

            # 只增加增量部分
            if url in self.process_metrics and "event_stats" in self.process_metrics[url]:
                old_stats = self.process_metrics[url]["event_stats"]

                stored_inc = event_stats.get("block_stored_count", 0) - old_stats.get("block_stored_count", 0)
                removed_inc = event_stats.get("block_removed_count", 0) - old_stats.get("block_removed_count", 0)
                cleared_inc = event_stats.get("all_blocks_cleared_count", 0) - old_stats.get("all_blocks_cleared_count",
                                                                                             0)
                tokens_inc = event_stats.get("total_tokens_processed", 0) - old_stats.get("total_tokens_processed", 0)

                # 只有在有增量时才更新
                if stored_inc > 0:
                    await self.kv_blocks_stored.inc(stored_inc, labels)
                if removed_inc > 0:
                    await self.kv_blocks_removed.inc(removed_inc, labels)
                if cleared_inc > 0:
                    await self.kv_blocks_cleared.inc(cleared_inc, labels)
                if tokens_inc > 0:
                    await self.kv_tokens_processed.inc(tokens_inc, labels)
            else:
                # 首次获取，直接设置
                await self.kv_blocks_stored.inc(event_stats.get("block_stored_count", 0), labels)
                await self.kv_blocks_removed.inc(event_stats.get("block_removed_count", 0), labels)
                await self.kv_blocks_cleared.inc(event_stats.get("all_blocks_cleared_count", 0), labels)
                await self.kv_tokens_processed.inc(event_stats.get("total_tokens_processed", 0), labels)

        # 更新请求指标
        if "request_stats" in data:
            req_stats = data["request_stats"]

            await self.pending_requests.set(
                req_stats.get("pending_requests", 0),
                labels
            )

            await self.active_requests.set(
                req_stats.get("active_requests", 0),
                labels
            )

            # 计数器增量更新逻辑
            if url in self.process_metrics and "request_stats" in self.process_metrics[url]:
                old_req_stats = self.process_metrics[url]["request_stats"]

                completed_inc = req_stats.get("completed_requests", 0) - old_req_stats.get("completed_requests", 0)
                failed_inc = req_stats.get("failed_requests", 0) - old_req_stats.get("failed_requests", 0)

                if completed_inc > 0:
                    await self.completed_requests.inc(completed_inc, labels)
                if failed_inc > 0:
                    await self.failed_requests.inc(failed_inc, labels)
            else:
                # 首次获取，直接设置
                await self.completed_requests.inc(req_stats.get("completed_requests", 0), labels)
                await self.failed_requests.inc(req_stats.get("failed_requests", 0), labels)

            # 更新平均时间指标
            await self.avg_ttft.set(
                req_stats.get("avg_ttft", 0),
                labels
            )

            await self.avg_latency.set(
                req_stats.get("avg_latency", 0),
                labels
            )

        # 保存最新抓取的指标
        self.process_metrics[url] = data

    async def collect(self) -> Dict[str, Any]:
        """异步收集所有业务进程指标"""
        results = {}

        # 并发抓取所有端点
        tasks = []
        for endpoint in self.endpoints:
            task = asyncio.create_task(self._scrape_endpoint(endpoint))
            tasks.append((endpoint, task))

        # 等待所有任务完成
        for endpoint, task in tasks:
            try:
                data = await task
                if data:
                    # 更新指标
                    await self._update_metrics_from_scraped_data(endpoint, data)
                    results[endpoint.url] = data
            except Exception as e:
                logger.error(f"Error processing metrics from {endpoint.url}: {e}")

        return results

    @property
    def collection_interval(self) -> float:
        """收集间隔，秒"""
        return self._scrape_interval
