"""
vLLM KV Cache Event Publisher Simulator

该模块提供了一个简单的vLLM KV缓存事件发布器模拟器，
用于测试KV事件订阅系统而不需要实际运行vLLM。
"""
import argparse
import logging
import random
import signal
import sys
import threading
import time
from datetime import datetime
from typing import List, Optional, Set, Union

import msgspec
import zmq

# 配置日志
from tmatrix.common.logging import init_logger
logger = init_logger("plugins/routers/kvevent_publisher")


# 事件数据结构定义，需与subscriber使用相同结构
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


class KVCacheSimulator:
    """模拟vLLM的KV缓存行为"""

    def __init__(self, block_size: int = 16):
        self.block_size = block_size
        self.active_blocks: Set[int] = set()
        self.next_block_id = 1
        self.next_token_id = 1000

    def generate_block_stored_event(self, num_blocks: int = 1) -> BlockStored:
        """生成块存储事件"""
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

    def generate_all_blocks_cleared_event(self) -> AllBlocksCleared:
        """生成所有块清除事件"""
        self.active_blocks.clear()
        return AllBlocksCleared()

    def generate_random_event(self) -> KVCacheEvent:
        """生成随机事件"""
        r = random.random()

        # 70% 概率生成块存储事件
        if r < 0.7:
            return self.generate_block_stored_event(
                num_blocks=random.randint(1, 3)
            )

        # 25% 概率生成块移除事件，如果有块可移除
        elif r < 0.95 and self.active_blocks:
            return self.generate_block_removed_event() or self.generate_block_stored_event()

        # 5% 概率生成所有块清除事件
        else:
            return self.generate_all_blocks_cleared_event()

    def generate_event_batch(self, num_events: int = 1) -> KVEventBatch:
        """生成事件批次"""
        events = []
        for _ in range(num_events):
            events.append(self.generate_random_event())

        return KVEventBatch(ts=time.time(), events=events)


class KVEventPublisher:
    """KV事件发布器，模拟vLLM的发布行为"""

    def __init__(
            self,
            endpoint: str = "tcp://*:5557",
            replay_endpoint: str = "tcp://*:5558",
            topic: str = "kv-events",
            buffer_size: int = 1000,
    ):
        self.endpoint = endpoint
        self.replay_endpoint = replay_endpoint
        self.topic = topic
        self.buffer_size = buffer_size

        self.context = zmq.Context()
        self.encoder = msgspec.msgpack.Encoder()

        # 事件缓冲区用于重放
        self.event_buffer = []
        self.next_seq = 0

        # 标志和线程
        self.running = False
        self.replay_thread = None

        # 初始化套接字
        self._setup_sockets()

        # 设置信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info(
            f"初始化KV事件发布器:\n"
            f"  发布端点: {endpoint}\n"
            f"  重放端点: {replay_endpoint}\n"
            f"  主题: '{topic}'"
        )

    def _signal_handler(self, sig, frame):
        """处理中断信号"""
        logger.info(f"收到信号 {sig}，正在关闭...")
        self.stop()
        sys.exit(0)

    def _setup_sockets(self):
        """设置ZMQ套接字"""
        # 设置发布套接字
        self.pub_socket = self.context.socket(zmq.PUB)
        self.pub_socket.bind(self.endpoint)

        # 设置重放套接字
        self.replay_socket = self.context.socket(zmq.ROUTER)
        self.replay_socket.bind(self.replay_endpoint)

    def _replay_thread_func(self):
        """处理重放请求的后台线程"""
        logger.info("重放线程已启动")

        poller = zmq.Poller()
        poller.register(self.replay_socket, zmq.POLLIN)

        while self.running:
            try:
                # 非阻塞轮询
                if dict(poller.poll(timeout=100)):
                    # 接收重放请求
                    frames = self.replay_socket.recv_multipart()
                    if len(frames) != 3:
                        logger.warning(f"收到无效重放请求: {frames}")
                        continue

                    identity, empty, seq_bytes = frames
                    start_seq = int.from_bytes(seq_bytes, "big")

                    logger.info(f"收到来自 {identity} 的重放请求，从序列 {start_seq} 开始")

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

                    logger.info(f"重放完成，已发送 {sent_count} 个事件")

            except Exception as e:
                logger.error(f"重放线程出错: {e}", exc_info=True)
                time.sleep(0.1)

    def publish(self, event_batch: KVEventBatch) -> None:
        """发布事件批次"""
        try:
            # 序列化事件
            payload = self.encoder.encode(event_batch)
            seq_bytes = self.next_seq.to_bytes(8, "big")

            # 发布事件
            self.pub_socket.send_multipart([
                self.topic.encode('utf-8'),
                seq_bytes,
                payload
            ])

            # 添加到重放缓冲区
            self.event_buffer.append((self.next_seq, payload))
            if len(self.event_buffer) > self.buffer_size:
                self.event_buffer.pop(0)

            # 递增序列号
            self.next_seq += 1

            # 打印事件信息
            event_types = [type(e).__name__ for e in event_batch.events]
            logger.info(f"已发布序列 {self.next_seq-1}: {event_types}")

        except Exception as e:
            logger.error(f"发布事件时出错: {e}", exc_info=True)

    def start_replay_service(self):
        """启动重放服务线程"""
        if self.replay_thread is not None:
            return

        self.running = True
        self.replay_thread = threading.Thread(
            target=self._replay_thread_func,
            daemon=True,
            name="replay-thread"
        )
        self.replay_thread.start()

    def stop(self):
        """停止发布器并清理资源"""
        logger.info("正在停止发布器...")
        self.running = False

        if self.replay_thread and self.replay_thread.is_alive():
            self.replay_thread.join(timeout=2.0)

        if hasattr(self, 'pub_socket'):
            self.pub_socket.close(linger=0)

        if hasattr(self, 'replay_socket'):
            self.replay_socket.close(linger=0)

        logger.info("发布器已停止")


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="vLLM KV缓存事件发布器模拟器")

    parser.add_argument(
        "--endpoint",
        type=str,
        default="tcp://*:5557",
        help="ZMQ发布端点 (默认: tcp://*:5557)"
    )

    parser.add_argument(
        "--replay-endpoint",
        type=str,
        default="tcp://*:5558",
        help="ZMQ重放端点 (默认: tcp://*:5558)"
    )

    parser.add_argument(
        "--topic",
        type=str,
        default="kv-events",
        help="发布主题 (默认: kv-events)"
    )

    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="每秒生成事件的速率 (默认: 1.0)"
    )

    parser.add_argument(
        "--events-per-batch",
        type=int,
        default=3,
        help="每批次的事件数量 (默认: 3)"
    )

    parser.add_argument(
        "--drop-rate",
        type=float,
        default=0.0,
        help="模拟丢包率，用于测试重放功能 (默认: 0.0)"
    )

    parser.add_argument(
        "--block-size",
        type=int,
        default=16,
        help="KV缓存块大小 (默认: 16)"
    )

    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="运行持续时间(秒)，0表示一直运行 (默认: 0)"
    )

    return parser.parse_args()


def main():
    """主程序入口"""
    args = parse_args()

    # 创建KV缓存模拟器
    simulator = KVCacheSimulator(block_size=args.block_size)

    # 创建发布器
    publisher = KVEventPublisher(
        endpoint=args.endpoint,
        replay_endpoint=args.replay_endpoint,
        topic=args.topic
    )

    # 启动重放服务
    publisher.start_replay_service()

    try:
        start_time = time.time()
        count = 0

        logger.info(f"开始生成事件，速率: {args.rate}/秒, 每批次 {args.events_per_batch} 个事件")

        # 主循环
        while True:
            # 检查是否达到持续时间
            if args.duration > 0 and time.time() - start_time > args.duration:
                logger.info(f"已达到指定持续时间 {args.duration} 秒，退出")
                break

            # 计算下一次发布时间
            next_time = start_time + (count + 1) / args.rate

            # 生成事件批次
            batch = simulator.generate_event_batch(num_events=args.events_per_batch)

            # 模拟随机丢包
            if random.random() >= args.drop_rate:
                publisher.publish(batch)
            else:
                # 故意跳过序列号，但不发布，用于测试重放
                publisher.next_seq += 1
                logger.info(f"模拟丢包，跳过序列 {publisher.next_seq-1}")

            count += 1

            # 等待到下一次发布时间
            sleep_time = next_time - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("收到用户中断，退出")
    finally:
        publisher.stop()
        logger.info("程序已退出")


if __name__ == "__main__":
    main()
