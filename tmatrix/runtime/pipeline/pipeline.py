from typing import List, Optional, Callable

from tmatrix.common.logging import init_logger
from tmatrix.runtime.config import PipelineRoute
from tmatrix.runtime.core.context import RequestContext, RequestState
from .hooks import PipelineHook
from .stages import PipelineStage

logger = init_logger("runtime/pipeline")


class Pipeline:
    """
    请求处理管道

    管道是由多个处理阶段组成的流程控制器，负责按照正确的顺序执行这些阶段
    支持复杂的依赖关系和并行执行，以及各种钩子机制

    TODO, 后续演进的特性:
        1.支持DAG编排
        2.
    """

    def __init__(self, name: str):
        """
        初始化Pipeline

        Args:
            name: Pipeline名称
        """
        self.name = name

        # 阶段列表 - 按照执行顺序排列
        self.stages: List[PipelineStage] = []

        # Pipeline钩子
        self.hooks: List[PipelineHook] = []

        # 错误处理
        self.error_handler: Optional[Callable] = None

        # 路由配置
        self.routes: List[PipelineRoute] = []

        # 验证状态
        self._is_validated = False

    def add_stage(self, stage: PipelineStage) -> 'Pipeline':
        """
        添加处理阶段到执行序列末尾

        Args:
            stage: 处理阶段

        Returns:
            Pipeline实例，用于链式调用
        """
        self.stages.append(stage)
        return self

    def add_hook(self, hook: PipelineHook) -> 'Pipeline':
        """
        添加Pipeline钩子

        Args:
            hook: Pipeline钩子

        Returns:
            Pipeline实例，用于链式调用
        """
        self.hooks.append(hook)
        return self

    def set_error_handler(self, handler: Callable) -> 'Pipeline':
        """
        设置错误处理器

        Args:
            handler: 错误处理函数

        Returns:
            Pipeline实例，用于链式调用
        """
        self.error_handler = handler
        return self

    def add_route(self, route: PipelineRoute) -> 'Pipeline':
        """
        添加路由配置

        Args:
            route: 路由

        Returns:
            Pipeline实例，用于链式调用
        """
        self.routes.append(route)
        return self

    def validate(self) -> bool:
        """
        验证Pipeline配置

        TODO, 目前只是标记为已验证状态，后续可添加验证逻辑

        Returns:
            验证是否通过
        """
        self._is_validated = True
        return True

    async def _execute_stage(self, stage: PipelineStage, context: RequestContext) -> None:
        """
        执行单个阶段

        Args:
            stage: 阶段对象
            context: 请求上下文
        """
        try:
            await stage.execute(context)
        except Exception as e:
            if self.error_handler:
                try:
                    self.error_handler(context, stage.name, e)
                except Exception as handler_error:
                    logger.error(f"Error in pipeline error handler: {handler_error}")
                    context.fail(e)
            else:
                context.fail(e)

    async def _execute_pipeline_hooks(self, hook_method: str, context: RequestContext, **kwargs) -> None:
        """
        执行管道钩子

        Args:
            hook_method: 钩子方法名
            context: 请求上下文
            **kwargs: 额外参数
        """
        for hook in self.hooks:
            try:
                method = getattr(hook, hook_method)
                await method(context, **kwargs)
            except Exception as e:
                logger.error(f"Error in pipeline hook {hook.name}.{hook_method}: {e}")

    async def process(self, context: RequestContext) -> RequestContext:
        """
        处理请求，按序执行所有阶段，并处理钩子和错误

        Args:
            request: Fast API Request
            context: 请求上下文

        Returns:
            处理后的请求上下文
        """
        if not self._is_validated:
            self.validate()

        # 记录开始时间
        context.start_processing()

        # 执行管道前钩子
        await self._execute_pipeline_hooks("before_pipeline", context)

        try:
            # 按序执行所有阶段
            for stage in self.stages:
                # 执行当前阶段
                await self._execute_stage(stage, context)

                # 如果上下文已完成或失败，中断执行
                if context.state in (RequestState.COMPLETED, RequestState.FAILED):
                    break

            # 确保请求被标记为完成
            if context.state not in (RequestState.COMPLETED, RequestState.FAILED):
                context.complete()

        except Exception as e:
            logger.error(f"Error during pipeline execution: {e}", exc_info=True)
            context.fail(e)

            # 执行管道错误钩子
            await self._execute_pipeline_hooks("on_pipeline_error", context, error=e)

        # 执行管道后钩子
        await self._execute_pipeline_hooks("after_pipeline", context)

        # 返回处理后的上下文
        return context

    def __str__(self) -> str:
        status = "validated" if self._is_validated else "not validated"
        stage_count = len(self.stages)
        hook_count = len(self.hooks)
        return f"Pipeline({self.name}, {status}, stages={stage_count}, hooks={hook_count})"
