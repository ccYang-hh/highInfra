import asyncio
import time
from typing import Dict, Any, List, Optional
import aiohttp
from dataclasses import dataclass

from ..core.metric_core import MetricCollector, logger
from ..core.registry import get_metric_registry, create_gauge


@dataclass
class EngineStats:
    """vLLM引擎指标数据类"""
    num_running_requests: int = 0
    num_queuing_requests: int = 0
    gpu_prefix_cache_hit_rate: float = 0.0
    gpu_cache_usage_perc: float = 0.0


@dataclass
class EndpointInfo:
    """端点信息"""
    url: str
    name: str = ""


class EngineStatsCollector(MetricCollector):
    """vLLM引擎指标收集器"""

    def __init__(self, endpoints: List[EndpointInfo] = None,
                 scrape_interval: float = 5.0, registry=None):
        """
        初始化引擎指标收集器

        Args:
            endpoints: 要抓取的端点列表
            scrape_interval: 抓取间隔，单位为秒
            registry: 指标注册中心
        """
        super().__init__(registry or get_metric_registry())
        self.endpoints = endpoints or []
        self._scrape_interval = scrape_interval
        self.engine_stats: Dict[str, EngineStats] = {}

        # 注册指标
        self._register_metrics()

    def _register_metrics(self):
        """注册引擎相关指标"""
        self.running_requests = create_gauge(
            "engine_running_requests",
            "Number of running requests",
            ["engine_url", "engine_name"]
        )

        self.queuing_requests = create_gauge(
            "engine_queuing_requests",
            "Number of queuing requests",
            ["engine_url", "engine_name"]
        )

        self.gpu_prefix_cache_hit_rate = create_gauge(
            "engine_gpu_prefix_cache_hit_rate",
            "GPU prefix cache hit rate",
            ["engine_url", "engine_name"]
        )

        self.gpu_cache_usage_perc = create_gauge(
            "engine_gpu_cache_usage_perc",
            "GPU KV cache usage percentage",
            ["engine_url", "engine_name"]
        )

    def add_endpoint(self, endpoint: EndpointInfo):
        """添加要抓取的端点"""
        self.endpoints.append(endpoint)

    def remove_endpoint(self, url: str):
        """移除指定URL的端点"""
        self.endpoints = [e for e in self.endpoints if e.url != url]

    async def _scrape_one_endpoint(self, endpoint: EndpointInfo) -> Optional[EngineStats]:
        """异步抓取单个引擎的指标"""
        url = endpoint.url
        metrics_url = f"{url}/metrics"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(metrics_url, timeout=self._scrape_interval) as response:
                    if response.status != 200:
                        logger.error(f"Failed to scrape metrics from {url}: HTTP {response.status}")
                        return None

                    text = await response.text()
                    return self._parse_vllm_metrics(text)
        except asyncio.TimeoutError:
            logger.error(f"Timeout scraping metrics from {url}")
            return None
        except Exception as e:
            logger.error(f"Failed to scrape metrics from {url}: {e}")
            return None

    def _parse_vllm_metrics(self, metrics_text: str) -> EngineStats:
        """解析vLLM指标文本"""
        # 简单的解析实现，实际应用中可能需要更复杂的解析逻辑
        stats = EngineStats()

        for line in metrics_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            metric_name = parts[0]
            value = float(parts[1])

            if metric_name == "vllm:num_requests_running":
                stats.num_running_requests = int(value)
            elif metric_name == "vllm:num_requests_waiting":
                stats.num_queuing_requests = int(value)
            elif metric_name == "vllm:gpu_prefix_cache_hit_rate":
                stats.gpu_prefix_cache_hit_rate = value
            elif metric_name == "vllm:gpu_cache_usage_perc":
                stats.gpu_cache_usage_perc = value

        return stats

    async def collect(self) -> Dict[str, EngineStats]:
        """异步收集所有引擎的指标"""
        collected_engine_stats = {}

        # 并发抓取所有端点
        tasks = []
        for endpoint in self.endpoints:
            task = asyncio.create_task(self._scrape_one_endpoint(endpoint))
            tasks.append((endpoint, task))

        # 等待所有任务完成
        for endpoint, task in tasks:
            try:
                engine_stats = await task
                if engine_stats:
                    collected_engine_stats[endpoint.url] = engine_stats

                    # 更新指标
                    await self.running_requests.set(
                        engine_stats.num_running_requests,
                        {"engine_url": endpoint.url, "engine_name": endpoint.name}
                    )

                    await self.queuing_requests.set(
                        engine_stats.num_queuing_requests,
                        {"engine_url": endpoint.url, "engine_name": endpoint.name}
                    )

                    await self.gpu_prefix_cache_hit_rate.set(
                        engine_stats.gpu_prefix_cache_hit_rate,
                        {"engine_url": endpoint.url, "engine_name": endpoint.name}
                    )

                    await self.gpu_cache_usage_perc.set(
                        engine_stats.gpu_cache_usage_perc,
                        {"engine_url": endpoint.url, "engine_name": endpoint.name}
                    )
            except Exception as e:
                logger.error(f"Error processing metrics from {endpoint.url}: {e}")

        # 更新内部状态
        old_urls = list(self.engine_stats.keys())
        for old_url in old_urls:
            if old_url not in collected_engine_stats:
                del self.engine_stats[old_url]

        self.engine_stats.update(collected_engine_stats)

        return self.engine_stats.copy()

    @property
    def collection_interval(self) -> float:
        """收集间隔，秒"""
        return self._scrape_interval

    def get_engine_stats(self) -> Dict[str, EngineStats]:
        """获取当前引擎统计信息的副本"""
        return self.engine_stats.copy()
