from abc import ABC
from typing import Optional

from tmatrix.components.logging import init_logger
from tmatrix.runtime.core.context import RequestContext

logger = init_logger("runtime/pipeline")


class StageHook(ABC):
    """
    阶段钩子接口

    钩子可以在阶段处理的不同点执行，用于添加横切关注点
    如日志记录、指标收集、调试等，而不必修改阶段的核心逻辑
    """

    def __init__(self, name: Optional[str] = None):
        """
        初始化钩子

        Args:
            name: 钩子名称，默认为类名
        """
        self._name = name or self.__class__.__name__

    @property
    def name(self) -> str:
        """获取钩子名称"""
        return self._name

    async def before_stage(self, stage: "PipelineStage", context: RequestContext) -> None:
        """
        在阶段执行前调用

        Args:
            stage: 当前执行的阶段
            context: 请求上下文
        """
        pass

    async def after_stage(self, stage: "PipelineStage", context: RequestContext) -> None:
        """
        在阶段执行后调用

        Args:
            stage: 当前执行的阶段
            context: 请求上下文
        """
        pass

    async def on_error(self, stage: "PipelineStage", context: RequestContext, error: Exception) -> None:
        """
        在阶段执行出错时调用

        Args:
            stage: 当前执行的阶段
            context: 请求上下文
            error: 发生的错误
        """
        pass

    def __str__(self) -> str:
        return f"Hook({self.name})"


class PipelineHook(ABC):
    """
    管道钩子接口

    与阶段钩子类似，但作用于整个管道的执行流程
    """

    def __init__(self, name: Optional[str] = None):
        """
        初始化钩子

        Args:
            name: 钩子名称，默认为类名
        """
        self._name = name or self.__class__.__name__

    @property
    def name(self) -> str:
        """获取钩子名称"""
        return self._name

    async def before_pipeline(self, context: RequestContext) -> None:
        """
        在管道开始处理请求之前调用

        Args:
            context: 请求上下文
        """
        pass

    async def after_pipeline(self, context: RequestContext) -> None:
        """
        在管道完成处理请求之后调用

        Args:
            context: 请求上下文
        """
        pass

    async def on_pipeline_error(self, context: RequestContext, error: Exception) -> None:
        """
        在管道处理请求出错时调用

        Args:
            context: 请求上下文
            error: 发生的错误
        """
        pass

    def __str__(self) -> str:
        return f"PipelineHook({self.name})"
