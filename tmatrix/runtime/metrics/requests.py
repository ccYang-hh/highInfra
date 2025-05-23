import time
import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Set, TYPE_CHECKING

from tmatrix.common.logging import init_logger
logger = init_logger("metrics/kv_events")


class RequestStats:
    """请求统计类"""

    def __init__(self, sliding_window_size: float = 60.0):
        self.pending_requests = 0
        self.active_requests = 0
        self.completed_requests = 0
        self.failed_requests = 0
        self.request_times = {}  # 请求开始时间记录
        self.response_times = {}  # 首次响应时间记录
        self.total_latencies = []  # 总延迟记录
        self.ttft_values = []  # TTFT记录
        self.sliding_window_size = sliding_window_size
        self.last_cleanup = time.time()
        self._lock = asyncio.Lock()  # 用于多线程环境

    async def on_request_start(self, request_id: str):
        async with self._lock:
            self.pending_requests += 1
            self.request_times[request_id] = time.time()
            # 定期清理过期记录
            await self._cleanup_old_records()

    async def on_first_token(self, request_id: str):
        async with self._lock:
            if request_id in self.request_times:
                now = time.time()
                self.pending_requests = max(0, self.pending_requests - 1)
                self.active_requests += 1
                self.response_times[request_id] = now
                # 计算TTFT
                ttft = now - self.request_times[request_id]
                self.ttft_values.append((now, ttft))
            await self._cleanup_old_records()

    async def on_request_complete(self, request_id: str, success: bool = True):
        async with self._lock:
            now = time.time()
            if request_id in self.request_times:
                # 更新计数
                self.active_requests = max(0, self.active_requests - 1)
                if success:
                    self.completed_requests += 1
                else:
                    self.failed_requests += 1

                # 计算总延迟
                latency = now - self.request_times[request_id]
                self.total_latencies.append((now, latency))

                # 清理该请求记录
                self.request_times.pop(request_id, None)
                self.response_times.pop(request_id, None)

            await self._cleanup_old_records()

    async def _cleanup_old_records(self):
        """清理过期的记录"""
        now = time.time()
        # 每10秒清理一次
        if now - self.last_cleanup < 10:
            return

        self.last_cleanup = now
        cutoff = now - self.sliding_window_size

        # 清理过期的延迟记录
        self.ttft_values = [(t, v) for t, v in self.ttft_values if t >= cutoff]
        self.total_latencies = [(t, v) for t, v in self.total_latencies if t >= cutoff]

        # 清理过期的请求记录
        expired_requests = [req_id for req_id, start_time in self.request_times.items()
                            if start_time < cutoff - 3600]  # 超过1小时的视为过期
        for req_id in expired_requests:
            self.request_times.pop(req_id, None)
            self.response_times.pop(req_id, None)

    def get_avg_ttft(self) -> float:
        """获取平均TTFT"""
        if not self.ttft_values:
            return 0
        return sum(v for _, v in self.ttft_values) / len(self.ttft_values)

    def get_avg_latency(self) -> float:
        """获取平均延迟"""
        if not self.total_latencies:
            return 0
        return sum(v for _, v in self.total_latencies) / len(self.total_latencies)

    def to_dict(self) -> Dict[str, Any]:
        """将统计信息转换为字典"""
        return {
            "pending_requests": self.pending_requests,
            "active_requests": self.active_requests,
            "completed_requests": self.completed_requests,
            "failed_requests": self.failed_requests,
            "avg_ttft": self.get_avg_ttft(),
            "avg_latency": self.get_avg_latency(),
            "requests_per_minute": len(self.total_latencies) * (60 / self.sliding_window_size)
        }
