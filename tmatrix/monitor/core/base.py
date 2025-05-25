"""
监控系统核心基础模块
定义了收集器、导出器等核心抽象类
"""
import asyncio
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

# 配置日志
from tmatrix.common.logging import init_logger
logger = init_logger("monitor/core")


class MetricType(Enum):
    """指标类型枚举"""
    COUNTER = "counter"      # 计数器：只增不减
    GAUGE = "gauge"          # 仪表盘：可增可减
    HISTOGRAM = "histogram"  # 直方图：记录分布
    SUMMARY = "summary"      # 摘要：记录统计信息


@dataclass
class MetricValue:
    """指标值数据类"""
    name: str                          # 指标名称
    value: Any                         # 指标值
    metric_type: MetricType            # 指标类型
    labels: Dict[str, str] = field(default_factory=dict)  # 标签
    timestamp: float = field(default_factory=time.time)   # 时间戳
    description: str = ""              # 描述信息
    unit: str = ""                     # 单位

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CollectedEndpoint:
    """拉取数据的断点信息"""
    address: str
    instance_name: str


class MetricCollector(ABC):
    """
    指标收集器抽象基类
    所有收集器都需要继承此类并实现collect方法
    """

    def __init__(self, name: str, scrape_interval: float = 10.0):
        """
        初始化收集器

        Args:
            name: 收集器名称
            scrape_interval: 抓取间隔（秒）
        """
        self.name = name
        self.scrape_interval = scrape_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_scrape_time = 0
        self._scrape_errors = 0
        self._total_scrapes = 0

    @abstractmethod
    async def collect(self) -> List[MetricValue]:
        """
        收集指标的核心方法，子类必须实现

        Returns:
            指标值列表
        """
        pass

    async def start(self):
        """启动收集器"""
        if self._running:
            logger.warning(f"Collector {self.name} is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._collection_loop())
        logger.info(f"Started collector: {self.name}")

    async def stop(self):
        """停止收集器"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info(f"Stopped collector: {self.name}")

    async def _collection_loop(self):
        """收集循环，定期调用collect方法"""
        while self._running:
            try:
                start_time = time.time()

                # 收集指标
                metrics = await self.collect()

                # 更新统计信息
                self._last_scrape_time = time.time()
                self._total_scrapes += 1

                # 记录收集耗时
                duration = time.time() - start_time
                if duration > self.scrape_interval * 0.8:
                    # 收集耗时太久，可能存在性能问题或系统故障
                    logger.warning(
                        f"Collector {self.name} took {duration:.2f}s to collect, "
                        f"which is close to scrape_interval {self.scrape_interval}s"
                    )

                # 将收集到的指标注册到注册中心
                from .registry import MetricRegistry
                registry = MetricRegistry()
                await registry.update_metrics(self.name, metrics)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._scrape_errors += 1
                logger.error(f"Error in collector {self.name}: {e}", exc_info=True)

            # 等待下一次收集
            try:
                await asyncio.sleep(self.scrape_interval)
            except asyncio.CancelledError:
                break

    def get_stats(self) -> Dict[str, Any]:
        """获取收集器自身的统计信息"""
        return {
            "name": self.name,
            "running": self._running,
            "last_scrape_time": self._last_scrape_time,
            "scrape_errors": self._scrape_errors,
            "total_scrapes": self._total_scrapes,
            "scrape_interval": self.scrape_interval
        }


class MetricExporter(ABC):
    """
    指标导出器抽象基类
    所有导出器都需要继承此类并实现export方法
    """

    def __init__(self, name: str, export_interval: float = 10.0):
        """
        初始化导出器

        Args:
            name: 导出器名称
            export_interval: 导出间隔（秒）
        """
        self.name = name
        self.export_interval = export_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._export_errors = 0
        self._total_exports = 0

    @abstractmethod
    async def export(self, metrics: Dict[str, List[MetricValue]]) -> None:
        """
        导出指标的核心方法，子类必须实现

        Args:
            metrics: 按收集器名称分组的指标字典
        """
        pass

    async def start(self):
        """启动导出器"""
        if self._running:
            logger.warning(f"Exporter {self.name} is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._export_loop())
        logger.info(f"Started exporter: {self.name}")

    async def stop(self):
        """停止导出器"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info(f"Stopped exporter: {self.name}")

    async def _export_loop(self):
        """导出循环，定期调用export方法"""
        while self._running:
            try:
                # 从注册中心获取所有指标
                from .registry import MetricRegistry
                registry = MetricRegistry()
                metrics = await registry.get_all_metrics()

                # 导出指标
                await self.export(metrics)

                # 更新统计信息
                self._total_exports += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._export_errors += 1
                logger.error(f"Error in exporter {self.name}: {e}", exc_info=True)

            # 等待下一次导出
            try:
                await asyncio.sleep(self.export_interval)
            except asyncio.CancelledError:
                break

    def get_stats(self) -> Dict[str, Any]:
        """获取导出器自身的统计信息"""
        return {
            "name": self.name,
            "running": self._running,
            "export_errors": self._export_errors,
            "total_exports": self._total_exports,
            "export_interval": self.export_interval
        }
