# metrics_system/collectors/scheduler_collector.py
import asyncio
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from ..core.metric_core import MetricCollector, logger
from ..core.registry import get_metric_registry, create_gauge, create_counter, create_histogram


@dataclass
class SchedulerStats:
    """调度器统计信息数据类"""
    pending_tasks: int = 0
    running_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    avg_wait_time: float = 0.0
    avg_execution_time: float = 0.0
    queue_depth: Dict[str, int] = field(default_factory=dict)


class SchedulerStatsCollector(MetricCollector):
    """调度器统计指标收集器"""

    def __init__(self, registry=None):
        """
        初始化调度器统计监控器

        Args:
            registry: 指标注册中心
        """
        super().__init__(registry or get_metric_registry())

        self.pending_tasks = 0
        self.running_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0

        # 队列深度
        self.queue_depth: Dict[str, int] = {}

        # 用于计算平均等待和执行时间
        self.wait_times: List[float] = []
        self.execution_times: List[float] = []

        # 最大保留的历史记录数
        self.max_history = 1000

        # 任务状态跟踪
        self.task_start_times: Dict[str, float] = {}
        self.task_exec_start_times: Dict[str, float] = {}

        # 注册指标
        self._register_metrics()

        # 用于保护共享数据的锁
        self._lock = asyncio.Lock()

    def _register_metrics(self):
        """注册调度器相关指标"""
        # 任务状态指标
        self.pending_tasks_metric = create_gauge(
            "scheduler_pending_tasks",
            "Number of pending tasks"
        )

        self.running_tasks_metric = create_gauge(
            "scheduler_running_tasks",
            "Number of running tasks"
        )

        self.completed_tasks_metric = create_counter(
            "scheduler_completed_tasks_total",
            "Total number of completed tasks"
        )

        self.failed_tasks_metric = create_counter(
            "scheduler_failed_tasks_total",
            "Total number of failed tasks"
        )

        # 队列深度指标
        self.queue_depth_metric = create_gauge(
            "scheduler_queue_depth",
            "Current queue depth",
            ["queue_name"]
        )

        # 时间指标
        self.wait_time_metric = create_histogram(
            "scheduler_wait_time_seconds",
            "Task wait time in seconds",
            [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5]
        )

        self.execution_time_metric = create_histogram(
            "scheduler_execution_time_seconds",
            "Task execution time in seconds",
            [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30]
        )

    async def on_task_submitted(self, task_id: str, timestamp: float = None):
        """异步记录任务提交事件"""
        timestamp = timestamp or time.time()

        async with self._lock:
            self.pending_tasks += 1
            self.task_start_times[task_id] = timestamp

            # 更新指标
            await self.pending_tasks_metric.set(self.pending_tasks)

    # 同步版本
    def on_task_submitted_sync(self, task_id: str, timestamp: float = None):
        """同步记录任务提交事件"""
        timestamp = timestamp or time.time()

        self.pending_tasks += 1
        self.task_start_times[task_id] = timestamp

        # 更新指标
        self.pending_tasks_metric.set_sync(self.pending_tasks)

    async def on_task_started(self, task_id: str, timestamp: float = None):
        """异步记录任务开始执行事件"""
        timestamp = timestamp or time.time()

        async with self._lock:
            if task_id in self.task_start_times:
                wait_time = timestamp - self.task_start_times[task_id]
                self.wait_times.append(wait_time)

                # 限制历史记录数量
                if len(self.wait_times) > self.max_history:
                    self.wait_times.pop(0)

                # 更新等待时间指标
                await self.wait_time_metric.observe(wait_time)

            self.pending_tasks = max(0, self.pending_tasks - 1)
            self.running_tasks += 1
            self.task_exec_start_times[task_id] = timestamp

            # 更新指标
            await self.pending_tasks_metric.set(self.pending_tasks)
            await self.running_tasks_metric.set(self.running_tasks)

    # 同步版本
    def on_task_started_sync(self, task_id: str, timestamp: float = None):
        """同步记录任务开始执行事件"""
        timestamp = timestamp or time.time()

        if task_id in self.task_start_times:
            wait_time = timestamp - self.task_start_times[task_id]
            self.wait_times.append(wait_time)

            # 限制历史记录数量
            if len(self.wait_times) > self.max_history:
                self.wait_times.pop(0)

            # 更新等待时间指标
            self.wait_time_metric.observe_sync(wait_time)

        self.pending_tasks = max(0, self.pending_tasks - 1)
        self.running_tasks += 1
        self.task_exec_start_times[task_id] = timestamp

        # 更新指标
        self.pending_tasks_metric.set_sync(self.pending_tasks)
        self.running_tasks_metric.set_sync(self.running_tasks)

    async def on_task_completed(self, task_id: str, timestamp: float = None, success: bool = True):
        """异步记录任务完成事件"""
        timestamp = timestamp or time.time()

        async with self._lock:
            self.running_tasks = max(0, self.running_tasks - 1)

            if success:
                self.completed_tasks += 1
                # 更新完成任务计数
                await self.completed_tasks_metric.inc(1)
            else:
                self.failed_tasks += 1
                # 更新失败任务计数
                await self.failed_tasks_metric.inc(1)

            # 计算执行时间
            if task_id in self.task_exec_start_times:
                exec_time = timestamp - self.task_exec_start_times[task_id]
                self.execution_times.append(exec_time)

                # 限制历史记录数量
                if len(self.execution_times) > self.max_history:
                    self.execution_times.pop(0)

                # 更新执行时间指标
                await self.execution_time_metric.observe(exec_time)

                # 清理任务记录
                self.task_exec_start_times.pop(task_id, None)

            # 清理开始时间记录
            self.task_start_times.pop(task_id, None)

            # 更新指标
            await self.running_tasks_metric.set(self.running_tasks)

    # 同步版本
    def on_task_completed_sync(self, task_id: str, timestamp: float = None, success: bool = True):
        """同步记录任务完成事件"""
        timestamp = timestamp or time.time()

        self.running_tasks = max(0, self.running_tasks - 1)

        if success:
            self.completed_tasks += 1
            # 更新完成任务计数
            self.completed_tasks_metric.inc_sync(1)
        else:
            self.failed_tasks += 1
            # 更新失败任务计数
            self.failed_tasks_metric.inc_sync(1)

        # 计算执行时间
        if task_id in self.task_exec_start_times:
            exec_time = timestamp - self.task_exec_start_times[task_id]
            self.execution_times.append(exec_time)

            # 限制历史记录数量
            if len(self.execution_times) > self.max_history:
                self.execution_times.pop(0)

            # 更新执行时间指标
            self.execution_time_metric.observe_sync(exec_time)

            # 清理任务记录
            self.task_exec_start_times.pop(task_id, None)

        # 清理开始时间记录
        self.task_start_times.pop(task_id, None)

        # 更新指标
        self.running_tasks_metric.set_sync(self.running_tasks)

    async def update_queue_depth(self, queue_name: str, depth: int):
        """异步更新队列深度"""
        async with self._lock:
            self.queue_depth[queue_name] = depth

            # 更新队列深度指标
            await self.queue_depth_metric.set(depth, {"queue_name": queue_name})

    # 同步版本
    def update_queue_depth_sync(self, queue_name: str, depth: int):
        """同步更新队列深度"""
        self.queue_depth[queue_name] = depth

        # 更新队列深度指标
        self.queue_depth_metric.set_sync(depth, {"queue_name": queue_name})

    async def get_stats(self) -> SchedulerStats:
        """异步获取当前调度器统计信息"""
        async with self._lock:
            avg_wait_time = sum(self.wait_times) / len(self.wait_times) if self.wait_times else 0.0
            avg_exec_time = sum(self.execution_times) / len(self.execution_times) if self.execution_times else 0.0

            return SchedulerStats(
                pending_tasks=self.pending_tasks,
                running_tasks=self.running_tasks,
                completed_tasks=self.completed_tasks,
                failed_tasks=self.failed_tasks,
                avg_wait_time=avg_wait_time,
                avg_execution_time=avg_exec_time,
                queue_depth=self.queue_depth.copy()
            )

    # 同步版本
    def get_stats_sync(self) -> SchedulerStats:
        """同步获取当前调度器统计信息"""
        avg_wait_time = sum(self.wait_times) / len(self.wait_times) if self.wait_times else 0.0
        avg_exec_time = sum(self.execution_times) / len(self.execution_times) if self.execution_times else 0.0

        return SchedulerStats(
            pending_tasks=self.pending_tasks,
            running_tasks=self.running_tasks,
            completed_tasks=self.completed_tasks,
            failed_tasks=self.failed_tasks,
            avg_wait_time=avg_wait_time,
            avg_execution_time=avg_exec_time,
            queue_depth=self.queue_depth.copy()
        )

    async def collect(self) -> SchedulerStats:
        """异步收集当前调度器统计信息"""
        return await self.get_stats()

    @property
    def collection_interval(self) -> float:
        """收集间隔，秒"""
        return 5.0  # 定期更新调度器统计
