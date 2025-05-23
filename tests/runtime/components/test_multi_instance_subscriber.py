"""
vLLM Prefix Cache 集成测试

使用线程模拟多个vLLM实例，测试前缀缓存查找功能
"""
import random
import sys
import threading
import time
from typing import Dict, List, Optional, Set, Union

import msgspec
import zmq

# 导入前缀缓存查找器
from tmatrix.runtime.components.events_subscriber import (
    BlockRemoved, BlockStored, KVEventBatch,
    PrefixCacheFinder, SubscriberFactory, ZMQInstanceConfig
)

# 配置日志
from tmatrix.common.logging import init_logger
logger = init_logger("tests/test_multi_instance_subscriber")


class KVCacheSimulator:
    """模拟vLLM的KV缓存行为"""

    def __init__(self, instance_id: str, token_offset: int = 0, block_size: int = 16):
        self.instance_id = instance_id
        self.token_offset = token_offset  # 添加token ID偏移
        self.block_size = block_size
        self.active_blocks: Set[int] = set()
        self.next_block_id = 1
        self.next_token_id = 1000 + token_offset  # 使用偏移作为基准

    def generate_random_block_stored_event(self, num_blocks: int = 1) -> BlockStored:
        """生成随机块存储事件"""
        block_hashes = []
        for _ in range(num_blocks):
            block_hash = self.next_block_id
            self.next_block_id += 1
            block_hashes.append(block_hash)
            self.active_blocks.add(block_hash)

        # 随机选择父块，如果有的话
        parent_block_hash = None
        if self.active_blocks and random.random() < 0.7:
            parent_candidates = list(self.active_blocks - set(block_hashes))
            if parent_candidates:
                parent_block_hash = random.choice(parent_candidates)

        # 生成token IDs
        token_ids = [self.next_token_id + i for i in range(random.randint(1, self.block_size))]
        self.next_token_id += len(token_ids)

        # 随机LoRA ID (大多数情况下为None)
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

    def generate_specific_block_stored_event(self, token_ids: List[int]) -> BlockStored:
        """生成指定token ID序列的块存储事件"""
        block_hash = self.next_block_id
        self.next_block_id += 1
        self.active_blocks.add(block_hash)

        parent_block_hash = None
        if self.active_blocks and random.random() < 0.5:
            parent_candidates = list(self.active_blocks - {block_hash})
            if parent_candidates:
                parent_block_hash = random.choice(parent_candidates)

        return BlockStored(
            block_hashes=[block_hash],
            parent_block_hash=parent_block_hash,
            token_ids=token_ids,
            block_size=self.block_size,
            lora_id=None
        )

    def generate_block_removed_event(self, max_blocks: int = 3) -> Optional[BlockRemoved]:
        """生成块移除事件"""
        if not self.active_blocks:
            return None

        # 随机选择要移除的块
        num_to_remove = min(random.randint(1, max_blocks), len(self.active_blocks))
        blocks_to_remove = random.sample(list(self.active_blocks), num_to_remove)

        # 从活跃集合中移除
        for block in blocks_to_remove:
            self.active_blocks.remove(block)

        return BlockRemoved(block_hashes=blocks_to_remove)


class EventPublisher:
    """事件发布器，用于测试"""

    def __init__(self, instance_id: str, pub_port: int, replay_port: int):
        self.instance_id = instance_id
        self.context = zmq.Context()
        self.pub_socket = self.context.socket(zmq.PUB)
        self.pub_socket.bind(f"tcp://127.0.0.1:{pub_port}")

        # 重放套接字
        self.replay_socket = self.context.socket(zmq.ROUTER)
        self.replay_socket.bind(f"tcp://127.0.0.1:{replay_port}")

        self.running = False
        self.next_seq = 0
        self.event_buffer = []
        self.encoder = msgspec.msgpack.Encoder()

        # 创建处理重放请求的线程
        self.replay_thread = threading.Thread(
            target=self._replay_thread_func,
            daemon=True,
            name=f"replay-{instance_id}"
        )

    def start(self):
        """启动发布器"""
        self.running = True
        self.replay_thread.start()
        logger.info(f"发布器 {self.instance_id} 已启动")

    def stop(self):
        """停止发布器"""
        self.running = False
        if self.replay_thread.is_alive():
            self.replay_thread.join(timeout=2.0)
        self.pub_socket.close()
        self.replay_socket.close()
        logger.info(f"发布器 {self.instance_id} 已停止")

    def publish(self, event_batch: KVEventBatch, topic: str = "kv-events"):
        """发布事件批次"""
        # 序列化事件
        payload = self.encoder.encode(event_batch)
        seq_bytes = self.next_seq.to_bytes(8, "big")

        # 发布事件
        self.pub_socket.send_multipart([
            topic.encode('utf-8'),
            seq_bytes,
            payload
        ])

        # 添加到重放缓冲区
        self.event_buffer.append((self.next_seq, payload))
        if len(self.event_buffer) > 100:  # 最多保留100个事件
            self.event_buffer.pop(0)

        # 递增序列号
        self.next_seq += 1

        # 打印事件信息
        event_types = [type(e).__name__ for e in event_batch.events]
        token_info = ""
        if event_batch.events and isinstance(event_batch.events[0], BlockStored):
            tokens = event_batch.events[0].token_ids
            token_info = f", tokens: {tokens[:5]}{'...' if len(tokens) > 5 else ''}"
        logger.info(f"{self.instance_id} 发布序列 {self.next_seq-1}: {event_types}{token_info}")

    def _replay_thread_func(self):
        """处理重放请求的线程"""
        poller = zmq.Poller()
        poller.register(self.replay_socket, zmq.POLLIN)

        while self.running:
            try:
                # 非阻塞轮询
                if dict(poller.poll(timeout=100)):
                    # 接收重放请求
                    frames = self.replay_socket.recv_multipart()
                    if len(frames) != 3:
                        continue

                    identity, empty, seq_bytes = frames
                    start_seq = int.from_bytes(seq_bytes, "big")

                    logger.info(f"{self.instance_id} 收到重放请求，从序列 {start_seq} 开始")

                    # 发送请求范围内的所有事件
                    sent_count = 0
                    for seq, payload in self.event_buffer:
                        if seq >= start_seq:
                            self.replay_socket.send_multipart([
                                identity,
                                b"",
                                seq.to_bytes(8, "big"),
                                payload
                            ])
                            sent_count += 1

                    # 发送结束标记
                    self.replay_socket.send_multipart([
                        identity,
                        b"",
                        (-1).to_bytes(8, "big", signed=True),
                        b""
                    ])

                    logger.info(f"{self.instance_id} 重放完成，已发送 {sent_count} 个事件")

            except Exception as e:
                logger.error(f"重放线程出错: {e}", exc_info=True)
                time.sleep(0.1)


def generate_test_token_sequences():
    """生成测试用的token序列"""
    # 实例1的token序列，基准值为10000
    inst1_sequences = [
        {"name": "inst1_seq1", "tokens": [10001, 10002, 10003, 10004, 10005]},
        {"name": "inst1_seq2", "tokens": [10010, 10011, 10012, 10013]},
        {"name": "inst1_seq3", "tokens": [10020, 10021, 10022]}
    ]

    # 实例2的token序列，基准值为20000
    inst2_sequences = [
        {"name": "inst2_seq1", "tokens": [20001, 20002, 20003, 20004]},
        {"name": "inst2_seq2", "tokens": [20010, 20011, 20012]},
        # 与实例1有部分重叠的序列，测试前缀查找能否找到最长匹配
        {"name": "inst2_seq3", "tokens": [10001, 10002, 10003, 10004, 10005, 10006, 10007]}
    ]

    # 测试查询序列
    test_queries = [
        # 应匹配实例1的inst1_seq1
        {"name": "query1", "tokens": [10001, 10002, 10003], "expected": "inst1"},
        # 应匹配实例2的inst2_seq1
        {"name": "query2", "tokens": [20001, 20002], "expected": "inst2"},
        # 应匹配实例2的inst2_seq3，因为它是最长匹配
        {"name": "query3", "tokens": [10001, 10002, 10003, 10004, 10005, 10006], "expected": "inst2"},
        # 不应该匹配任何序列
        {"name": "query4", "tokens": [30001, 30002, 30003], "expected": None}
    ]

    return {
        "inst1": inst1_sequences,
        "inst2": inst2_sequences,
        "queries": test_queries
    }


def run_integration_test():
    """运行集成测试"""
    logger.info("=== 开始前缀缓存集成测试 ===")

    # 创建前缀缓存查找器
    finder = PrefixCacheFinder()

    # 设置实例
    instances = [
        {"id": "inst1", "pub_port": 5547, "replay_port": 5548, "token_offset": 10000},
        {"id": "inst2", "pub_port": 5549, "replay_port": 5540, "token_offset": 20000}
    ]

    # 创建发布器
    publishers = {}
    simulators = {}

    for instance in instances:
        # 创建并启动发布器
        pub = EventPublisher(
            instance_id=instance["id"],
            pub_port=instance["pub_port"],
            replay_port=instance["replay_port"]
        )
        publishers[instance["id"]] = pub

        # 创建模拟器
        sim = KVCacheSimulator(
            instance_id=instance["id"],
            token_offset=instance["token_offset"]
        )
        simulators[instance["id"]] = sim

        # 启动发布器
        pub.start()

    # 创建ZMQ订阅器
    subscriber = SubscriberFactory.create_zmq_subscriber(finder, topic="kv-events")

    # 添加实例配置
    for instance in instances:
        config = ZMQInstanceConfig(
            instance_id=instance["id"],
            pub_endpoint=f"tcp://127.0.0.1:{instance['pub_port']}",
            replay_endpoint=f"tcp://127.0.0.1:{instance['replay_port']}"
        )
        subscriber.add_instance(config)

    # 启动订阅器
    subscriber.start()
    logger.info("订阅器已启动")

    # 关键修复点：给订阅者一些时间来建立连接
    logger.info("等待2秒让订阅者连接...")
    time.sleep(2)

    try:
        # 获取测试序列
        test_data = generate_test_token_sequences()

        # 发布特定的测试序列
        logger.info("发布测试token序列...")

        for instance_id, sequences in [("inst1", test_data["inst1"]), ("inst2", test_data["inst2"])]:
            pub = publishers[instance_id]
            sim = simulators[instance_id]

            for seq_data in sequences:
                # 生成特定序列的事件
                event = sim.generate_specific_block_stored_event(seq_data["tokens"])
                batch = KVEventBatch(ts=time.time(), events=[event])

                # 发布事件
                pub.publish(batch)
                logger.info(f"已发布 {instance_id} 的序列 {seq_data['name']}: {seq_data['tokens']}")

                # 稍等，让订阅器有时间处理
                time.sleep(0.5)

        # 等待订阅器处理完所有事件
        logger.info("等待5秒让系统处理所有事件...")
        time.sleep(5)

        # 测试前缀查找
        logger.info("\n=== 开始前缀匹配测试 ===")

        for query in test_data["queries"]:
            token_ids = query["tokens"]
            expected = query["expected"]

            logger.info(f"\n测试查询: {query['name']} - {token_ids}")

            # 查找最长前缀
            match = finder.find_longest_prefix_match(token_ids)

            # 验证结果
            if expected is None:
                if match is None:
                    logger.info(f"✅ 测试通过: 预期无匹配，实际无匹配")
                else:
                    logger.error(f"❌ 测试失败: 预期无匹配，实际匹配 {match.instance_id}")
            else:
                if match is None:
                    logger.error(f"❌ 测试失败: 预期匹配 {expected}，实际无匹配")
                elif match.instance_id == expected:
                    logger.info(f"✅ 测试通过: 正确匹配 {match.instance_id}，匹配长度: {match.match_length}")
                    logger.info(f"  匹配块信息: 块哈希={match.block_hash}, token序列: {match.block_info.token_ids}")
                else:
                    logger.error(f"❌ 测试失败: 预期匹配 {expected}，实际匹配 {match.instance_id}")

            # 查找所有前缀匹配
            all_matches = finder.find_all_prefix_matches(token_ids)

            if all_matches:
                logger.info(f"找到 {len(all_matches)} 个前缀匹配:")
                for i, m in enumerate(all_matches):
                    logger.info(f"  {i+1}. 实例: {m.instance_id}, 匹配长度: {m.match_length}, 块哈希: {m.block_hash}")
            else:
                logger.info("没有找到前缀匹配")

        # 测试随机序列
        logger.info("\n=== 发布随机事件测试 ===")
        for instance_id in ["inst1", "inst2"]:
            pub = publishers[instance_id]
            sim = simulators[instance_id]

            # 发布一些随机事件
            for _ in range(5):
                event = sim.generate_random_block_stored_event(num_blocks=1)
                batch = KVEventBatch(ts=time.time(), events=[event])
                pub.publish(batch)
                time.sleep(0.3)

        # 等待处理
        logger.info("等待3秒让系统处理随机事件...")
        time.sleep(3)

        # 打印统计信息
        logger.info("\n=== 系统统计信息 ===")
        stats = finder.get_stats()
        logger.info(f"总块数: {stats['blocks_tracked']}")
        logger.info(f"已处理的token数: {stats['total_tokens_processed']}")
        logger.info(f"跟踪的实例: {stats['instances_tracked']}")

        logger.info("\n所有测试完成!")
        return True

    except Exception as e:
        logger.error(f"测试过程中出错: {e}", exc_info=True)
        return False
    finally:
        # 清理资源
        logger.info("清理资源...")
        subscriber.stop()
        for pub in publishers.values():
            pub.stop()


if __name__ == "__main__":
    success = run_integration_test()
    sys.exit(0 if success else 1)
