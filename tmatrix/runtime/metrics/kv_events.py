import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Set, TYPE_CHECKING

from tmatrix.common.logging import init_logger
logger = init_logger("metrics/kv_events")


if TYPE_CHECKING:
    # metrics优先于events_subscriber初始化
    from tmatrix.runtime.components.events_subscriber import EventType, BlockStored, BlockRemoved


def get_kv_event_stats() -> 'EventStatsBase':
    return KVEventStats()


class EventStatsBase(ABC):
    """
    事件统计基类
    定义了统计功能的接口
    """

    @abstractmethod
    def update_on_block_stored(self, event: "BlockStored", instance_id: str) -> None:
        """更新块存储事件统计"""
        pass

    @abstractmethod
    def update_on_block_removed(self, event: "BlockRemoved", instance_id: str) -> None:
        """更新块移除事件统计"""
        pass

    @abstractmethod
    def update_on_all_blocks_cleared(self, instance_id: str) -> None:
        """更新所有块清除事件统计"""
        pass

    @abstractmethod
    def print_stats(self, force: bool = False) -> None:
        """打印统计信息"""
        pass

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """获取统计信息字典"""
        pass

    @abstractmethod
    def record_instance(self, instance_id: str) -> None:
        """记录实例"""
        pass

    @abstractmethod
    def clear(self) -> None:
        """清空统计"""
        pass


@dataclass
class KVEventStats(EventStatsBase):
    """
    KV事件统计类
    跟踪和记录KV缓存事件的统计信息
    """
    blocks_tracked: int = 0
    blocks_removed: int = 0
    blocks_cleared: int = 0
    active_blocks: Set[int] = field(default_factory=set)
    total_tokens_processed: int = 0
    instances_tracked: Set[str] = field(default_factory=set)
    instance_block_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    instance_token_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    event_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    start_time: float = field(default_factory=time.time)
    last_update_time: float = field(default_factory=time.time)
    last_print_time: float = field(default_factory=time.time)

    def update_on_block_stored(self, event: "BlockStored", instance_id: str) -> None:
        """更新块存储事件统计"""
        from tmatrix.runtime.components.events_subscriber import EventType
        self.blocks_tracked += len(event.block_hashes)
        self.active_blocks.update(event.block_hashes)
        self.total_tokens_processed += len(event.token_ids)
        self.record_instance(instance_id)
        self.instance_block_counts[instance_id] += len(event.block_hashes)
        self.instance_token_counts[instance_id] += len(event.token_ids)
        self.event_counts[EventType.BLOCK_STORED.value] += 1
        self.last_update_time = time.time()

    def update_on_block_removed(self, event: "BlockRemoved", instance_id: str) -> None:
        """更新块移除事件统计"""
        from tmatrix.runtime.components.events_subscriber import EventType
        self.blocks_removed += len(event.block_hashes)
        self.active_blocks.difference_update(event.block_hashes)
        self.record_instance(instance_id)
        self.instance_block_counts[instance_id] -= len(event.block_hashes)
        self.event_counts[EventType.BLOCK_REMOVED.value] += 1
        self.last_update_time = time.time()

    def update_on_all_blocks_cleared(self, instance_id: str) -> None:
        """更新所有块清除事件统计"""
        from tmatrix.runtime.components.events_subscriber import EventType
        self.blocks_cleared += 1
        previous_blocks = self.instance_block_counts.get(instance_id, 0)
        self.blocks_removed += previous_blocks
        self.instance_block_counts[instance_id] = 0
        self.event_counts[EventType.ALL_BLOCKS_CLEARED.value] += 1
        self.last_update_time = time.time()

        # 重新计算活跃块（较慢但准确）
        # 在实际中可能需要更高效的方法来跟踪每个实例的块
        self.active_blocks.clear()

    def print_stats(self, force: bool = False) -> None:
        """定期打印统计信息"""
        now = time.time()
        if force or (now - self.last_print_time >= 5.0):  # 每5秒打印一次
            elapsed = now - self.start_time
            logger.info(
                f"统计(运行时间: {elapsed:.1f}s): "
                f"块存储: {self.blocks_tracked}, "
                f"块移除: {self.blocks_removed}, "
                f"清除: {self.blocks_cleared}, "
                f"活跃块: {len(self.active_blocks)}, "
                f"处理的token数: {self.total_tokens_processed}, "
                f"实例数: {len(self.instances_tracked)}"
            )
            self.last_print_time = now

    def to_dict(self) -> Dict[str, Any]:
        """获取统计信息字典"""
        return {
            "blocks_tracked": self.blocks_tracked,
            "blocks_removed": self.blocks_removed,
            "blocks_cleared": self.blocks_cleared,
            "active_blocks": len(self.active_blocks),
            "total_tokens_processed": self.total_tokens_processed,
            "instances_tracked": list(self.instances_tracked),
            "instance_block_counts": dict(self.instance_block_counts),
            "instance_token_counts": dict(self.instance_token_counts),
            "event_counts": dict(self.event_counts),
            "uptime": time.time() - self.start_time,
            "last_update_time": self.last_update_time
        }

    def record_instance(self, instance_id: str) -> None:
        """记录实例"""
        self.instances_tracked.add(instance_id)

    def clear(self) -> None:
        """清空统计"""
        self.blocks_tracked = 0
        self.blocks_removed = 0
        self.blocks_cleared = 0
        self.active_blocks.clear()
        self.total_tokens_processed = 0
        self.instances_tracked.clear()
        self.instance_block_counts.clear()
        self.instance_token_counts.clear()
        self.event_counts.clear()
        self.start_time = time.time()
        self.last_update_time = time.time()
        self.last_print_time = time.time()
