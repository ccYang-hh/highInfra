import concurrent.futures
import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from tmatrix.components.logging import init_logger
from .types import (
    BlockStored, BlockRemoved, AllBlocksCleared, KVCacheEvent, KVEventBatch, BlockInfo, EventType, PrefixMatchResult
)
from .stats import EventStatsBase, KVEventStats
logger = init_logger("events_subscriber/kv_events")


class KVEventHandlerBase(ABC):
    """
    KV事件处理器基类
    定义了事件处理的接口
    """

    def __init__(self, stats: Optional[EventStatsBase] = None):
        """
        初始化KV事件处理器
        参数:
            stats: 统计对象，为None时使用默认实现
        """
        self.stats = stats or KVEventStats()

    def process_event_batch(self, event_batch: KVEventBatch, instance_id: str) -> None:
        """处理事件批次"""
        for event in event_batch.events:
            if isinstance(event, BlockStored):
                self.on_block_stored(event, instance_id, event_batch.ts)
                self.stats.update_on_block_stored(event, instance_id)
            elif isinstance(event, BlockRemoved):
                self.on_block_removed(event, instance_id)
                self.stats.update_on_block_removed(event, instance_id)
            elif isinstance(event, AllBlocksCleared):
                self.on_all_blocks_cleared(instance_id)
                self.stats.update_on_all_blocks_cleared(instance_id)

        self.stats.print_stats()

    @abstractmethod
    def on_block_stored(self, event: BlockStored, instance_id: str, timestamp: float) -> None:
        """块存储事件处理"""
        pass

    @abstractmethod
    def on_block_removed(self, event: BlockRemoved, instance_id: str) -> None:
        """块移除事件处理"""
        pass

    @abstractmethod
    def on_all_blocks_cleared(self, instance_id: str) -> None:
        """所有块清除事件处理"""
        pass

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.stats.get_stats_dict()

    def clear(self) -> None:
        """清空处理器状态"""
        self.stats.clear()


class DefaultKVEventHandler(KVEventHandlerBase):
    """
    默认KV事件处理器实现
    提供基本的事件处理和日志记录
    """

    def on_block_stored(self, event: BlockStored, instance_id: str, timestamp: float) -> None:
        """块存储事件处理"""
        logger.debug(
            f"实例 {instance_id} 块存储: {len(event.block_hashes)} 个块, "
            f"父块哈希: {event.parent_block_hash}, "
            f"token数: {len(event.token_ids)}, "
            f"块大小: {event.block_size}, "
            f"LoRA ID: {event.lora_id}"
        )

    def on_block_removed(self, event: BlockRemoved, instance_id: str) -> None:
        """块移除事件处理"""
        logger.debug(f"实例 {instance_id} 块移除: {len(event.block_hashes)} 个块")

    def on_all_blocks_cleared(self, instance_id: str) -> None:
        """所有块清除事件处理"""
        logger.debug(f"实例 {instance_id} 所有块已清除")


class BlockGraph:
    """
    存储KV缓存块之间的关系图
    使用邻接表表示，支持高效查找父子关系
    """

    def __init__(self):
        # 子块 -> 父块映射
        self.parents: Dict[int, int] = {}
        # 父块 -> 子块集合映射
        self.children: Dict[int, Set[int]] = defaultdict(set)
        # 块锁，防止并发修改冲突
        self.lock = threading.RLock()

    def add_block(self, block_hash: int, parent_hash: Optional[int]) -> None:
        """添加块到图中"""
        with self.lock:
            if parent_hash is not None:
                self.parents[block_hash] = parent_hash
                self.children[parent_hash].add(block_hash)

    def remove_block(self, block_hash: int) -> None:
        """从图中移除块"""
        with self.lock:
            if block_hash in self.parents:
                parent_hash = self.parents[block_hash]
                del self.parents[block_hash]
                if parent_hash in self.children:
                    self.children[parent_hash].discard(block_hash)
                    if not self.children[parent_hash]:
                        del self.children[parent_hash]

            # 移除作为父块的记录
            if block_hash in self.children:
                for child_hash in list(self.children[block_hash]):
                    if child_hash in self.parents:
                        del self.parents[child_hash]
                del self.children[block_hash]

    def get_parent(self, block_hash: int) -> Optional[int]:
        """获取块的父块"""
        with self.lock:
            return self.parents.get(block_hash)

    def get_children(self, block_hash: int) -> Set[int]:
        """获取块的所有子块"""
        with self.lock:
            return self.children.get(block_hash, set())

    def get_ancestor_chain(self, block_hash: int) -> List[int]:
        """获取从该块到根块的祖先链"""
        chain = []
        with self.lock:
            current = block_hash
            while current is not None:
                chain.append(current)
                current = self.parents.get(current)
        return chain

    def clear(self) -> None:
        """清空图"""
        with self.lock:
            self.parents.clear()
            self.children.clear()


class BlockRegistry:
    """
    存储和管理所有KV缓存块的信息
    支持按实例ID和块哈希查找
    """

    def __init__(self):
        # 块哈希 -> 块信息映射
        self.blocks: Dict[int, BlockInfo] = {}
        # 实例ID -> 块哈希集合映射
        self.instance_blocks: Dict[str, Set[int]] = defaultdict(set)
        # 锁，防止并发修改冲突
        self.lock = threading.RLock()

    def register_block(self, block_hash: int, block_info: BlockInfo) -> None:
        """注册块信息"""
        with self.lock:
            self.blocks[block_hash] = block_info
            self.instance_blocks[block_info.instance_id].add(block_hash)

    def unregister_block(self, block_hash: int) -> None:
        """取消注册块信息"""
        with self.lock:
            if block_hash in self.blocks:
                instance_id = self.blocks[block_hash].instance_id
                del self.blocks[block_hash]
                if instance_id in self.instance_blocks:
                    self.instance_blocks[instance_id].discard(block_hash)
                    if not self.instance_blocks[instance_id]:
                        del self.instance_blocks[instance_id]

    def get_block_info(self, block_hash: int) -> Optional[BlockInfo]:
        """获取块信息"""
        with self.lock:
            return self.blocks.get(block_hash)

    def get_instance_blocks(self, instance_id: str) -> Set[int]:
        """获取实例所有的块哈希"""
        with self.lock:
            return self.instance_blocks.get(instance_id, set()).copy()

    def get_all_instances(self) -> List[str]:
        """获取所有实例ID"""
        with self.lock:
            return list(self.instance_blocks.keys())

    def clear_instance(self, instance_id: str) -> None:
        """清除实例的所有块"""
        with self.lock:
            if instance_id in self.instance_blocks:
                for block_hash in list(self.instance_blocks[instance_id]):
                    if block_hash in self.blocks:
                        del self.blocks[block_hash]
                del self.instance_blocks[instance_id]

    def clear(self) -> None:
        """清空注册表"""
        with self.lock:
            self.blocks.clear()
            self.instance_blocks.clear()


class TokenSequenceIndex:
    """
    建立token序列到块哈希的索引
    支持前缀匹配查询
    """

    def __init__(self):
        # token序列前缀 -> 块哈希集合映射，按实例ID分组
        # 键：token序列的元组，值：{实例ID: 块哈希集合}
        self.prefix_index: Dict[Tuple[int, ...], Dict[str, Set[int]]] = {}
        # 锁，防止并发修改冲突
        self.lock = threading.RLock()

    def index_block(self, block_hash: int, token_ids: List[int], instance_id: str) -> None:
        """为块建立前缀索引"""
        with self.lock:
            # 为所有可能的前缀建立索引
            for i in range(1, len(token_ids) + 1):
                prefix = tuple(token_ids[:i])
                if prefix not in self.prefix_index:
                    self.prefix_index[prefix] = {}

                if instance_id not in self.prefix_index[prefix]:
                    self.prefix_index[prefix][instance_id] = set()

                self.prefix_index[prefix][instance_id].add(block_hash)

    def remove_block(self, block_hash: int, token_ids: List[int], instance_id: str) -> None:
        """移除块的前缀索引"""
        with self.lock:
            for i in range(1, len(token_ids) + 1):
                prefix = tuple(token_ids[:i])
                if prefix in self.prefix_index and instance_id in self.prefix_index[prefix]:
                    self.prefix_index[prefix][instance_id].discard(block_hash)
                    if not self.prefix_index[prefix][instance_id]:
                        del self.prefix_index[prefix][instance_id]
                    if not self.prefix_index[prefix]:
                        del self.prefix_index[prefix]

    def find_longest_prefix(self, token_ids: List[int]) -> List[Tuple[str, int, int]]:
        """
        找到最长的前缀匹配
        返回：[(实例ID, 块哈希, 匹配长度)] 列表，按匹配长度降序排序
        """
        matches = []

        with self.lock:
            # 从最长前缀开始尝试匹配
            for i in range(len(token_ids), 0, -1):
                prefix = tuple(token_ids[:i])
                if prefix in self.prefix_index:
                    # 对每个实例的匹配进行处理
                    for instance_id, block_hashes in self.prefix_index[prefix].items():
                        for block_hash in block_hashes:
                            matches.append((instance_id, block_hash, i))

                    # 如果找到匹配，可以提前退出，因为我们只需要最长的前缀
                    # 但我们需要检查所有实例，因此不能在第一个匹配时就退出
                    if matches:
                        break

        # 按匹配长度降序排序
        return sorted(matches, key=lambda x: x[2], reverse=True)

    def clear_instance(self, instance_id: str) -> None:
        """清除实例的所有索引"""
        with self.lock:
            # 找出需要清理的前缀
            prefixes_to_check = list(self.prefix_index.keys())
            for prefix in prefixes_to_check:
                if instance_id in self.prefix_index[prefix]:
                    del self.prefix_index[prefix][instance_id]
                    if not self.prefix_index[prefix]:
                        del self.prefix_index[prefix]

    def clear(self) -> None:
        """清空索引"""
        with self.lock:
            self.prefix_index.clear()


class PrefixCacheFinder(KVEventHandlerBase):
    """
    前缀缓存查找器
    跟踪多个vLLM实例的KV缓存块，支持最长前缀匹配查询
    """

    def __init__(self, stats: Optional[EventStatsBase] = None):
        """
        初始化前缀缓存查找器
        参数:
            stats: 统计对象，为None时使用默认实现
        """
        super().__init__(stats)
        self.block_graph = BlockGraph()
        self.block_registry = BlockRegistry()
        self.token_index = TokenSequenceIndex()

        # 线程池，用于并行处理事件
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="prefix-cache-worker"
        )

        # 用于等待所有任务完成的Future集合
        self.pending_tasks = set()
        self.tasks_lock = threading.Lock()

    def process_event_batch(self, event_batch: KVEventBatch, instance_id: str) -> None:
        """处理事件批次"""
        for event in event_batch.events:
            if isinstance(event, BlockStored):
                self._submit_task(self.on_block_stored, event, instance_id, event_batch.ts)
                self.stats.update_on_block_stored(event, instance_id)
            elif isinstance(event, BlockRemoved):
                self._submit_task(self.on_block_removed, event, instance_id)
                self.stats.update_on_block_removed(event, instance_id)
            elif isinstance(event, AllBlocksCleared):
                self._submit_task(self.on_all_blocks_cleared, instance_id)
                self.stats.update_on_all_blocks_cleared(instance_id)

        self.stats.print_stats()

    def _submit_task(self, func, *args):
        """提交任务到线程池，并跟踪其完成情况"""
        with self.tasks_lock:
            future = self.executor.submit(func, *args)
            self.pending_tasks.add(future)
            future.add_done_callback(self._task_done)

    def _task_done(self, future):
        """任务完成回调"""
        with self.tasks_lock:
            self.pending_tasks.remove(future)
            # 检查任务是否有异常
            try:
                future.result()
            except Exception as e:
                logger.error(f"任务执行出错: {e}", exc_info=True)

    def on_block_stored(self, event: BlockStored, instance_id: str, timestamp: float) -> None:
        """块存储事件处理"""
        for block_hash in event.block_hashes:
            # 创建块信息
            block_info = BlockInfo(
                instance_id=instance_id,
                token_ids=event.token_ids,
                parent_hash=event.parent_block_hash,
                block_size=event.block_size,
                lora_id=event.lora_id,
                timestamp=timestamp
            )

            # 注册块信息
            self.block_registry.register_block(block_hash, block_info)

            # 添加到块图
            self.block_graph.add_block(block_hash, event.parent_block_hash)

            # 建立前缀索引
            self.token_index.index_block(block_hash, event.token_ids, instance_id)

    def on_block_removed(self, event: BlockRemoved, instance_id: str) -> None:
        """块移除事件处理"""
        for block_hash in event.block_hashes:
            # 获取块信息，用于后续清理
            block_info = self.block_registry.get_block_info(block_hash)
            if block_info is None:
                continue

            # 只处理属于该实例的块
            if block_info.instance_id != instance_id:
                logger.warning(f"尝试从实例 {instance_id} 移除属于实例 {block_info.instance_id} 的块 {block_hash}")
                continue

            # 移除前缀索引
            self.token_index.remove_block(block_hash, block_info.token_ids, instance_id)

            # 从块图中移除
            self.block_graph.remove_block(block_hash)

            # 取消注册块
            self.block_registry.unregister_block(block_hash)

    def on_all_blocks_cleared(self, instance_id: str) -> None:
        """处理所有块清除事件"""
        # 获取实例的所有块
        instance_blocks = self.block_registry.get_instance_blocks(instance_id)

        # 逐个移除块
        for block_hash in instance_blocks:
            block_info = self.block_registry.get_block_info(block_hash)
            if block_info:
                # 移除前缀索引
                self.token_index.remove_block(block_hash, block_info.token_ids, instance_id)
                # 从块图中移除
                self.block_graph.remove_block(block_hash)

        # 清除实例的块注册信息
        self.block_registry.clear_instance(instance_id)
        # 清除实例的前缀索引
        self.token_index.clear_instance(instance_id)

        logger.info(f"已清除实例 {instance_id} 的所有块，共 {len(instance_blocks)} 个")

    def find_longest_prefix_match(self, token_ids: List[int]) -> Optional[PrefixMatchResult]:
        """
        查找最长的前缀匹配
        参数:
            token_ids: 输入token序列
        返回:
            匹配结果对象，若无匹配则返回None
        """
        # 查找最长前缀
        matches = self.token_index.find_longest_prefix(token_ids)

        if not matches:
            return None

        # 取最长匹配
        best_instance_id, best_block_hash, match_length = matches[0]
        block_info = self.block_registry.get_block_info(best_block_hash)

        if not block_info:
            logger.warning(f"找到匹配块 {best_block_hash}，但无法获取其信息")
            return None

        return PrefixMatchResult(
            instance_id=best_instance_id,
            block_hash=best_block_hash,
            match_length=match_length,
            block_info=block_info
        )

    def find_all_prefix_matches(self, token_ids: List[int],
                                limit: int = 5) -> List[PrefixMatchResult]:
        """
        查找所有实例的最长前缀匹配
        参数:
            token_ids: 输入token序列
            limit: 返回结果的最大数量
        返回:
            按匹配长度降序排序的匹配结果列表
        """
        matches = self.token_index.find_longest_prefix(token_ids)
        results = []

        # 为每个匹配创建结果对象
        for instance_id, block_hash, match_length in matches[:limit]:
            block_info = self.block_registry.get_block_info(block_hash)
            if block_info:
                results.append(PrefixMatchResult(
                    instance_id=instance_id,
                    block_hash=block_hash,
                    match_length=match_length,
                    block_info=block_info
                ))

        return results

    def wait_for_all_tasks(self, timeout: Optional[float] = None) -> bool:
        """等待所有挂起的任务完成"""
        with self.tasks_lock:
            futures = list(self.pending_tasks)

        if not futures:
            return True

        done, not_done = concurrent.futures.wait(
            futures,
            timeout=timeout,
            return_when=concurrent.futures.ALL_COMPLETED
        )

        return len(not_done) == 0

    def print_stats(self) -> None:
        """打印当前统计信息"""
        self.stats.print_stats(force=True)

    def clear(self) -> None:
        """清空所有数据"""
        # 等待所有任务完成
        self.wait_for_all_tasks(timeout=5.0)

        self.block_graph.clear()
        self.block_registry.clear()
        self.token_index.clear()
        super().clear()

    def shutdown(self) -> None:
        """关闭查找器，释放资源"""
        self.wait_for_all_tasks(timeout=5.0)
        self.executor.shutdown(wait=True)
        self.clear()
