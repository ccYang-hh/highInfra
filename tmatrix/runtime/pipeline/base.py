from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type, Callable
import asyncio
import time
import logging
from ..context import RequestContext, RequestState

logger = logging.getLogger(__name__)


class PipelineStage(ABC):
    """
    管道阶段抽象基类
    每个处理阶段都需要实现process方法
    """

    @abstractmethod
    async def process(self, context: RequestContext) -> None:
        """
        处理请求上下文

        Args:
            context: 当前请求上下文

        应当在处理过程中更新上下文状态，如果出现错误应调用context.fail()
        """
        pass

    def get_name(self) -> str:
        """获取阶段名称，默认为类名"""
        return self.__class__.__name__

    def __str__(self) -> str:
        return f"PipelineStage({self.get_name()})"


class PipelineHook(ABC):
    """
    管道钩子基类
    钩子可以在管道的不同阶段执行，无需修改主流程
    """

    @abstractmethod
    async def before_stage(self, stage: PipelineStage, context: RequestContext) -> None:
        """在阶段执行前调用"""
        pass

    @abstractmethod
    async def after_stage(self, stage: PipelineStage, context: RequestContext, error: Optional[Exception] = None) -> None:
        """在阶段执行后调用"""
        pass

    @abstractmethod
    async def on_pipeline_start(self, context: RequestContext) -> None:
        """在管道开始处理请求时调用"""
        pass

    @abstractmethod
    async def on_pipeline_end(self, context: RequestContext) -> None:
        """在管道完成处理请求时调用"""
        pass


class Pipeline:
    """
    请求处理管道
    将多个处理阶段组合起来处理请求
    """

    def __init__(self, name: str = "main"):
        self.name = name
        self.stages: List[PipelineStage] = []
        self.hooks: List[PipelineHook] = []
        self.error_handlers: Dict[Type[Exception], Callable] = {}
        logger.info(f"Created pipeline: {name}")

    def add_stage(self, stage: PipelineStage) -> "Pipeline":
        """
        添加处理阶段

        Args:
            stage: 处理阶段实例

        Returns:
            自身，支持链式调用
        """
        self.stages.append(stage)
        logger.info(f"Added stage {stage.get_name()} to pipeline {self.name}")
        return self

    def add_hook(self, hook: PipelineHook) -> "Pipeline":
        """
        添加管道钩子

        Args:
            hook: 钩子实例

        Returns:
            自身，支持链式调用
        """
        self.hooks.append(hook)
        return self

    def add_error_handler(self, exception_type: Type[Exception], handler: Callable) -> "Pipeline":
        """
        添加错误处理器

        Args:
            exception_type: 异常类型
            handler: 处理函数，接收异常和上下文作为参数

        Returns:
            自身，支持链式调用
        """
        self.error_handlers[exception_type] = handler
        return self

    async def process(self, context: RequestContext) -> RequestContext:
        """
        处理请求，按顺序执行所有阶段

        Args:
            context: 请求上下文

        Returns:
            处理后的请求上下文
        """
        # 通知钩子管道开始处理
        for hook in self.hooks:
            try:
                await hook.on_pipeline_start(context)
            except Exception as e:
                logger.error(f"Error in hook {hook.__class__.__name__}.on_pipeline_start: {e}")

        # 逐一执行各个阶段
        for stage in self.stages:
            # 如果请求已经完成或失败，停止处理
            if context.state in (RequestState.COMPLETED, RequestState.FAILED):
                break

            logger.debug(f"Pipeline {self.name}: Executing stage {stage.get_name()} for request {context.request_id}")
            stage_start = time.time()

            # 通知钩子阶段开始
            for hook in self.hooks:
                try:
                    await hook.before_stage(stage, context)
                except Exception as e:
                    logger.error(f"Error in hook {hook.__class__.__name__}.before_stage: {e}")

            # 执行阶段处理
            try:
                await stage.process(context)
                stage_end = time.time()
                context.metrics[f"stage.{stage.get_name()}.latency"] = stage_end - stage_start

                # 通知钩子阶段结束
                for hook in self.hooks:
                    try:
                        await hook.after_stage(stage, context)
                    except Exception as e:
                        logger.error(f"Error in hook {hook.__class__.__name__}.after_stage: {e}")

            except Exception as e:
                logger.error(f"Error in pipeline stage {stage.get_name()}: {e}", exc_info=True)

                # 查找合适的错误处理器
                handled = False
                for exc_type, handler in self.error_handlers.items():
                    if isinstance(e, exc_type):
                        try:
                            handler(e, context)
                            handled = True
                            break
                        except Exception as handler_error:
                            logger.error(f"Error in exception handler: {handler_error}")

                # 如果没有处理器处理或处理器失败，标记请求为失败
                if not handled:
                    context.fail(e)

                # 通知钩子阶段出错
                for hook in self.hooks:
                    try:
                        await hook.after_stage(stage, context, error=e)
                    except Exception as hook_error:
                        logger.error(f"Error in hook {hook.__class__.__name__}.after_stage: {hook_error}")

        # 通知钩子管道处理结束
        for hook in self.hooks:
            try:
                await hook.on_pipeline_end(context)
            except Exception as e:
                logger.error(f"Error in hook {hook.__class__.__name__}.on_pipeline_end: {e}")

        return context

    def __str__(self) -> str:
        stages = ", ".join(stage.get_name() for stage in self.stages)
        return f"Pipeline({self.name}, stages=[{stages}])"
    