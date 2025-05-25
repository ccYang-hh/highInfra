import enum
import asyncio
from typing import Any, Dict

from .kv_events import KVEventStats
from .requests import RequestStats

from tmatrix.common.logging import init_logger
logger = init_logger("metrics/kv_events")


class StatType(enum.Enum):
    KV_EVENTS = "KV_EVENTS"
    REQUESTS = "REQUESTS"


class MetricsRegistry:
    """指标注册中心，用于集中管理所有指标"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MetricsRegistry, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.event_stats = KVEventStats()
        self.request_stats = RequestStats()
        self._lock = asyncio.Lock()

    def get_stats_instance(self, stat_type: StatType) -> Any:
        if stat_type == StatType.KV_EVENTS:
            return self.event_stats
        elif stat_type == StatType.REQUESTS:
            return self.request_stats
        else:
            raise KeyError(f"统计对象 '{stat_type.value}' 不存在")

    async def get_all_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        async with self._lock:
            metrics = {
                "kv_events_stats": self.event_stats.to_dict(),
                "requests_stats": self.request_stats.to_dict()
            }

            return metrics
