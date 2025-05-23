import threading
import time
import queue
from dataclasses import dataclass
from typing import List, Optional, Dict

import msgspec
import zmq

from tmatrix.common.logging import init_logger
from .kv_events import KVEventBatch
from .kv_events import KVEventHandlerBase
from .base import EventSubscriberConfig, ZMQInstanceConfig, EventSubscriberBase
logger = init_logger("events_subscriber")


@dataclass
class ZMQSubscriberInfo:
    """
    | 字段            | 类型               | 责任/用途               | 用在 ZeroMQ 的哪一端            |
    |----------------|------------------|------------------------|------------------------------|
    | sub_socket     | socket对象       | 接收订阅消息              | 客户端（连接 pub_endpoint）    |
    | replay_socket  | socket对象       | 接收回放/历史消息          | 客户端（连接 replay_endpoint） |
    | pub_endpoint   | 字符串(address)  | 消息推送的服务端地址        | 服务端绑定/客户端连接           |
    | replay_endpoint| 字符串(address)  | 回放服务端的地址           | 服务端绑定/客户端连接          |
    """
    thread: threading.Thread
    sub_socket: zmq.Socket = None
    replay_socket: zmq.Socket = None
    context: zmq.Context = None
    running: bool = False
    last_req: int = -1
    config: Optional[ZMQInstanceConfig] = None


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
        self.instances: Dict[str, ZMQSubscriberInfo] = {}  # 实例ID -> 实例信息
        self.lock = threading.RLock()

        # 创建一个消息队列来解耦订阅和处理
        # queue.Queue是线程安全的，不必加锁控制
        # TODO
        #   1.当前所有订阅者公用一个消处理队列和一个处理线程，这是处于handler逻辑主要是CPU密集型任务的考量
        #   2.当handler逻辑向IO密集型考虑时，可能需要优化此处代码
        self.event_queue = queue.Queue(maxsize=1000)  # 设置合理的队列大小
        self.processor_thread = None
        # TODO 处理队列指标，当前暂不做管理和上报，只在当前实现中统计性能数据
        self.queue_stats = {
            "enqueued": 0,
            "processed": 0,
            "dropped": 0,
            "last_queue_size": 0,
            "max_queue_size": 0,
        }

    def add_instance(self, instance_config: ZMQInstanceConfig) -> bool:
        """
        动态添加vLLM实例
        如果订阅器已运行，会立即启动新实例的订阅线程
        参数:
            instance_config: 实例配置
        返回:
            是否成功添加
        """
        with self.lock:
            instance_id = instance_config.instance_id
            if instance_id in self.instances:
                logger.warning(f"实例 {instance_id} 已存在，跳过添加")
                return False

            logger.info(f"动态添加实例 {instance_id}: {instance_config.pub_endpoint}")

            try:
                # 创建ZMQ上下文
                context = zmq.Context()

                # 设置订阅套接字
                sub_socket = context.socket(zmq.SUB)
                sub_socket.setsockopt(zmq.RCVHWM, 1000)
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

                # 存储ZMQ订阅者线程信息
                instance_info = ZMQSubscriberInfo(
                    thread=thread,
                    sub_socket=sub_socket,
                    replay_socket=replay_socket,
                    context=context,
                    running=False,
                    last_req=0,
                    config=instance_config,
                )

                self.instances[instance_id] = instance_info

                # 如果订阅器已经运行，立即启动新实例的线程
                if self.running:
                    return self._start_instance(instance_id, instance_info)
                else:
                    logger.info(f"实例 {instance_id} 已添加，等待订阅器启动")
                    return True

            except Exception as e:
                logger.error(f"添加实例 {instance_id} 时出错: {e}", exc_info=True)
                # 清理可能已创建的资源
                if instance_id in self.instances:
                    self._cleanup_instance(instance_id)
                return False

    def remove_instance(self, instance_id: str) -> bool:
        """
        动态移除vLLM实例
        会立即停止对应的订阅线程
        参数:
            instance_id: 实例ID
        返回:
            是否成功移除
        """
        with self.lock:
            if instance_id not in self.instances:
                logger.warning(f"实例 {instance_id} 不存在，无法移除")
                return False

            logger.info(f"动态移除实例 {instance_id}")

            try:
                # 停止实例
                success = self._stop_instance(instance_id)

                # 清理实例信息
                self._cleanup_instance(instance_id)

                # 通知事件处理器清除实例的缓存数据
                self.event_handler.on_all_blocks_cleared(instance_id)

                logger.info(f"实例 {instance_id} 已成功移除")
                return success

            except Exception as e:
                logger.error(f"移除实例 {instance_id} 时出错: {e}", exc_info=True)
                return False

    def _start_instance(self, instance_id: str, instance_info: ZMQSubscriberInfo) -> bool:
        """
        启动单个实例的订阅线程
        """
        try:
            if instance_info.running or instance_info.thread.is_alive():
                logger.warning(f"实例 {instance_id} 已在运行中")
                return True

            instance_info.running = True
            instance_info.thread.start()

            logger.info(f"实例 {instance_id} 订阅线程已启动")
            return True

        except Exception as e:
            logger.error(f"启动实例 {instance_id} 时出错: {e}", exc_info=True)
            instance_info.running = False
            return False

    def _stop_instance(self, instance_id: str) -> bool:
        """
        停止单个实例的订阅线程
        """
        if instance_id not in self.instances:
            return False

        instance_info = self.instances[instance_id]

        try:
            # 标记为停止
            instance_info.running = False

            # 关闭套接字，这会导致线程的poll操作返回错误，从而退出循环
            if instance_info.sub_socket:
                instance_info.sub_socket.close(linger=0)

            if instance_info.replay_socket:
                instance_info.replay_socket.close(linger=0)

            # 等待线程结束
            if instance_info.thread.is_alive():
                instance_info.thread.join(timeout=3.0)
                if instance_info.thread.is_alive():
                    logger.warning(f"实例 {instance_id} 线程未在超时时间内结束")

            logger.info(f"实例 {instance_id} 订阅线程已停止")
            return True

        except Exception as e:
            logger.error(f"停止实例 {instance_id} 时出错: {e}", exc_info=True)
            return False

    def _cleanup_instance(self, instance_id: str):
        """
        清理实例资源
        """
        if instance_id not in self.instances:
            return

        instance_info = self.instances[instance_id]

        try:
            # 关闭ZMQ上下文
            if hasattr(instance_info, 'context') and instance_info.context:
                instance_info.context.term()
        except Exception as e:
            logger.error(f"清理实例 {instance_id} 上下文时出错: {e}")

        # 移除实例记录
        del self.instances[instance_id]

    def start(self) -> None:
        """启动订阅器和所有已添加的实例"""
        with self.lock:
            if self.running:
                logger.info("订阅器已在运行中")
                return

            logger.info("启动ZMQ事件订阅器...")
            self.running = True

            # 启动事件处理线程
            try:
                self.processor_thread = threading.Thread(
                    target=self._processor_thread_func,
                    daemon=True,
                    name="event-processor"
                )
                self.processor_thread.start()
                logger.info("事件处理线程已启动")
            except Exception as e:
                logger.error(f"启动事件处理线程失败: {e}", exc_info=True)
                self.running = False
                return

            # 启动所有实例的订阅线程
            success_count = 0
            total_count = len(self.instances)

            for instance_id, instance_info in self.instances.items():
                if self._start_instance(instance_id, instance_info):
                    success_count += 1
                else:
                    logger.error(f"启动实例 {instance_id} 失败")

            if total_count == 0:
                logger.info("订阅器已启动，但当前没有实例。可以通过 add_instance() 动态添加实例")
            else:
                logger.info(f"订阅器已启动，成功启动 {success_count}/{total_count} 个实例的订阅")

    def stop(self) -> None:
        """停止所有订阅线程和处理线程"""
        with self.lock:
            if not self.running:
                logger.info("订阅器未在运行中")
                return

            logger.info("停止ZMQ事件订阅器...")
            self.running = False

            # 停止所有实例的订阅线程
            instance_ids = list(self.instances.keys())
            for instance_id in instance_ids:
                self._stop_instance(instance_id)

            # 停止事件处理线程
            if self.processor_thread and self.processor_thread.is_alive():
                try:
                    # 发送停止信号
                    self.event_queue.put((None, "STOP"), block=False)
                except queue.Full:
                    logger.warning("无法发送停止信号到队列，队列已满")

                # 等待处理线程结束
                self.processor_thread.join(timeout=5.0)
                if self.processor_thread.is_alive():
                    logger.warning("事件处理线程未在超时时间内结束")

            # 清空队列
            cleared_count = 0
            while not self.event_queue.empty():
                try:
                    self.event_queue.get(block=False)
                    self.event_queue.task_done()
                    cleared_count += 1
                except queue.Empty:
                    break

            if cleared_count > 0:
                logger.info(f"已清空队列中的 {cleared_count} 个未处理事件")

            # 记录最终统计
            logger.info("订阅器已停止")
            logger.info(f"最终队列统计: 入队={self.queue_stats['enqueued']}, "
                        f"处理={self.queue_stats['processed']}, "
                        f"丢弃={self.queue_stats['dropped']}, "
                        f"最大队列大小={self.queue_stats['max_queue_size']}")

    def is_running(self) -> bool:
        """检查订阅器是否运行中"""
        with self.lock:
            return self.running

    def _subscriber_thread(self, instance_id: str, sub_socket: zmq.Socket,
                           replay_socket: Optional[zmq.Socket]) -> None:
        """
        订阅线程函数 - 仅负责接收消息并放入队列
        """
        logger.info(f"实例 {instance_id} 的订阅线程已启动")

        decoder = msgspec.msgpack.Decoder(type=KVEventBatch)
        poller = zmq.Poller()
        poller.register(sub_socket, zmq.POLLIN)

        last_seq = -1

        try:
            while (self.running and
                   instance_id in self.instances and
                   self.instances[instance_id].running):

                try:
                    # 使用较短的超时，以便能及时响应停止信号
                    socks = dict(poller.poll(timeout=100))

                    if sub_socket in socks and socks[sub_socket] == zmq.POLLIN:
                        # 接收消息
                        frames = sub_socket.recv_multipart(zmq.NOBLOCK)

                        if len(frames) != 3:
                            logger.warning(f"实例 {instance_id} 收到无效消息格式: {len(frames)} 帧")
                            continue

                        topic_part, seq_bytes, payload = frames
                        seq = int.from_bytes(seq_bytes, "big")

                        # 检查是否有消息丢失
                        if last_seq >= 0 and seq > last_seq + 1 and replay_socket:
                            self._request_replay(instance_id, replay_socket, last_seq + 1, seq, decoder)

                        # 解码并入队事件
                        try:
                            event_batch = decoder.decode(payload)
                            self._enqueue_event(event_batch, instance_id, seq)
                            last_seq = seq
                        except Exception as e:
                            logger.error(f"实例 {instance_id} 解码事件时出错: {e}", exc_info=True)

                except zmq.Again:
                    # 非阻塞接收没有消息，继续循环
                    continue
                except zmq.ZMQError as e:
                    # ZMQ错误，可能是socket被关闭，检查是否应该退出
                    if self.running and instance_id in self.instances and self.instances[instance_id].running:
                        logger.error(f"实例 {instance_id} ZMQ错误: {e}")
                        time.sleep(0.1)
                    else:
                        # 正常退出，socket被主动关闭
                        break
                except Exception as e:
                    logger.error(f"实例 {instance_id} 处理消息时出错: {e}", exc_info=True)
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"实例 {instance_id} 订阅线程异常: {e}", exc_info=True)
        finally:
            logger.info(f"实例 {instance_id} 的订阅线程已退出 (最后序列号: {last_seq})")

            # 更新最后序列号
            with self.lock:
                if instance_id in self.instances:
                    self.instances[instance_id].last_req = last_seq

    def _enqueue_event(self, event_batch, instance_id: str, seq: int):
        """
        将事件放入处理队列
        """
        try:
            self.event_queue.put((event_batch, instance_id), block=False)
            with self.lock:
                self.queue_stats["enqueued"] += 1
                queue_size = self.event_queue.qsize()
                self.queue_stats["last_queue_size"] = queue_size
                if queue_size > self.queue_stats["max_queue_size"]:
                    self.queue_stats["max_queue_size"] = queue_size

            logger.debug(f"实例 {instance_id} 序列 {seq} 已入队 (队列大小: {queue_size})")
        except queue.Full:
            logger.warning(f"队列已满，丢弃实例 {instance_id} 序列 {seq} 的事件")
            with self.lock:
                self.queue_stats["dropped"] += 1

    def _processor_thread_func(self):
        """
        处理线程 - 从队列获取消息并处理
        """
        logger.info("事件处理线程已启动")

        processed_count = 0
        last_log_time = time.time()

        while self.running:
            try:
                # 使用超时来允许线程定期检查running状态
                event_batch, instance_id = self.event_queue.get(timeout=0.5)

                # 检查特殊的停止消息
                if event_batch is None and instance_id == "STOP":
                    logger.info("处理线程收到停止信号")
                    break

                # 处理消息
                try:
                    start_time = time.time()
                    self.event_handler.process_event_batch(event_batch, instance_id)
                    process_time = time.time() - start_time

                    # 记录处理统计
                    with self.lock:
                        self.queue_stats["processed"] += 1
                        processed_count += 1

                    # 周期性日志记录
                    current_time = time.time()
                    if current_time - last_log_time > 10.0:  # 每10秒记录一次
                        logger.info(f"队列处理状态: 已处理={processed_count}, "
                                    f"队列大小={self.event_queue.qsize()}, "
                                    f"活跃实例={len(self.get_running_instances())}")
                        processed_count = 0
                        last_log_time = current_time

                    # 处理时间警告
                    if process_time > 0.1:  # 100ms
                        logger.warning(f"事件处理时间过长: {process_time:.3f}秒 (实例: {instance_id})")

                    logger.debug(f"完成处理实例 {instance_id} 的事件")
                except Exception as e:
                    logger.error(f"处理实例 {instance_id} 事件时出错: {e}", exc_info=True)
                finally:
                    self.event_queue.task_done()

            except queue.Empty:
                # 队列为空，继续等待
                pass
            except Exception as e:
                logger.error(f"处理线程异常: {e}", exc_info=True)
                time.sleep(0.1)

        logger.info("事件处理线程已退出")

    def _request_replay(self, instance_id: str, replay_socket: zmq.Socket,
                        start_seq: int, end_seq: int, decoder) -> None:
        """请求事件重放"""
        missed = end_seq - start_seq
        logger.info(
            f"实例 {instance_id} 检测到消息缺失 (序列: {start_seq - 1} -> {end_seq}, 缺失: {missed}), 正在请求重放...")

        try:
            # 发送重放请求
            replay_socket.send(start_seq.to_bytes(8, "big"))

            # 设置超时
            poller = zmq.Poller()
            poller.register(replay_socket, zmq.POLLIN)

            # 接收重放消息
            replay_count = 0
            timeout_count = 0
            max_timeouts = 3

            while timeout_count < max_timeouts:
                socks = dict(poller.poll(timeout=2000))  # 2秒超时

                if not socks:
                    timeout_count += 1
                    logger.warning(f"实例 {instance_id} 重放请求超时 ({timeout_count}/{max_timeouts})")
                    continue

                try:
                    seq_bytes, replay_payload = replay_socket.recv_multipart(zmq.NOBLOCK)
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
                            self._enqueue_event(event_batch, instance_id, replay_seq)
                            replay_count += 1
                        except Exception as e:
                            logger.error(f"实例 {instance_id} 解码重放事件时出错: {e}", exc_info=True)

                    if replay_seq >= end_seq - 1:
                        break

                except zmq.Again:
                    continue

            logger.info(f"实例 {instance_id} 重放完成，已入队 {replay_count} 条事件")

        except Exception as e:
            logger.error(f"实例 {instance_id} 重放过程中出错: {e}", exc_info=True)

    def get_instance_count(self) -> int:
        """获取实例总数"""
        with self.lock:
            return len(self.instances)

    def get_running_instances(self) -> List[str]:
        """获取正在运行的实例列表"""
        with self.lock:
            return [
                instance_id for instance_id, info in self.instances.items()
                if info.running and info.thread.is_alive()
            ]

    def get_instance_status(self) -> Dict[str, Dict]:
        """获取所有实例的详细状态"""
        with self.lock:
            status = {}
            for instance_id, info in self.instances.items():
                status[instance_id] = {
                    "running": info.running,
                    "thread_alive": info.thread.is_alive(),
                    "last_seq": info.last_req,
                    "endpoint": info.config.pub_endpoint,
                    "replay_endpoint": info.config.replay_endpoint
                }
            return status

    def get_queue_stats(self) -> Dict:
        """获取队列统计信息"""
        with self.lock:
            stats = self.queue_stats.copy()
            stats["current_queue_size"] = self.event_queue.qsize()
            stats["processor_thread_alive"] = (
                self.processor_thread.is_alive() if self.processor_thread else False
            )
            return stats
