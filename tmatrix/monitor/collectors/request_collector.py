# metrics_system/collectors/request_collector.py
import asyncio
import time
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass
from collections import deque

from ..core.metric_core import MetricCollector, logger
from ..core.registry import get_metric_registry, create_gauge, create_counter, create_histogram


@dataclass
class RequestStats:
    """请求统计信息数据类"""
    qps: float = 0.0
    ttft: float = 0.0
    in_prefill_requests: int = 0
    in_decoding_requests: int = 0
    finished_requests: int = 0
    uptime: int = 0
    avg_decoding_length: float = 0.0
    avg_latency: float = 0.0
    avg_itl: float = -1.0
    num_swapped_requests: int = 0


class MovingAverageMonitor:
    """移动平均监控器，用于跟踪一段时间内的平均值"""

    def __init__(self, window_size: float):
        """
        初始化移动平均监控器

        Args:
            window_size: 滑动窗口大小，单位为秒
        """
        self.window_size = window_size
        self.timestamps: deque = deque()
        self.values: deque = deque()
        self._lock = asyncio.Lock()

    async def update(self, timestamp: float, value: float) -> None:
        """异步更新监控器数据"""
        async with self._lock:
            self.timestamps.append(timestamp)
            self.values.append(value)
            await self._cleanup(timestamp)

            # 同步版本

    def update_sync(self, timestamp: float, value: float) -> None:
        """同步更新监控器数据"""
        self.timestamps.append(timestamp)
        self.values.append(value)
        self._cleanup_sync(timestamp)

    async def update_no_value(self, timestamp: float) -> None:
        """异步更新时间戳但不添加新值，用于清理过期数据"""
        async with self._lock:
            await self._cleanup(timestamp)

            # 同步版本

    def update_no_value_sync(self, timestamp: float) -> None:
        """同步更新时间戳但不添加新值"""
        self._cleanup_sync(timestamp)

    async def _cleanup(self, current_time: float) -> None:
        """异步清理过期数据"""
        while self.timestamps and self.timestamps[0] < current_time - self.window_size:
            self.timestamps.popleft()
            self.values.popleft()

            # 同步版本

    def _cleanup_sync(self, current_time: float) -> None:
        """同步清理过期数据"""
        while self.timestamps and self.timestamps[0] < current_time - self.window_size:
            self.timestamps.popleft()
            self.values.popleft()

    async def get_average(self) -> float:
        """异步获取当前平均值"""
        async with self._lock:
            if not self.values:
                return 0.0
            return sum(self.values) / len(self.values)

            # 同步版本

    def get_average_sync(self) -> float:
        """同步获取当前平均值"""
        if not self.values:
            return 0.0
        return sum(self.values) / len(self.values)

    async def get_sum(self) -> float:
        """异步获取当前总和"""
        async with self._lock:
            return sum(self.values) if self.values else 0.0

            # 同步版本

    def get_sum_sync(self) -> float:
        """同步获取当前总和"""
        return sum(self.values) if self.values else 0.0


class RequestStatsCollector(MetricCollector):
    """请求统计指标收集器"""

    def __init__(self, sliding_window_size: float = 60.0, registry=None):
        """
        初始化请求统计监控器

        Args:
            sliding_window_size: 滑动窗口大小，单位为秒
            registry: 指标注册中心
        """
        super().__init__(registry or get_metric_registry())
        self.sliding_window_size = sliding_window_size
        self.qps_monitors: Dict[str, MovingAverageMonitor] = {}
        self.ttft_monitors: Dict[str, MovingAverageMonitor] = {}

        # 请求到达时间: (engine_url, request_id) -> timestamp
        self.request_start_time: Dict[Tuple[str, str], float] = {}
        # 首个token接收时间: (engine_url, request_id) -> timestamp
        self.first_token_time: Dict[Tuple[str, str], float] = {}

        # 不同阶段的请求数量
        self.in_prefill_requests: Dict[str, int] = {}
        self.in_decoding_requests: Dict[str, int] = {}
        self.finished_requests: Dict[str, int] = {}

        # 总体延迟和解码长度监控器
        self.latency_monitors: Dict[str, MovingAverageMonitor] = {}
        self.decoding_length_monitors: Dict[str, MovingAverageMonitor] = {}

        # 被交换出的请求计数
        self.swapped_requests: Dict[str, int] = {}

        self.first_query_time: float = None

        # 注册指标
        self._register_metrics()

        # 用于保护共享数据的锁
        self._lock = asyncio.Lock()

    def _register_metrics(self):
        """注册请求相关指标"""
        # QPS指标
        self.qps_metric = create_gauge(
            "request_qps",
            "Queries per second",
            ["engine_url"]
        )

        # TTFT指标
        self.ttft_metric = create_histogram(
            "request_ttft_seconds",
            "Time to first token in seconds",
            [0.01, 0.05, 0.1, 0.5, 1, 2, 5],
            ["engine_url"]
        )

        # 请求计数指标
        self.prefill_requests = create_gauge(
            "request_in_prefill",
            "Number of requests in prefill stage",
            ["engine_url"]
        )

        self.decoding_requests = create_gauge(
            "request_in_decoding",
            "Number of requests in decoding stage",
            ["engine_url"]
        )

        self.total_finished = create_counter(
            "request_finished_total",
            "Total number of finished requests",
            ["engine_url"]
        )

        # 延迟指标
        self.latency_metric = create_histogram(
            "request_latency_seconds",
            "Overall request latency in seconds",
            [0.1, 0.5, 1, 2, 5, 10, 30, 60],
            ["engine_url"]
        )

        # 解码长度指标
        self.decoding_length = create_histogram(
            "request_decoding_length_seconds",
            "Decoding length in seconds",
            [0.1, 0.5, 1, 2, 5, 10, 30],
            ["engine_url"]
        )

        # 交换请求指标
        self.swapped_metric = create_counter(
            "request_swapped_total",
            "Total number of swapped requests",
            ["engine_url"]
        )

    async def on_new_request(self, engine_url: str, request_id: str, timestamp: float):
        """异步通知监控器有新请求创建"""
        async with self._lock:
            self.request_start_time[(engine_url, request_id)] = timestamp

            if engine_url not in self.in_prefill_requests:
                self.in_prefill_requests[engine_url] = 0
            self.in_prefill_requests[engine_url] += 1

            # 更新指标
            await self.prefill_requests.set(
                self.in_prefill_requests[engine_url],
                {"engine_url": engine_url}
            )

            if engine_url not in self.qps_monitors:
                self.qps_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)
            await self.qps_monitors[engine_url].update(timestamp, 1)

            # 更新QPS指标
            qps = await self.qps_monitors[engine_url].get_sum() / self.sliding_window_size
            await self.qps_metric.set(qps, {"engine_url": engine_url})

            if engine_url not in self.latency_monitors:
                self.latency_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

            if self.first_query_time is None:
                self.first_query_time = timestamp

                # 同步版本

    def on_new_request_sync(self, engine_url: str, request_id: str, timestamp: float):
        """同步通知监控器有新请求创建"""
        self.request_start_time[(engine_url, request_id)] = timestamp

        if engine_url not in self.in_prefill_requests:
            self.in_prefill_requests[engine_url] = 0
        self.in_prefill_requests[engine_url] += 1

        # 更新指标
        self.prefill_requests.set_sync(
            self.in_prefill_requests[engine_url],
            {"engine_url": engine_url}
        )

        if engine_url not in self.qps_monitors:
            self.qps_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)
        self.qps_monitors[engine_url].update_sync(timestamp, 1)

        # 更新QPS指标
        qps = self.qps_monitors[engine_url].get_sum_sync() / self.sliding_window_size
        self.qps_metric.set_sync(qps, {"engine_url": engine_url})

        if engine_url not in self.latency_monitors:
            self.latency_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

        if self.first_query_time is None:
            self.first_query_time = timestamp

    async def on_request_response(self, engine_url: str, request_id: str, timestamp: float):
        """异步通知监控器请求收到首个响应"""
        async with self._lock:
            if (engine_url, request_id) not in self.request_start_time:
                return

                # 记录首个token时间
            self.first_token_time[(engine_url, request_id)] = timestamp

            if engine_url not in self.in_decoding_requests:
                self.in_decoding_requests[engine_url] = 0

                # 更新状态
            self.in_prefill_requests[engine_url] = max(
                0, self.in_prefill_requests.get(engine_url, 1) - 1
            )
            self.in_decoding_requests[engine_url] += 1

            # 更新指标
            await self.prefill_requests.set(
                self.in_prefill_requests[engine_url],
                {"engine_url": engine_url}
            )
            await self.decoding_requests.set(
                self.in_decoding_requests[engine_url],
                {"engine_url": engine_url}
            )

            if engine_url not in self.ttft_monitors:
                self.ttft_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

                # 计算TTFT
            ttft = timestamp - self.request_start_time[(engine_url, request_id)]
            await self.ttft_monitors[engine_url].update(timestamp, ttft)

            # 更新TTFT指标
            await self.ttft_metric.observe(
                ttft,
                {"engine_url": engine_url}
            )

            # 同步版本

    def on_request_response_sync(self, engine_url: str, request_id: str, timestamp: float):
        """同步通知监控器请求收到首个响应"""
        if (engine_url, request_id) not in self.request_start_time:
            return

            # 记录首个token时间
        self.first_token_time[(engine_url, request_id)] = timestamp

        if engine_url not in self.in_decoding_requests:
            self.in_decoding_requests[engine_url] = 0

            # 更新状态
        self.in_prefill_requests[engine_url] = max(
            0, self.in_prefill_requests.get(engine_url, 1) - 1
        )
        self.in_decoding_requests[engine_url] += 1

        # 更新指标
        self.prefill_requests.set_sync(
            self.in_prefill_requests[engine_url],
            {"engine_url": engine_url}
        )
        self.decoding_requests.set_sync(
            self.in_decoding_requests[engine_url],
            {"engine_url": engine_url}
        )

        if engine_url not in self.ttft_monitors:
            self.ttft_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

            # 计算TTFT
        ttft = timestamp - self.request_start_time[(engine_url, request_id)]
        self.ttft_monitors[engine_url].update_sync(timestamp, ttft)

        # 更新TTFT指标
        self.ttft_metric.observe_sync(
            ttft,
            {"engine_url": engine_url}
        )

    async def on_request_complete(self, engine_url: str, request_id: str, timestamp: float):
        """异步通知监控器请求已完成"""
        async with self._lock:
            if engine_url not in self.finished_requests:
                self.finished_requests[engine_url] = 0

                # 更新状态
            self.in_decoding_requests[engine_url] = max(
                0, self.in_decoding_requests.get(engine_url, 1) - 1
            )
            self.finished_requests[engine_url] += 1

            # 更新指标
            await self.decoding_requests.set(
                self.in_decoding_requests[engine_url],
                {"engine_url": engine_url}
            )
            await self.total_finished.inc(
                1,
                {"engine_url": engine_url}
            )

            # 计算和记录总体延迟
            if request_start_time := self.request_start_time.get((engine_url, request_id)):
                total_latency = timestamp - request_start_time

                if engine_url not in self.latency_monitors:
                    self.latency_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

                await self.latency_monitors[engine_url].update(timestamp, total_latency)

                # 更新延迟指标
                await self.latency_metric.observe(
                    total_latency,
                    {"engine_url": engine_url}
                )

                # 如果有首个token时间，计算解码时长
                if first_token_time := self.first_token_time.get((engine_url, request_id)):
                    decoding_length = timestamp - first_token_time

                    if engine_url not in self.decoding_length_monitors:
                        self.decoding_length_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

                    await self.decoding_length_monitors[engine_url].update(timestamp, decoding_length)

                    # 更新解码长度指标
                    await self.decoding_length.observe(
                        decoding_length,
                        {"engine_url": engine_url}
                    )

                    # 清理请求记录
                self.request_start_time.pop((engine_url, request_id), None)
                self.first_token_time.pop((engine_url, request_id), None)

                # 同步版本

    def on_request_complete_sync(self, engine_url: str, request_id: str, timestamp: float):
        """同步通知监控器请求已完成"""
        if engine_url not in self.finished_requests:
            self.finished_requests[engine_url] = 0

            # 更新状态
        self.in_decoding_requests[engine_url] = max(
            0, self.in_decoding_requests.get(engine_url, 1) - 1
        )
        self.finished_requests[engine_url] += 1

        # 更新指标
        self.decoding_requests.set_sync(
            self.in_decoding_requests[engine_url],
            {"engine_url": engine_url}
        )
        self.total_finished.inc_sync(
            1,
            {"engine_url": engine_url}
        )

        # 计算和记录总体延迟
        if request_start_time := self.request_start_time.get((engine_url, request_id)):
            total_latency = timestamp - request_start_time

            if engine_url not in self.latency_monitors:
                self.latency_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

            self.latency_monitors[engine_url].update_sync(timestamp, total_latency)

            # 更新延迟指标
            self.latency_metric.observe_sync(
                total_latency,
                {"engine_url": engine_url}
            )

            # 如果有首个token时间，计算解码时长
            if first_token_time := self.first_token_time.get((engine_url, request_id)):
                decoding_length = timestamp - first_token_time

                if engine_url not in self.decoding_length_monitors:
                    self.decoding_length_monitors[engine_url] = MovingAverageMonitor(self.sliding_window_size)

                self.decoding_length_monitors[engine_url].update_sync(timestamp, decoding_length)

                # 更新解码长度指标
                self.decoding_length.observe_sync(
                    decoding_length,
                    {"engine_url": engine_url}
                )

                # 清理请求记录
            self.request_start_time.pop((engine_url, request_id), None)
            self.first_token_time.pop((engine_url, request_id), None)

    async def on_request_swapped(self, engine_url: str, request_id: str, timestamp: float):
        """异步通知监控器请求已从GPU交换到CPU"""
        async with self._lock:
            if engine_url not in self.swapped_requests:
                self.swapped_requests[engine_url] = 0
            self.swapped_requests[engine_url] += 1

            # 更新指标
            await self.swapped_metric.inc(
                1,
                {"engine_url": engine_url}
            )

            # 同步版本

    def on_request_swapped_sync(self, engine_url: str, request_id: str, timestamp: float):
        """同步通知监控器请求已从GPU交换到CPU"""
        if engine_url not in self.swapped_requests:
            self.swapped_requests[engine_url] = 0
        self.swapped_requests[engine_url] += 1

        # 更新指标
        self.swapped_metric.inc_sync(
            1,
            {"engine_url": engine_url}
        )

    async def get_request_stats(self, current_time: float) -> Dict[str, RequestStats]:
        """异步获取各服务引擎的请求统计信息"""
        async with self._lock:
            ret = {}
            urls = set(self.in_prefill_requests.keys()).union(
                set(self.in_decoding_requests.keys())
            )

            for engine_url in urls:
                # 计算QPS
                if engine_url not in self.qps_monitors:
                    qps = 0.0
                else:
                    await self.qps_monitors[engine_url].update_no_value(current_time)
                    qps = await self.qps_monitors[engine_url].get_sum() / self.sliding_window_size

                # 计算TTFT
                if engine_url not in self.ttft_monitors:
                    ttft = -1.0
                else:
                    await self.ttft_monitors[engine_url].update_no_value(current_time)
                    ttft = await self.ttft_monitors[engine_url].get_average()

                # 获取各状态请求数
                in_prefill = self.in_prefill_requests.get(engine_url, 0)
                in_decoding = self.in_decoding_requests.get(engine_url, 0)
                finished = self.finished_requests.get(engine_url, 0)

                # 计算平均解码长度
                if engine_url in self.decoding_length_monitors:
                    avg_dec_len = await self.decoding_length_monitors[engine_url].get_average()
                else:
                    avg_dec_len = -1.0

                # 计算平均延迟
                if engine_url in self.latency_monitors:
                    avg_lat = await self.latency_monitors[engine_url].get_average()
                else:
                    avg_lat = -1.0

                # 默认平均token间延迟为-1
                avg_itl_val = -1.0

                # 获取交换请求数
                swapped = self.swapped_requests.get(engine_url, 0)

                # 创建统计对象
                ret[engine_url] = RequestStats(
                    qps=qps,
                    ttft=ttft,
                    in_prefill_requests=in_prefill,
                    in_decoding_requests=in_decoding,
                    finished_requests=finished,
                    uptime=(
                        current_time - self.first_query_time if self.first_query_time else 0
                    ),
                    avg_decoding_length=avg_dec_len,
                    avg_latency=avg_lat,
                    avg_itl=avg_itl_val,
                    num_swapped_requests=swapped,
                )

            return ret

    # 同步版本
    def get_request_stats_sync(self, current_time: float) -> Dict[str, RequestStats]:
        """同步获取各服务引擎的请求统计信息"""
        ret = {}
        urls = set(self.in_prefill_requests.keys()).union(
            set(self.in_decoding_requests.keys())
        )

        for engine_url in urls:
            # 计算QPS
            if engine_url not in self.qps_monitors:
                qps = 0.0
            else:
                self.qps_monitors[engine_url].update_no_value_sync(current_time)
                qps = self.qps_monitors[engine_url].get_sum_sync() / self.sliding_window_size

            # 计算TTFT
            if engine_url not in self.ttft_monitors:
                ttft = -1.0
            else:
                self.ttft_monitors[engine_url].update_no_value_sync(current_time)
                ttft = self.ttft_monitors[engine_url].get_average_sync()

            # 获取各状态请求数
            in_prefill = self.in_prefill_requests.get(engine_url, 0)
            in_decoding = self.in_decoding_requests.get(engine_url, 0)
            finished = self.finished_requests.get(engine_url, 0)

            # 计算平均解码长度
            if engine_url in self.decoding_length_monitors:
                avg_dec_len = self.decoding_length_monitors[engine_url].get_average_sync()
            else:
                avg_dec_len = -1.0

            # 计算平均延迟
            if engine_url in self.latency_monitors:
                avg_lat = self.latency_monitors[engine_url].get_average_sync()
            else:
                avg_lat = -1.0

            # 默认平均token间延迟为-1
            avg_itl_val = -1.0

            # 获取交换请求数
            swapped = self.swapped_requests.get(engine_url, 0)

            # 创建统计对象
            ret[engine_url] = RequestStats(
                qps=qps,
                ttft=ttft,
                in_prefill_requests=in_prefill,
                in_decoding_requests=in_decoding,
                finished_requests=finished,
                uptime=(
                    current_time - self.first_query_time if self.first_query_time else 0
                ),
                avg_decoding_length=avg_dec_len,
                avg_latency=avg_lat,
                avg_itl=avg_itl_val,
                num_swapped_requests=swapped,
            )

        return ret

    async def collect(self) -> Dict[str, RequestStats]:
        """异步收集当前请求统计信息"""
        return await self.get_request_stats(time.time())

    @property
    def collection_interval(self) -> float:
        """收集间隔，秒"""
        return 5.0  # 更新统计信息的频率
