import asyncio
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

from tmatrix.common.logging import init_logger
from tmatrix.runtime.metrics.client import AsyncMetricsClient, MetricsPoller, MetricsClientConfig
logger = init_logger("metrics/balancer")


@dataclass
class InstanceLoad:
    """实例负载信息"""
    instance_id: str
    running_requests: int = 0
    waiting_requests: int = 0
    gpu_cache_usage: float = 0.0
    load_score: float = 0.0
    timestamp: datetime = None


@dataclass
class LoadBalancer(ABC):
    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass


class RealtimeVLLMLoadBalancer(LoadBalancer):
    """简化版 vLLM 负载均衡器（基于 Poller）"""

    def __init__(self, metrics_client: AsyncMetricsClient, poll_interval: float = 5.0):
        self.metrics_client = metrics_client
        self.poll_interval = poll_interval

        # 初始化轮询器
        self.poller = MetricsPoller(metrics_client, poll_interval)
        self._is_running = False

    async def start(self):
        """启动负载均衡器"""
        if self._is_running:
            logger.warning("Load balancer is already running")
            return

        # 启动指标轮询
        metrics_to_poll = [
            "vllm:num_requests_running",
            "vllm:num_requests_waiting",
            "vllm:gpu_cache_usage_perc"
        ]

        await self.poller.start_polling(metrics_to_poll)
        self._is_running = True

        logger.info(f"Load balancer started with {self.poll_interval}s poll interval")

    async def stop(self):
        """停止负载均衡器"""
        if not self._is_running:
            return

        await self.poller.stop_polling()
        self._is_running = False

        logger.info("Load balancer stopped")

    def get_instance_loads(self) -> List[InstanceLoad]:
        """从 Poller 获取实例负载信息"""
        try:
            # 从轮询器获取最新数据
            latest_data = self.poller.get_latest_data()
            metrics = latest_data.get('metrics', {})

            if not metrics:
                logger.debug("No metrics data available from poller")
                return []

            return self._parse_instance_loads(metrics)

        except Exception as e:
            logger.error(f"Failed to get instance loads: {e}")
            return []

    def _parse_instance_loads(self, metrics: Dict) -> List[InstanceLoad]:
        """解析指标数据为实例负载信息"""
        instance_data = {}

        # 按实例分组指标
        for metric_name, metric_list in metrics.items():
            for metric in metric_list:
                instance_id = metric.get('labels', {}).get('inference_instance', 'unknown')

                if instance_id == 'unknown':
                    continue

                if instance_id not in instance_data:
                    instance_data[instance_id] = InstanceLoad(
                        instance_id=instance_id,
                        timestamp=datetime.now()
                    )

                # 提取指标值
                value = float(metric.get('value', 0))

                if metric_name == "vllm:num_requests_running":
                    instance_data[instance_id].running_requests = int(value)
                elif metric_name == "vllm:num_requests_waiting":
                    instance_data[instance_id].waiting_requests = int(value)
                elif metric_name == "vllm:gpu_cache_usage_perc":
                    instance_data[instance_id].gpu_cache_usage = value

        # 计算负载分数
        instances = list(instance_data.values())
        if instances:
            self._calculate_load_scores(instances)

        return instances

    def _calculate_load_scores(self, instances: List[InstanceLoad]):
        """计算负载分数（动态阈值版本）"""
        if not instances:
            return

        # 动态计算阈值（取所有实例的最大值作为参考）
        max_running = max(inst.running_requests for inst in instances)
        max_waiting = max(inst.waiting_requests for inst in instances)
        max_cache = max(inst.gpu_cache_usage for inst in instances)

        # 设置最小阈值，避免除零
        max_running = max(max_running, 1)
        max_waiting = max(max_waiting, 1)
        max_cache = max(max_cache, 0.1)

        for instance in instances:
            # 直接使用相对比例，不设固定阈值
            running_ratio = instance.running_requests / max_running
            waiting_ratio = instance.waiting_requests / max_waiting
            cache_ratio = instance.gpu_cache_usage / max_cache

            # 加权计算: 运行中请求40%, 等待请求40%, GPU缓存20%
            instance.load_score = running_ratio * 0.4 + waiting_ratio * 0.4 + cache_ratio * 0.2

    def select_best_instance(self) -> Optional[str]:
        """选择负载最低的实例"""
        instances = self.get_instance_loads()

        if not instances:
            logger.warning("No instances available")
            return None

        # 过滤掉过载的实例（这里用绝对阈值判断健康状态）
        healthy_instances = []
        for inst in instances:
            # 使用绝对阈值判断是否健康
            if (inst.running_requests < 30 and
                    inst.waiting_requests < 80 and
                    inst.gpu_cache_usage < 0.95):
                healthy_instances.append(inst)

        if not healthy_instances:
            logger.warning("All instances may be overloaded, selecting least loaded")
            healthy_instances = instances

        # 选择负载最低的实例
        best_instance = min(healthy_instances, key=lambda x: x.load_score)

        logger.info(f"Selected instance: {best_instance.instance_id} "
                         f"(score: {best_instance.load_score:.3f})")

        return best_instance.instance_id

    def get_load_status(self) -> Dict:
        """获取所有实例的负载状态"""
        instances = self.get_instance_loads()

        # 计算健康实例数
        healthy_count = sum(1 for inst in instances
                            if inst.running_requests < 30 and
                            inst.waiting_requests < 80 and
                            inst.gpu_cache_usage < 0.95)

        status = {
            "is_running": self._is_running,
            "last_update": self.poller.get_last_update().isoformat() if self.poller.get_last_update() else None,
            "total_instances": len(instances),
            "healthy_instances": healthy_count,
            "instances": []
        }

        for instance in sorted(instances, key=lambda x: x.load_score):
            is_healthy = (instance.running_requests < 30 and
                          instance.waiting_requests < 80 and
                          instance.gpu_cache_usage < 0.95)

            status["instances"].append({
                "instance_id": instance.instance_id,
                "load_score": round(instance.load_score, 3),
                "running_requests": instance.running_requests,
                "waiting_requests": instance.waiting_requests,
                "gpu_cache_usage": round(instance.gpu_cache_usage * 100, 1),
                "status": "healthy" if is_healthy else "overloaded",
                "timestamp": instance.timestamp.isoformat() if instance.timestamp else None
            })

        return status

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._is_running


# 使用示例
async def main():
    """负载均衡器测试"""
    # 配置监控客户端
    config = MetricsClientConfig(
        base_url="http://172.17.111.150:8088",
        endpoint="/api/v1/monitor/stats",
        timeout=5.0
    )

    client = AsyncMetricsClient()
    await client.initialize(config)

    # 创建负载均衡器
    lb = RealtimeVLLMLoadBalancer(client, poll_interval=3.0)

    try:
        # 启动负载均衡器
        await lb.start()

        # 等待一段时间让数据稳定
        await asyncio.sleep(5)

        print("=== vLLM 负载均衡器测试 ===\n")

        # 获取状态
        status = lb.get_load_status()
        print(f"运行状态: {status['is_running']}")
        print(f"实例总数: {status['total_instances']}")
        print(f"健康实例: {status['healthy_instances']}")

        for instance in status['instances']:
            print(f"  {instance['instance_id']}: "
                  f"负载={instance['load_score']}, "
                  f"运行={instance['running_requests']}, "
                  f"等待={instance['waiting_requests']}, "
                  f"缓存={instance['gpu_cache_usage']}%, "
                  f"状态={instance['status']}")

        print("\n选择测试:")
        for i in range(3):
            selected = lb.select_best_instance()
            print(f"第{i + 1}次选择: {selected}")
            await asyncio.sleep(2)

    finally:
        await lb.stop()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
    