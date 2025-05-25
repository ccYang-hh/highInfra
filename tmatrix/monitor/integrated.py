"""
集成到业务系统的入口
提供简单的API供业务系统调用，以启动监控服务
"""
import sys
import asyncio
import multiprocessing
from typing import Optional

from .core.config import MonitorConfig, get_default_config
from .service import MonitorService

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/integrated")


class MonitorProcess:
    """
    监控进程管理器
    在独立进程中运行监控服务
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化监控进程管理器

        Args:
            config_path: 配置文件路径，如果为None则使用默认配置
        """
        self.config_path = config_path
        self._process: Optional[multiprocessing.Process] = None

    def start(self):
        """启动监控进程"""
        if self._process and self._process.is_alive():
            logger.warning("Monitor process is already running")
            return

        # 创建进程
        multiprocessing.set_start_method('spawn')
        self._process = multiprocessing.Process(
            target=self._run_monitor_service,
            args=(self.config_path,),
            name='metrics-monitor'
        )

        # 设置为守护进程
        self._process.daemon = True

        # 启动进程
        self._process.start()
        logger.info(f"Started monitor process with PID {self._process.pid}")

    def stop(self):
        """停止监控进程"""
        if not self._process:
            return

        if self._process.is_alive():
            logger.info("Stopping monitor process...")
            self._process.terminate()
            self._process.join(timeout=10)

            if self._process.is_alive():
                logger.warning("Monitor process did not stop gracefully, forcing...")
                self._process.kill()
                self._process.join()

        self._process = None
        logger.info("Monitor process stopped")

    def is_alive(self) -> bool:
        """检查监控进程是否存活"""
        return self._process is not None and self._process.is_alive()

    @staticmethod
    def _run_monitor_service(config_path: Optional[str]):
        """在子进程中运行监控服务"""
        # 运行异步主函数
        asyncio.run(MonitorProcess._async_run(config_path))

    @staticmethod
    async def _async_run(config_path: Optional[str]):
        """异步运行监控服务"""
        # 加载配置
        if config_path:
            from pathlib import Path
            config_path_c = Path(config_path)
            if not config_path_c.exists():
                logger.error(f"Config file not found: {config_path}")
                sys.exit(1)

            try:
                config = MonitorConfig.from_file(config_path)
                logger.info(f"Loaded config from {config_path}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
                sys.exit(1)
        else:
            config = get_default_config()
            logger.info("Using default configuration. Use --config to specify a config file.")

        # 验证配置
        try:
            config.validate()
        except ValueError as e:
            logger.error(f"Invalid configuration: {e}")
            sys.exit(1)

        # 创建并启动服务
        service = MonitorService(config)

        try:
            await service.initialize()
            await service.start()

            logger.info("Monitor service is running. Press Ctrl+C to stop.")
            await service.wait_until_stop()

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Service error: {e}", exc_info=True)
            sys.exit(1)
        finally:
            await service.stop()
            logger.info("Monitor Service stopped")
