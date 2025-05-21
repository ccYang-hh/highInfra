import threading
import time
from typing import List, Optional

import msgspec
import zmq

from tmatrix.components.logging import init_logger
from .kv_events import KVEventHandlerBase, KVEventBatch
from .base import EventSubscriberBase, EventSubscriberConfig, ZMQInstanceConfig

logger = init_logger("events_subscriber/kv_events")


class ZMQEventSubscriber(EventSubscriberBase):
    """
    ZMQ实现的事件订阅器
    支持从多个vLLM实例订阅KV缓存事件
    """

    def __init__(self, event_handler: KVEventHandlerBase, config: EventSubscriberConfig):
        """
        初始化ZMQ事件订阅器
        参数:
            event_handler: 事件处理器
            config: 订阅器配置
        """
        super().__init__(event_handler, config)
        self.instances = {}  # 实例ID -> 实例信息
        self.lock = threading.RLock()

    def add_instance(self, instance_config: ZMQInstanceConfig) -> bool:
        """
        添加vLLM实例
        参数:
            instance_config: 实例配置
        返回:
            是否成功添加
        """
        with self.lock:
            instance_id = instance_config.instance_id
            if instance_id in self.instances:
                logger.warning(f"实例 {instance_id} 已存在")
                return False

            logger.info(f"添加实例 {instance_id}: {instance_config.pub_endpoint}")

            # 创建ZMQ上下文
            context = zmq.Context()

            # 设置订阅套接字
            sub_socket = context.socket(zmq.SUB)
            sub_socket.connect(instance_config.pub_endpoint)
            sub_socket.setsockopt_string(zmq.SUBSCRIBE, self.config.topic)

            # 设置重放套接字
            replay_socket = None
            if instance_config.replay_endpoint:
                replay_socket = context.socket(zmq.REQ)
                replay_socket.connect(instance_config.replay_endpoint)

            # 创建线程
            thread = threading.Thread(
                target=self._subscriber_thread,
                args=(instance_id, sub_socket, replay_socket),
                daemon=True,
                name=f"sub-{instance_id}"
            )

            # 存储线程信息
            self.instances[instance_id] = {
                "thread": thread,
                "sub_socket": sub_socket,
                "replay_socket": replay_socket,
                "context": context,
                "running": False,
                "last_seq": -1,
                "config": instance_config
            }

            # 如果已经运行，启动线程
            if self.running:
                self.instances[instance_id]["running"] = True
                thread.start()

            return True

    def remove_instance(self, instance_id: str) -> bool:
        """
        移除vLLM实例
        参数:
            instance_id: 实例ID
        返回:
            是否成功移除
        """
        with self.lock:
            if instance_id not in self.instances:
                logger.warning(f"实例 {instance_id} 不存在")
                return False

            logger.info(f"移除实例 {instance_id}")

            # 停止线程
            self.instances[instance_id]["running"] = False
            thread = self.instances[instance_id]["thread"]

            # 关闭套接字
            if self.instances[instance_id]["sub_socket"]:
                self.instances[instance_id]["sub_socket"].close(linger=0)

            if self.instances[instance_id]["replay_socket"]:
                self.instances[instance_id]["replay_socket"].close(linger=0)

            # 等待线程结束
            if thread.is_alive():
                thread.join(timeout=2.0)

            # 移除实例
            del self.instances[instance_id]

            # 通知事件处理器清除实例的缓存数据
            self.event_handler.on_all_blocks_cleared(instance_id)

            return True

    def start(self) -> None:
        """启动所有订阅线程"""
        with self.lock:
            if self.running:
                return

            self.running = True

            for instance_id, info in self.instances.items():
                info["running"] = True
                if not info["thread"].is_alive():
                    info["thread"].start()

            logger.info(f"已启动 {len(self.instances)} 个实例的订阅")

    def stop(self) -> None:
        """停止所有订阅线程"""
        with self.lock:
            if not self.running:
                return

            self.running = False

            for instance_id, info in self.instances.items():
                info["running"] = False

            for instance_id, info in self.instances.items():
                if info["thread"].is_alive():
                    info["thread"].join(timeout=2.0)

                if info["sub_socket"]:
                    info["sub_socket"].close(linger=0)

                if info["replay_socket"]:
                    info["replay_socket"].close(linger=0)

            logger.info("已停止所有实例的订阅")

    def is_running(self) -> bool:
        """检查订阅器是否运行中"""
        with self.lock:
            return self.running

    def _subscriber_thread(self, instance_id: str, sub_socket: zmq.Socket,
                           replay_socket: Optional[zmq.Socket]) -> None:
        """订阅线程函数"""
        logger.info(f"实例 {instance_id} 的订阅线程已启动")

        decoder = msgspec.msgpack.Decoder(type=KVEventBatch)
        poller = zmq.Poller()
        poller.register(sub_socket, zmq.POLLIN)

        last_seq = -1

        try:
            while self.instances.get(instance_id, {}).get("running", False):
                try:
                    socks = dict(poller.poll(timeout=100))

                    if sub_socket in socks and socks[sub_socket] == zmq.POLLIN:
                        # 接收消息
                        frames = sub_socket.recv_multipart()

                        if len(frames) != 3:
                            logger.warning(f"实例 {instance_id} 收到无效消息格式: {len(frames)} 帧")
                            continue

                        topic_part, seq_bytes, payload = frames
                        seq = int.from_bytes(seq_bytes, "big")

                        # 检查是否有消息丢失
                        if last_seq >= 0 and seq > last_seq + 1 and replay_socket:
                            self._request_replay(instance_id, replay_socket, last_seq + 1, seq, decoder)

                        # 解码并处理事件
                        try:
                            event_batch = decoder.decode(payload)
                            self.event_handler.process_event_batch(event_batch, instance_id)
                            last_seq = seq
                        except Exception as e:
                            logger.error(f"实例 {instance_id} 解码事件时出错: {e}", exc_info=True)

                except zmq.ZMQError as e:
                    logger.error(f"实例 {instance_id} ZMQ错误: {e}")
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"实例 {instance_id} 处理消息时出错: {e}", exc_info=True)
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"实例 {instance_id} 订阅线程异常: {e}", exc_info=True)

        logger.info(f"实例 {instance_id} 的订阅线程已退出")

        with self.lock:
            # 更新状态
            if instance_id in self.instances:
                self.instances[instance_id]["running"] = False
                self.instances[instance_id]["last_seq"] = last_seq

    def _request_replay(self, instance_id: str, replay_socket: zmq.Socket,
                        start_seq: int, end_seq: int, decoder) -> None:
        """请求事件重放"""
        missed = end_seq - start_seq
        logger.info(
            f"实例 {instance_id} 可能丢失了 {missed} 条消息 (上次: {start_seq - 1}, 当前: {end_seq}), 正在请求重放...")

        try:
            # 发送重放请求
            replay_socket.send(start_seq.to_bytes(8, "big"))

            # 设置超时
            poller = zmq.Poller()
            poller.register(replay_socket, zmq.POLLIN)

            # 接收重放消息
            replay_count = 0
            while True:
                if not dict(poller.poll(timeout=1000)):  # 1秒超时
                    logger.warning(f"实例 {instance_id} 重放请求超时")
                    break

                seq_bytes, replay_payload = replay_socket.recv_multipart()
                replay_seq = int.from_bytes(seq_bytes, "big")

                # 检查结束标记
                if replay_seq == -1:
                    logger.debug(f"实例 {instance_id} 重放完成")
                    break

                if not replay_payload:
                    logger.debug(f"实例 {instance_id} 收到空重放载荷")
                    break

                if replay_seq >= start_seq:
                    try:
                        event_batch = decoder.decode(replay_payload)
                        self.event_handler.process_event_batch(event_batch, instance_id)
                        replay_count += 1
                    except Exception as e:
                        logger.error(f"实例 {instance_id} 解码重放事件时出错: {e}", exc_info=True)

                if replay_seq >= end_seq - 1:
                    break

            logger.info(f"实例 {instance_id} 重放已完成，处理了 {replay_count} 条事件")

        except Exception as e:
            logger.error(f"实例 {instance_id} 重放过程中出错: {e}", exc_info=True)

    def get_instance_count(self) -> int:
        """获取实例数量"""
        with self.lock:
            return len(self.instances)

    def get_running_instances(self) -> List[str]:
        """获取正在运行的实例列表"""
        with self.lock:
            return [
                instance_id for instance_id, info in self.instances.items()
                if info["running"] and info["thread"].is_alive()
            ]

