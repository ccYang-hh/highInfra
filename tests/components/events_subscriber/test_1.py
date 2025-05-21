
import argparse
import json
import logging
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Dict, List, Optional, Union

import requests
from tmatrix.components.logging import init_logger
logger = init_logger("tests/events_subscriber")


class PublisherProcess:
    """模拟vLLM实例的发布进程管理器"""

    def __init__(self, instance_id: str, pub_port: int, replay_port: int,
                 token_offset: int = 0, rate: float = 0.5):
        """
        初始化发布进程
        参数:
            instance_id: 实例ID
            pub_port: 发布端口
            replay_port: 重放端口
            token_offset: token ID基准偏移，用于区分不同实例的token范围
            rate: 每秒生成事件的速率
        """
        self.instance_id = instance_id
        self.pub_port = pub_port
        self.replay_port = replay_port
        self.token_offset = token_offset
        self.rate = rate
        self.process = None
        self.log_file = None

    def start(self) -> bool:
        """启动发布进程"""
        try:
            # 创建日志文件
            self.log_file = tempfile.NamedTemporaryFile(
                prefix=f"publisher_{self.instance_id}_",
                suffix=".log",
                delete=False,
                mode='w'
            )
            logger.info(f"Publisher {self.instance_id} 日志: {self.log_file.name}")

            # 构建命令
            cmd = [
                sys.executable,  # 当前Python解释器路径
                "kvevent_publisher_simulator.py",
                "--endpoint", f"tcp://*:{self.pub_port}",
                "--replay-endpoint", f"tcp://*:{self.replay_port}",
                "--topic", "kv-events",
                "--rate", str(self.rate),
                "--events-per-batch", "1",
                "--drop-rate", "0.0",
                "--token-offset", str(self.token_offset)
            ]

            # 启动进程
            self.process = subprocess.Popen(
                cmd,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            logger.info(f"启动 Publisher {self.instance_id}, PID: {self.process.pid}")

            # 给进程一点时间来初始化
            time.sleep(1)

            if self.process.poll() is not None:
                logger.error(f"Publisher {self.instance_id} 启动失败")
                return False

            return True

        except Exception as e:
            logger.error(f"启动 Publisher {self.instance_id} 时出错: {e}", exc_info=True)
            self.stop()
            return False

    def stop(self) -> bool:
        """停止发布进程"""
        if self.process:
            logger.info(f"停止 Publisher {self.instance_id}")

            try:
                # 发送SIGTERM信号
                self.process.terminate()

                # 等待进程结束
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Publisher {self.instance_id} 未响应SIGTERM，强制结束")
                    self.process.kill()

                logger.info(f"Publisher {self.instance_id} 已停止")

                if self.log_file:
                    self.log_file.close()

                return True

            except Exception as e:
                logger.error(f"停止 Publisher {self.instance_id} 时出错: {e}")
                return False

        return True

