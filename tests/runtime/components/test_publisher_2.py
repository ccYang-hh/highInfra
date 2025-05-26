import random
import hashlib
import sys
import threading
import time
from typing import Dict, List, Optional, Set, Union

import msgspec
import zmq

# 导入前缀缓存查找器
from tmatrix.runtime.components.events_subscriber import (
    BlockInfo, BlockRemoved, BlockStored, KVCacheEvent, AllBlocksCleared,
    KVEventBatch, KVEventHandlerBase, PrefixCacheFinder, SubscriberFactory,
    ZMQEventSubscriber, ZMQInstanceConfig, EventSubscriberConfig
)

from tmatrix.common.logging import init_logger

logger = init_logger("tests/test_publish")


class KVCacheSimulator:
    """模拟vLLM的KV缓存行为"""

    def __init__(self, instance_id: str, token_offset: int = 0, block_size: int = 16):
        self.instance_id = instance_id
        self.token_offset = token_offset
        self.block_size = block_size
        self.active_blocks: Set[int] = set()  # 存储当前活跃的block hash
        self.next_token_id = 1000 + token_offset

    def _generate_block_hash(self, token_ids: List[int]) -> int:
        """基于token序列生成合理的hash值"""
        # 将token_ids和instance_id组合起来生成hash
        content = f"{self.instance_id}:{','.join(map(str, token_ids))}"
        hash_obj = hashlib.md5(content.encode())
        # 取前8字节转为int作为hash值
        return int.from_bytes(hash_obj.digest()[:8], 'big', signed=True)

    def generate_random_event(self):
        """随机生成事件（70%存储，30%移除）"""
        # 如果没有活跃块，只能生成存储事件
        if len(self.active_blocks) == 0:
            return self.generate_random_block_stored_event()

        # 70%概率生成存储事件，30%概率生成移除事件
        if random.random() < 0.7:
            return self.generate_random_block_stored_event()
        else:
            return self.generate_block_removed_event()

    def generate_random_block_stored_event(self, num_blocks: int = 1) -> BlockStored:
        """生成随机块存储事件"""
        # 生成随机token序列
        token_count = random.randint(1, self.block_size)
        token_ids = [self.next_token_id + i for i in range(token_count)]
        self.next_token_id += token_count

        # 基于token序列生成block hash
        block_hashes = []
        for i in range(num_blocks):
            # 为每个块生成不同的token子序列
            start_idx = i * (len(token_ids) // num_blocks) if num_blocks > 1 else 0
            end_idx = (i + 1) * (len(token_ids) // num_blocks) if i < num_blocks - 1 else len(token_ids)
            block_tokens = token_ids[start_idx:end_idx] if num_blocks > 1 else token_ids

            # 添加块索引确保不同块有不同的hash
            block_content = block_tokens + [i] if num_blocks > 1 else block_tokens
            block_hash = self._generate_block_hash(block_content)
            block_hashes.append(block_hash)
            self.active_blocks.add(block_hash)

        # 随机选择父块（从已存在的块中选择，排除当前新生成的块）
        parent_block_hash = None
        if self.active_blocks and random.random() < 0.5:
            parent_candidates = list(self.active_blocks - set(block_hashes))
            if parent_candidates:
                parent_block_hash = random.choice(parent_candidates)

        # 随机LoRA ID
        lora_id = None
        if random.random() < 0.2:
            lora_id = random.randint(1, 10)

        return BlockStored(
            block_hashes=block_hashes,
            parent_block_hash=parent_block_hash,
            token_ids=token_ids,
            block_size=self.block_size,
            lora_id=lora_id
        )

    def generate_block_removed_event(self, max_blocks: int = 3) -> Optional[BlockRemoved]:
        """生成块移除事件 - 只移除已存在的块"""
        if not self.active_blocks:
            return None

        # 随机选择要移除的块数量（不超过现有块数量）
        num_to_remove = min(random.randint(1, max_blocks), len(self.active_blocks))
        blocks_to_remove = random.sample(list(self.active_blocks), num_to_remove)

        # 从活跃集合中移除这些块
        for block in blocks_to_remove:
            self.active_blocks.remove(block)

        return BlockRemoved(block_hashes=blocks_to_remove)


class EventPublisher:
    """简化的事件发布器"""

    def __init__(self, instance_id: str, pub_endpoint: str):
        self.instance_id = instance_id
        self.context = zmq.Context()
        self.pub_socket = self.context.socket(zmq.PUB)
        self.pub_socket.bind(pub_endpoint)
        self.next_seq = 0
        self.encoder = msgspec.msgpack.Encoder()
        logger.info(f"发布器 {self.instance_id} 绑定到 {pub_endpoint}")

    def publish(self, event_batch: KVEventBatch, topic: str = "kv-events"):
        """发布事件批次"""
        payload = self.encoder.encode(event_batch)
        seq_bytes = self.next_seq.to_bytes(8, "big")

        self.pub_socket.send_multipart([
            topic.encode('utf-8'),
            seq_bytes,
            payload
        ])

        # 打印事件信息
        event = event_batch.events[0] if event_batch.events else None
        if isinstance(event, BlockStored):
            hash_str = f"[{', '.join(f'{h:x}' for h in event.block_hashes[:2])}{'...' if len(event.block_hashes) > 2 else ''}]"
            event_info = f"BlockStored(hashes={hash_str}, tokens={event.token_ids[:3]}...)"
        elif isinstance(event, BlockRemoved):
            hash_str = f"[{', '.join(f'{h:x}' for h in event.block_hashes[:2])}{'...' if len(event.block_hashes) > 2 else ''}]"
            event_info = f"BlockRemoved(hashes={hash_str})"
        else:
            event_info = f"{type(event).__name__}"

        logger.info(f"{self.instance_id} 发布序列 {self.next_seq}: {event_info}")
        self.next_seq += 1

    def close(self):
        """关闭发布器"""
        self.pub_socket.close()
        self.context.term()


def run_continuous_publisher():
    """运行持续发布器"""
    logger.info("=== 启动持续事件发布器 ===")

    # 创建发布器和模拟器
    publisher = EventPublisher(
        instance_id="vllm-worker-2",
        pub_endpoint="tcp://*:5559"
    )

    simulator = KVCacheSimulator(
        instance_id="vllm-worker-2",
        token_offset=10000,
        block_size=16
    )

    logger.info("等待3秒让订阅器连接...")
    time.sleep(10)

    try:
        logger.info("开始持续发布随机事件...")
        logger.info(f"当前活跃块数量: {len(simulator.active_blocks)}")

        while True:
            # 生成随机事件
            event = simulator.generate_random_event()
            if event:
                batch = KVEventBatch(ts=time.time(), events=[event])
                publisher.publish(batch)

                # 显示当前活跃块状态
                logger.info(f"当前活跃块数量: {len(simulator.active_blocks)}")

            # 随机等待1-10秒
            wait_time = random.uniform(1, 10)
            logger.info(f"等待 {wait_time:.1f} 秒后发送下一个事件...")
            time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
    except Exception as e:
        logger.error(f"发布过程中出错: {e}", exc_info=True)
    finally:
        publisher.close()
        logger.info("发布器已关闭")


if __name__ == "__main__":
    run_continuous_publisher()
