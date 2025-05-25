"""
业务运行时指标收集器
从业务系统的/api/v1/metrics端点抓取指标
"""
import asyncio
import aiohttp
import time
from typing import List, Dict, Any, Optional

from tmatrix.monitor.core.base import MetricCollector, MetricValue, MetricType, CollectedEndpoint

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/collectors")


class RuntimeCollector(MetricCollector):
    """
    业务运行时指标收集器
    从业务系统抓取KV缓存事件和请求相关指标
    """

    def __init__(self, endpoints: List[CollectedEndpoint],
                 scrape_interval: float = 5.0,
                 timeout: float = 10.0):
        """
        初始化运行时收集器

        Args:
            endpoints: 业务系统端点列表，每个端点包含url和name
            scrape_interval: 抓取间隔（秒）
            timeout: HTTP请求超时时间（秒）
        """
        super().__init__(name='runtime_collector', scrape_interval=scrape_interval)
        self.endpoints = endpoints
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

        # 存储上一次的指标值，用于计算增量
        self._last_values: Dict[str, Dict[str, Any]] = {}

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
        从所有业务实例收集指标

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
                logger.error(f"Failed to scrape {endpoint['url']}: {result}")
                continue

            all_metrics.extend(result)

        return all_metrics

    async def _scrape_endpoint(self, endpoint: CollectedEndpoint) -> List[MetricValue]:
        """
        从单个业务端点抓取指标

        Args:
            endpoint: 端点配置，包含url和name

        Returns:
            指标列表
        """
        metrics_url = endpoint.address.rstrip('/') + '/api/v1/metrics'
        instance_name = endpoint.instance_name

        try:
            async with self._session.get(metrics_url) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}: {await response.text()}")

                # 获取JSON格式的指标数据
                data = await response.json()

                # 解析指标
                return self._parse_runtime_metrics(data, instance_name)

        except asyncio.TimeoutError:
            logger.error(f"Timeout scraping {metrics_url}")
            raise
        except Exception as e:
            logger.error(f"Error scraping {metrics_url}: {e}")
            raise

    def _parse_runtime_metrics(self, data: Dict[str, Any], instance_name: str) -> List[MetricValue]:
        """
        解析业务系统的指标数据

        Args:
            data: 业务系统返回的指标数据
            instance_name: 实例名称

        Returns:
            指标列表
        """
        metrics = []
        base_labels = {'instance': instance_name}

        # 解析KV缓存事件指标
        if 'kv_events_stats' in data:
            event_stats = data['kv_events_stats']

            # 块存储计数（Counter），总共存储了多少个Block，聚合值，递增值
            metrics.append(MetricValue(
                name='runtime_kv_blocks_stored_total',
                value=event_stats.get('block_stored_count', 0),
                metric_type=MetricType.COUNTER,
                labels=base_labels.copy(),
                description='Total number of KV blocks stored',
                unit='blocks'
            ))

            # 块移除计数（Counter），总共移除了多少个Block，聚合值，递增值
            metrics.append(MetricValue(
                name='runtime_kv_blocks_removed_total',
                value=event_stats.get('block_removed_count', 0),
                metric_type=MetricType.COUNTER,
                labels=base_labels.copy(),
                description='Total number of KV blocks removed',
                unit='blocks'
            ))

            # 当前实例持有的Block总数，非递增，大于等于0
            metrics.append(MetricValue(
                name='runtime_kv_blocks_count',
                value=event_stats.get('blocks_count', {}).get(instance_name, 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Current blocks count per instance',
                unit='blocks'
            ))

            # 当前实例处理的Token总数，递增值
            metrics.append(MetricValue(
                name='runtime_precessed_tokens_count',
                value=event_stats.get('processed_tokens_count', {}).get(instance_name, 0),
                metric_type=MetricType.COUNTER,
                labels=base_labels.copy(),
                description='Processed tokens count per instance',
                unit='tokens'
            ))

            # 活跃块数量（Gauge），所有实例的聚合指标
            metrics.append(MetricValue(
                name='runtime_kv_active_blocks',
                value=event_stats.get('active_blocks_count', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Current number of active KV blocks',
                unit='blocks'
            ))

            # 处理的token总数（Counter），所有实例的聚合指标
            metrics.append(MetricValue(
                name='runtime_processed_tokens_total',
                value=event_stats.get('processed_tokens_total', 0),
                metric_type=MetricType.COUNTER,
                labels=base_labels.copy(),
                description='Total number of tokens processed',
                unit='tokens'
            ))

            # 运行时间（Gauge）
            metrics.append(MetricValue(
                name='runtime_kv_uptime_seconds',
                value=event_stats.get('uptime', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Time since KV cache system started',
                unit='seconds'
            ))

        # 解析请求相关指标
        if 'requests_stats' in data:
            req_stats = data['requests_stats']

            # 待处理请求数（Gauge）
            metrics.append(MetricValue(
                name='runtime_requests_pending',
                value=req_stats.get('pending_requests', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Number of pending requests',
                unit='requests'
            ))

            # 活跃请求数（Gauge）
            metrics.append(MetricValue(
                name='runtime_requests_active',
                value=req_stats.get('active_requests', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Number of active requests',
                unit='requests'
            ))

            # 完成的请求数（Counter）
            metrics.append(MetricValue(
                name='runtime_requests_completed_total',
                value=req_stats.get('completed_requests', 0),
                metric_type=MetricType.COUNTER,
                labels=base_labels.copy(),
                description='Total number of completed requests',
                unit='requests'
            ))

            # 失败的请求数（Counter）
            metrics.append(MetricValue(
                name='runtime_requests_failed_total',
                value=req_stats.get('failed_requests', 0),
                metric_type=MetricType.COUNTER,
                labels=base_labels.copy(),
                description='Total number of failed requests',
                unit='requests'
            ))

            # 平均首个token时间（Gauge）
            metrics.append(MetricValue(
                name='runtime_avg_time_to_first_token_seconds',
                value=req_stats.get('avg_ttft', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Average time to first token',
                unit='seconds'
            ))

            # 平均请求延迟（Gauge）
            metrics.append(MetricValue(
                name='runtime_avg_request_latency_seconds',
                value=req_stats.get('avg_latency', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Average request latency',
                unit='seconds'
            ))

            # 每分钟请求数（Gauge）
            metrics.append(MetricValue(
                name='runtime_requests_per_minute',
                value=req_stats.get('requests_per_minute', 0),
                metric_type=MetricType.GAUGE,
                labels=base_labels.copy(),
                description='Requests per minute rate',
                unit='rpm'
            ))

        # 处理Counter类型指标的增量
        self._process_counter_increments(metrics, instance_name)

        return metrics

    def _process_counter_increments(self, metrics: List[MetricValue], instance_name: str):
        """
        处理Counter类型指标，确保它们只增不减

        Args:
            metrics: 指标列表
            instance_name: 实例名称
        """
        current_values = {}

        # 收集当前值
        for metric in metrics:
            if metric.metric_type == MetricType.COUNTER:
                key = f"{instance_name}:{metric.name}"
                current_values[key] = metric.value

        # 与上次值比较，处理重置情况
        if instance_name in self._last_values:
            last_values = self._last_values[instance_name]

            for metric in metrics:
                if metric.metric_type == MetricType.COUNTER:
                    key = f"{instance_name}:{metric.name}"
                    if key in last_values:
                        last_value = last_values[key]
                        current_value = metric.value

                        # 如果当前值小于上次值，说明可能发生了重置
                        if current_value < last_value:
                            logger.warning(
                                f"Counter {metric.name} reset detected for {instance_name}: "
                                f"{last_value} -> {current_value}"
                            )
                            # Counter重置时，我们仍然使用当前值
                            # 因为这是新的计数起点

        # 保存当前值供下次比较
        self._last_values[instance_name] = current_values
