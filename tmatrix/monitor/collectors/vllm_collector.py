"""
vLLM引擎指标收集器
通过HTTP接口从vLLM实例抓取Prometheus格式的指标
"""
import asyncio
import aiohttp
from typing import List, Dict, Optional
from prometheus_client.parser import text_string_to_metric_families

from tmatrix.monitor.core.base import MetricCollector, MetricValue, MetricType, CollectedEndpoint

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/collectors")


class VLLMCollector(MetricCollector):
    """
    vLLM引擎指标收集器
    从vLLM的/metrics端点抓取核心指标
    """

    # 定义要收集的核心指标
    CORE_METRICS = {
        'vllm:num_requests_running': {
            'type': MetricType.GAUGE,
            'description': 'Number of requests currently being processed',
            'unit': 'requests'
        },
        'vllm:num_requests_waiting': {
            'type': MetricType.GAUGE,
            'description': 'Number of requests waiting in queue',
            'unit': 'requests'
        },
        'vllm:gpu_cache_usage_perc': {
            'type': MetricType.GAUGE,
            'description': 'GPU KV-cache usage percentage',
            'unit': 'percent'
        },
        'vllm:prompt_tokens': {
            'type': MetricType.COUNTER,
            'description': 'Total number of prompt tokens processed',
            'unit': 'tokens'
        },
        'vllm:generation_tokens': {
            'type': MetricType.COUNTER,
            'description': 'Total number of generation tokens processed',
            'unit': 'tokens'
        },
        # 'vllm:request_success_total': {
        #     'type': MetricType.COUNTER,
        #     'description': 'Total number of successful requests',
        #     'unit': 'requests'
        # },
        # 'vllm:num_preemptions_total': {
        #     'type': MetricType.COUNTER,
        #     'description': 'Total number of request preemptions',
        #     'unit': 'preemptions'
        # },
        'vllm:time_to_first_token_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'Time to first token in seconds',
            'unit': 'seconds'
        },
        'vllm:time_per_output_token_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'Time per output token in seconds',
            'unit': 'seconds'
        },
        'vllm:e2e_request_latency_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'End-to-end request latency in seconds',
            'unit': 'seconds'
        },
        'vllm:request_queue_time_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'Time spent waiting in queue for request',
            'unit': 'seconds'
        },
        'vllm:request_inference_time_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'Time spent in inference execution for request',
            'unit': 'seconds'
        },
        'vllm:request_prefill_time_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'Time spent in prefill phase for request',
            'unit': 'seconds'
        },
        'vllm:request_decode_time_seconds': {
            'type': MetricType.HISTOGRAM,
            'description': 'Time spent in decode phase for request',
            'unit': 'seconds'
        }
    }

    def __init__(self, endpoints: List[CollectedEndpoint],
                 scrape_interval: float = 5.0,
                 timeout: float = 10.0):
        """
        初始化vLLM收集器

        Args:
            endpoints: vLLM实例端点列表，每个端点包含url和name
            scrape_interval: 抓取间隔（秒）
            timeout: HTTP请求超时时间（秒）
        """
        super().__init__(name='vllm_collector', scrape_interval=scrape_interval)
        self.endpoints = endpoints
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        """启动收集器，创建HTTP会话"""
        # 创建HTTP会话
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        await super().start()

    async def stop(self):
        """停止收集器，关闭HTTP会话"""
        await super().stop()
        if self._session:
            await self._session.close()
            self._session = None

    async def add_endpoint(self, endpoint: CollectedEndpoint):
        self.endpoints.append(endpoint)

    async def collect(self) -> List[MetricValue]:
        """
        从所有vLLM实例收集指标

        Returns:
            收集到的指标列表
        """
        if not self._session:
            logger.error("HTTP session not initialized")
            return []

        if len(self.endpoints) == 0:
            return []

        all_metrics = []

        # 并发抓取所有端点
        tasks = []
        for endpoint in self.endpoints:
            task = self._scrape_endpoint(endpoint)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for endpoint, result in zip(self.endpoints, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to scrape {endpoint.address}: {result}")
                continue

            all_metrics.extend(result)

        return all_metrics

    async def _scrape_endpoint(self, endpoint: CollectedEndpoint) -> List[MetricValue]:
        """
        从单个vLLM端点抓取指标

        Args:
            endpoint: 端点配置，包含url和name

        Returns:
            指标列表
        """
        metrics_url = endpoint.address.rstrip('/') + '/metrics'
        instance_name = endpoint.instance_name

        try:
            async with self._session.get(metrics_url) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}: {await response.text()}")

                # 获取Prometheus格式的文本
                text = await response.text()

                # 解析指标
                return self._parse_prometheus_metrics(text, instance_name)

        except asyncio.TimeoutError:
            logger.error(f"Timeout scraping {metrics_url}")
            raise
        except Exception as e:
            logger.error(f"Error scraping {metrics_url}: {e}")
            raise

    def _parse_prometheus_metrics(self, text: str, instance_name: str) -> List[MetricValue]:
        """
        解析Prometheus格式的指标文本

        Args:
            text: Prometheus格式的指标文本
            instance_name: 实例名称

        Returns:
            指标列表
        """
        metrics = []

        try:
            # 使用prometheus_client解析器
            # TODO 这里解析应该更精细，具体到family和sample级别
            families = text_string_to_metric_families(text)
            for family in families:
                # 只收集我们关心的核心指标
                if family.name not in self.CORE_METRICS:
                    continue

                metric_info = self.CORE_METRICS[family.name]

                # 处理每个样本
                for sample in family.samples:
                    # 构建标签，添加实例信息
                    labels = dict(sample.labels)
                    labels['instance'] = instance_name

                    # 创建指标值对象
                    metric = MetricValue(
                        name=sample.name,
                        value=sample.value,
                        metric_type=metric_info['type'],
                        labels=labels,
                        description=metric_info['description'],
                        unit=metric_info['unit']
                    )
                    metrics.append(metric)

        except Exception as e:
            logger.error(f"Failed to parse metrics from {instance_name}: {e}")

        return metrics
