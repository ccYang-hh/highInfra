from dataclasses import dataclass
from enum import Enum
from typing import  List, Optional, Union

import msgspec

from tmatrix.common.logging import init_logger
logger = init_logger("events_subscriber/kv_events")


# 事件数据结构定义
class EventBatch(msgspec.Struct, array_like=True, omit_defaults=True, gc=False):
    ts: float
    events: List[any]


class KVCacheEvent(msgspec.Struct, array_like=True, omit_defaults=True, gc=False, tag=True):
    """所有KV缓存相关事件的基类"""


class BlockStored(KVCacheEvent):
    block_hashes: List[int]
    parent_block_hash: Optional[int]
    token_ids: List[int]
    block_size: int
    lora_id: Optional[int]


class BlockRemoved(KVCacheEvent):
    block_hashes: List[int]


class AllBlocksCleared(KVCacheEvent):
    pass


class KVEventBatch(EventBatch):
    events: List[Union[BlockStored, BlockRemoved, AllBlocksCleared]]


@dataclass
class BlockInfo:
    """存储块的元数据"""
    instance_id: str  # vLLM实例ID
    token_ids: List[int]  # 块中的token IDs
    parent_hash: Optional[int]  # 父块哈希
    block_size: int  # 块大小
    lora_id: Optional[int]  # LoRA ID
    timestamp: float  # 创建时间戳


@dataclass
class PrefixMatchResult:
    """前缀匹配结果"""
    instance_id: str  # 持有最长前缀的vLLM实例ID
    block_hash: int  # 最长前缀的块哈希
    match_length: int  # 匹配的token数量
    block_info: BlockInfo  # 块信息


class EventType(Enum):
    """事件类型枚举"""
    BLOCK_STORED = "BlockStored"
    BLOCK_REMOVED = "BlockRemoved"
    ALL_BLOCKS_CLEARED = "AllBlocksCleared"
