# metrics_system/collectors/event_collector.py
import asyncio
import time
from typing import Dict, Any, Set, Optional
from dataclasses import dataclass, field
from collections import defaultdict

from ..core.metric_core import MetricCollector, logger
from ..core.registry import get_metric_registry, create_gauge, create_counter


@dataclass
class EventStatsBase:
    """事件统计基类"""
    event_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    start_time: float = field(default_factory=time.time)
    last_update_time: float = field(default_factory=time.time)


@dataclass
class KVEventStats(EventStatsBase):
    """KV事件统计类"""
    blocks_tracked: int = 0
    blocks_removed: int = 0
    blocks_cleared: int = 0
    active_blocks: Set[int] = field(default_factory=set)
    total_tokens_processed: int = 0
    instances_tracked: Set[str] = field(default_factory=set)
    instance_block_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    instance_token_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


class EventStatsCollector(MetricCollector):
    """事件统计指标收集器"""

    def __init__(self, registry=None):
        """
        初始化事件统计监控器

        Args:
            registry: 指标注册中心
        """
        super().__init__(registry or get_metric_registry())

        # 各类型事件统计
        self.kv_stats = KVEventStats()
        self.scheduler_stats = EventStatsBase()
        self.custom_event_stats: Dict[str, EventStatsBase] = {}

        # 注册指标
        self._register_metrics()

        # 用于保护共享数据的锁
        self._lock = asyncio.Lock()

    def _register_metrics(self):
        """注册事件相关指标"""
        # KV缓存指标
        self.kv_blocks_tracked = create_gauge(
            "event_kv_blocks_tracked",
            "Number of KV blocks being tracked"
        )

        self.kv_blocks_removed = create_counter(
            "event_kv_blocks_removed_total",
            "Total number of KV blocks removed"
        )

        self.kv_blocks_cleared = create_counter(
            "event_kv_blocks_cleared_total",
            "Total number of KV blocks cleared"
        )

        self.kv_active_blocks = create_gauge(
            "event_kv_active_blocks",
            "Number of active KV blocks"
        )

        self.kv_tokens_processed = create_counter(
            "event_kv_tokens_processed_total",
            "Total number of tokens processed in KV cache"
        )

        self.kv_instances_tracked = create_gauge(
            "event_kv_instances_tracked",
            "Number of KV cache instances being tracked"
        )

        # 事件计数指标
        self.event_counts = create_counter(
            "events_total",
            "Total number of events by type",
            ["event_type"]
        )

    async def track_kv_block_added(self, block_id: int, instance_id: str):
        """异步跟踪KV块添加事件"""
        async with self._lock:
            self.kv_stats.blocks_tracked += 1
            self.kv_stats.active_blocks.add(block_id)
            self.kv_stats.instances_tracked.add(instance_id)
            self.kv_stats.instance_block_counts[instance_id] += 1
            self.kv_stats.event_counts["kv_block_added"] += 1
            self.kv_stats.last_update_time = time.time()

            # 更新指标
            await self.kv_blocks_tracked.set(self.kv_stats.blocks_tracked)
            await self.kv_active_blocks.set(len(self.kv_stats.active_blocks))
            await self.kv_instances_tracked.set(len(self.kv_stats.instances_tracked))
            await self.event_counts.inc(1, {"event_type": "kv_block_added"})

    # 同步版本
    def track_kv_block_added_sync(self, block_id: int, instance_id: str):
        """同步跟踪KV块添加事件"""
        self.kv_stats.blocks_tracked += 1
        self.kv_stats.active_blocks.add(block_id)
        self.kv_stats.instances_tracked.add(instance_id)
        self.kv_stats.instance_block_counts[instance_id] += 1
        self.kv_stats.event_counts["kv_block_added"] += 1
        self.kv_stats.last_update_time = time.time()

        # 更新指标
        self.kv_blocks_tracked.set_sync(self.kv_stats.blocks_tracked)
        self.kv_active_blocks.set_sync(len(self.kv_stats.active_blocks))
        self.kv_instances_tracked.set_sync(len(self.kv_stats.instances_tracked))
        self.event_counts.inc_sync(1, {"event_type": "kv_block_added"})

    async def track_kv_block_removed(self, block_id: int, instance_id: str):
        """异步跟踪KV块移除事件"""
        async with self._lock:
            self.kv_stats.blocks_removed += 1
            self.kv_stats.active_blocks.discard(block_id)
            self.kv_stats.instance_block_counts[instance_id] = max(
                0, self.kv_stats.instance_block_counts[instance_id] - 1
            )
            self.kv_stats.event_counts["kv_block_removed"] += 1
            self.kv_stats.last_update_time = time.time()

            # 更新指标
            await self.kv_blocks_removed.inc(1)
            await self.kv_active_blocks.set(len(self.kv_stats.active_blocks))
            await self.event_counts.inc(1, {"event_type": "kv_block_removed"})

    # 同步版本
    def track_kv_block_removed_sync(self, block_id: int, instance_id: str):
        """同步跟踪KV块移除事件"""
        self.kv_stats.blocks_removed += 1
        self.kv_stats.active_blocks.discard(block_id)
        self.kv_stats.instance_block_counts[instance_id] = max(
            0, self.kv_stats.instance_block_counts[instance_id] - 1
        )
        self.kv_stats.event_counts["kv_block_removed"] += 1
        self.kv_stats.last_update_time = time.time()

        # 更新指标
        self.kv_blocks_removed.inc_sync(1)
        self.kv_active_blocks.set_sync(len(self.kv_stats.active_blocks))
        self.event_counts.inc_sync(1, {"event_type": "kv_block_removed"})

    async def track_kv_cache_cleared(self, instance_id: str):
        """异步跟踪KV缓存清除事件"""
        async with self._lock:
            self.kv_stats.blocks_cleared += 1
            self.kv_stats.instance_block_counts[instance_id] = 0
            self.kv_stats.event_counts["kv_cache_cleared"] += 1
            self.kv_stats.last_update_time = time.time()

            # 更新指标
            await self.kv_blocks_cleared.inc(1)
            await self.event_counts.inc(1, {"event_type": "kv_cache_cleared"})

    # 同步版本
    def track_kv_cache_cleared_sync(self, instance_id: str):
        """同步跟踪KV缓存清除事件"""
        self.kv_stats.blocks_cleared += 1
        self.kv_stats.instance_block_counts[instance_id] = 0
        self.kv_stats.event_counts["kv_cache_cleared"] += 1
        self.kv_stats.last_update_time = time.time()

        # 更新指标
        self.kv_blocks_cleared.inc_sync(1)
        self.event_counts.inc_sync(1, {"event_type": "kv_cache_cleared"})

    async def track_tokens_processed(self, token_count: int, instance_id: str):
        """异步跟踪处理的token数量"""
        async with self._lock:
            self.kv_stats.total_tokens_processed += token_count
            self.kv_stats.instance_token_counts[instance_id] += token_count
            self.kv_stats.event_counts["tokens_processed"] += 1
            self.kv_stats.last_update_time = time.time()

            # 更新指标
            await self.kv_tokens_processed.inc(token_count)
            await self.event_counts.inc(1, {"event_type": "tokens_processed"})

    # 同步版本
    def track_tokens_processed_sync(self, token_count: int, instance_id: str):
        """同步跟踪处理的token数量"""
        self.kv_stats.total_tokens_processed += token_count
        self.kv_stats.instance_token_counts[instance_id] += token_count
        self.kv_stats.event_counts["tokens_processed"] += 1
        self.kv_stats.last_update_time = time.time()

        # 更新指标
        self.kv_tokens_processed.inc_sync(token_count)
        self.event_counts.inc_sync(1, {"event_type": "tokens_processed"})

    async def track_scheduler_event(self, event_type: str):
        """异步跟踪调度器事件"""
        async with self._lock:
            self.scheduler_stats.event_counts[event_type] += 1
            self.scheduler_stats.last_update_time = time.time()

            # 更新指标
            await self.event_counts.inc(1, {"event_type": event_type})

    # 同步版本
    def track_scheduler_event_sync(self, event_type: str):
        """同步跟踪调度器事件"""
        self.scheduler_stats.event_counts[event_type] += 1
        self.scheduler_stats.last_update_time = time.time()

        # 更新指标
        self.event_counts.inc_sync(1, {"event_type": event_type})

    async def track_custom_event(self, category: str, event_type: str, count: int = 1):
        """异步跟踪自定义事件"""
        async with self._lock:
            if category not in self.custom_event_stats:
                self.custom_event_stats[category] = EventStatsBase()

            self.custom_event_stats[category].event_counts[event_type] += count
            self.custom_event_stats[category].last_update_time = time.time()

            # 更新指标
            full_event_type = f"{category}_{event_type}"
            await self.event_counts.inc(count, {"event_type": full_event_type})

    # 同步版本
    def track_custom_event_sync(self, category: str, event_type: str, count: int = 1):
        """同步跟踪自定义事件"""
        if category not in self.custom_event_stats:
            self.custom_event_stats[category] = EventStatsBase()

        self.custom_event_stats[category].event_counts[event_type] += count
        self.custom_event_stats[category].last_update_time = time.time()

        # 更新指标
        full_event_type = f"{category}_{event_type}"
        self.event_counts.inc_sync(count, {"event_type": full_event_type})

    async def get_kv_stats(self) -> KVEventStats:
        """异步获取KV事件统计信息"""
        async with self._lock:
            # 返回深拷贝避免并发修改
            import copy
            return copy.deepcopy(self.kv_stats)

    async def get_scheduler_stats(self) -> EventStatsBase:
        """异步获取调度器事件统计信息"""
        async with self._lock:
            import copy
            return copy.deepcopy(self.scheduler_stats)

    async def get_custom_stats(self, category: str) -> Optional[EventStatsBase]:
        """异步获取自定义事件统计信息"""
        async with self._lock:
            if category not in self.custom_event_stats:
                return None

            import copy
            return copy.deepcopy(self.custom_event_stats[category])

    async def collect(self) -> Dict[str, Any]:
        """异步收集所有事件统计信息"""
        async with self._lock:
            custom_stats = {}
            for k in self.custom_event_stats:
                custom_stats[k] = await self.get_custom_stats(k)

            return {
                "kv_stats": await self.get_kv_stats(),
                "scheduler_stats": await self.get_scheduler_stats(),
                "custom_stats": custom_stats
            }

    @property
    def collection_interval(self) -> float:
        """收集间隔，秒"""
        return 5.0  # 定期更新事件统计
