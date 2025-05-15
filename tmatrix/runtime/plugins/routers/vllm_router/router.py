import time
import random
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException

from tmatrix.components.logging import init_logger
from tmatrix.runtime.plugins.interface import Plugin
from tmatrix.runtime.pipeline.stages import PipelineStage
from tmatrix.runtime.pipeline.hooks import StageHook
from tmatrix.runtime.pipeline.pipeline import Pipeline
from tmatrix.runtime.context import RequestContext, RequestState

logger = init_logger("plugin/router")


class ModelResolutionStage(PipelineStage):
    """从请求中提取和验证模型信息的阶段"""

    def __init__(self):
        super().__init__("model_resolution")

    async def process(self, context: RequestContext) -> None:
        """处理请求上下文"""
        context.set_state(RequestState.MODEL_RESOLUTION)

        # 提取模型名称
        model = context.parsed_body.get("model")
        if not model:
            context.fail("Missing model parameter in request")
            return

        # 设置模型名称
        context.model_name = model

        # 解析其他模型参数
        if "parameters" in context.parsed_body:
            context.model_parameters = context.parsed_body["parameters"]

        # 检查模型是否支持
        model_registry = context.get_extension("model_registry", {})
        if model_registry and model not in model_registry:
            context.fail(f"Model '{model}' is not supported")
            return

        logger.debug(f"Resolved model: {model} for request {context.request_id}")


class BackendRoutingStage(PipelineStage):
    """将请求路由到合适的后端服务器的阶段"""

    def __init__(self, backends=None, strategy="round_robin"):
        """
        初始化路由阶段

        Args:
            backends: 可选的后端列表
            strategy: 路由策略 (round_robin, random, least_loaded)
        """
        super().__init__("backend_routing")
        self._backends = backends or []
        self._strategy = strategy
        self._current_backend_index = 0

    def configure(self, config: Dict[str, Any]) -> "BackendRoutingStage":
        """配置路由阶段"""
        if "backends" in config:
            self._backends = config["backends"]
        if "strategy" in config:
            self._strategy = config["strategy"]
        return self

    def add_backend(self, backend: Dict[str, Any]) -> None:
        """
        添加后端

        Args:
            backend: 后端信息字典
        """
        if backend not in self._backends:
            self._backends.append(backend)

    def remove_backend(self, backend_id: str) -> None:
        """
        移除后端

        Args:
            backend_id: 后端ID
        """
        self._backends = [b for b in self._backends if b.get("id") != backend_id]

    def get_backends(self) -> List[Dict[str, Any]]:
        """
        获取所有后端

        Returns:
            后端列表
        """
        return self._backends.copy()

    async def _select_backend(self, context: RequestContext) -> Optional[Dict[str, Any]]:
        """
        根据策略选择后端

        Args:
            context: 请求上下文

        Returns:
            选择的后端或None
        """
        # 过滤出支持请求模型的后端
        model = context.model_name
        available_backends = [
            b for b in self._backends
            if model in b.get("models", []) and b.get("healthy", True)
        ]

        if not available_backends:
            return None

        # 保存可用后端到上下文
        context.available_backends = available_backends

        # 根据策略选择后端
        if self._strategy == "round_robin":
            # 简单轮询
            backend = available_backends[self._current_backend_index % len(available_backends)]
            self._current_backend_index += 1
        elif self._strategy == "random":
            # 随机选择
            backend = random.choice(available_backends)
        elif self._strategy == "least_loaded":
            # 选择负载最低的
            backend = min(available_backends, key=lambda b: b.get("load", 0))
        else:
            # 默认使用第一个
            backend = available_backends[0]

        return backend

    async def process(self, context: RequestContext) -> None:
        """处理请求上下文"""
        context.set_state(RequestState.ROUTING)

        # 确保已经解析出模型
        if not context.model_name:
            context.fail("Model name not resolved")
            return

        # 选择后端
        backend = await self._select_backend(context)
        if not backend:
            context.fail(f"No backend available for model {context.model_name}")
            return

        # 设置选择的后端
        context.backend_url = backend.get("url")
        context.backend_info = backend

        logger.debug(f"Selected backend {backend.get('id')} for request {context.request_id}")


class RoutingMetricsHook(StageHook):
    """收集路由指标的钩子"""

    def __init__(self):
        super().__init__("routing_metrics")
        self.metrics = {
            "requests_by_model": {},
            "backend_selections": {},
            "routing_times": []
        }

    async def before_stage(self, stage: PipelineStage, context: RequestContext) -> None:
        """在阶段执行前记录时间戳"""
        if stage.name == "backend_routing":
            context.set_extension("routing_start_time", time.time())

    async def after_stage(self, stage: PipelineStage, context: RequestContext) -> None:
        """记录路由指标"""
        if stage.name == "backend_routing":
            # 记录路由时间
            start_time = context.get_extension("routing_start_time")
            if start_time:
                routing_time = time.time() - start_time
                self.metrics["routing_times"].append(routing_time)

                # 保持列表大小合理
                if len(self.metrics["routing_times"]) > 1000:
                    self.metrics["routing_times"] = self.metrics["routing_times"][-1000:]

            # 记录模型请求量
            model = context.model_name
            if model:
                self.metrics["requests_by_model"][model] = self.metrics["requests_by_model"].get(model, 0) + 1

            # 记录后端选择
            if context.backend_info:
                backend_id = context.backend_info.get("id")
                if backend_id:
                    self.metrics["backend_selections"][backend_id] = self.metrics["backend_selections"].get(backend_id, 0) + 1

    def get_metrics(self) -> Dict[str, Any]:
        """获取累积的指标"""
        result = self.metrics.copy()

        # 计算平均路由时间
        if self.metrics["routing_times"]:
            result["avg_routing_time"] = sum(self.metrics["routing_times"]) / len(self.metrics["routing_times"])
        else:
            result["avg_routing_time"] = 0

        return result


class RouterPlugin(Plugin):
    """路由插件，提供模型解析和后端路由功能"""

    def __init__(self):
        self.router = APIRouter(tags=["router"])
        self.model_resolution_stage = ModelResolutionStage()
        self.routing_stage = BackendRoutingStage()
        self.metrics_hook = RoutingMetricsHook()

    def get_name(self) -> str:
        return "router"

    def get_version(self) -> str:
        return "1.0.0"

    def get_description(self) -> str:
        return "Provides model resolution and backend routing functionality"

    async def initialize(self, config: Dict[str, Any]) -> None:
        """初始化路由插件"""
        # 配置路由阶段
        backends = config.get("backends", [])
        strategy = config.get("strategy", "round_robin")

        self.routing_stage.configure({
            "backends": backends,
            "strategy": strategy
        })

        # 设置API路由
        self._setup_api_routes()

        logger.info(f"Initialized router plugin with {len(backends)} backends using {strategy} strategy")

    async def shutdown(self) -> None:
        """关闭路由插件"""
        logger.info("Shutting down router plugin")

    def get_pipeline_stages(self) -> List[PipelineStage]:
        """获取管道阶段"""
        return [self.model_resolution_stage, self.routing_stage]

    def get_stage_hooks(self) -> List[StageHook]:
        """获取阶段钩子"""
        return [self.metrics_hook]

    def get_api_router(self) -> APIRouter:
        """获取API路由"""
        return self.router

    def configure_pipeline(self, pipeline: Pipeline) -> None:
        """配置管道连接"""
        # 如果两个阶段都在管道中，确保它们正确连接
        if (self.model_resolution_stage.name in pipeline.stages and
                self.routing_stage.name in pipeline.stages):
            pipeline.connect(self.model_resolution_stage.name, self.routing_stage.name)

    def _setup_api_routes(self) -> None:
        """设置API路由"""
        @self.router.get("/backends")
        async def get_backends():
            """获取所有后端"""
            return {"backends": self.routing_stage.get_backends()}

        @self.router.post("/backends")
        async def add_backend(backend: Dict[str, Any]):
            """添加后端"""
            if not backend.get("id") or not backend.get("url"):
                raise HTTPException(status_code=400, detail="Backend must have id and url")

            self.routing_stage.add_backend(backend)
            return {"status": "ok", "backend": backend}

        @self.router.delete("/backends/{backend_id}")
        async def remove_backend(backend_id: str):
            """移除后端"""
            self.routing_stage.remove_backend(backend_id)
            return {"status": "ok"}

        @self.router.get("/metrics")
        async def get_metrics():
            """获取路由指标"""
            return self.metrics_hook.get_metrics()
