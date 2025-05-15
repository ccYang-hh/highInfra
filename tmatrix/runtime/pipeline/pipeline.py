import asyncio
from graphlib import TopologicalSorter
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Callable

from tmatrix.components.logging import init_logger
from ..context import RequestContext, RequestState
from .hooks import PipelineHook
from .stages import PipelineStage

logger = init_logger("runtime/pipeline")


class Pipeline:
    """
    请求处理管道

    管道是由多个处理阶段组成的流程控制器，负责按照正确的顺序执行这些阶段
    支持复杂的依赖关系和并行执行，以及各种钩子机制
    """

    def __init__(self,
                 name: str,
                 max_parallelism: int = 4,
                 executor: Optional[ThreadPoolExecutor] = None,
                 error_handler: Optional[Callable] = None):
        """
        初始化管道

        Args:
            name: 管道名称
            max_parallelism: 最大并行度
            executor: 线程池执行器，用于CPU密集型操作
            error_handler: 默认错误处理函数
        """
        self.name = name
        self.max_parallelism = max_parallelism
        self.executor = executor or ThreadPoolExecutor(max_workers=max_parallelism)
        self.error_handler = error_handler

        # 阶段注册表
        self.stages: Dict[str, PipelineStage] = {}

        # 管道钩子
        self.hooks: List[PipelineHook] = []

        # 阶段执行顺序 (拓扑排序结果的缓存)
        self._execution_order: Optional[List[str]] = None

        # 可选的入口和退出点
        self.entry_stages: List[str] = []
        self.exit_stages: List[str] = []

        # 标志管道是否已验证
        self._is_validated = False

    def add_stage(self, stage: PipelineStage) -> "Pipeline":
        """
        添加处理阶段

        Args:
            stage: 处理阶段实例

        Returns:
            自身，用于链式调用
        """
        if stage.name in self.stages:
            raise ValueError(f"Stage with name '{stage.name}' already exists in pipeline")

        self.stages[stage.name] = stage
        # 添加阶段时需要重新验证管道
        self._is_validated = False
        self._execution_order = None
        return self

    def connect(self, from_stage: str, to_stage: str) -> "Pipeline":
        """
        连接两个阶段，建立执行顺序

        Args:
            from_stage: 源阶段名称
            to_stage: 目标阶段名称

        Returns:
            自身，用于链式调用
        """
        if from_stage not in self.stages:
            raise ValueError(f"Source stage '{from_stage}' not found in pipeline")
        if to_stage not in self.stages:
            raise ValueError(f"Target stage '{to_stage}' not found in pipeline")

        # 建立连接
        self.stages[from_stage].add_next_stage(self.stages[to_stage])

        # 连接更改时需要重新验证
        self._is_validated = False
        self._execution_order = None
        return self

    def add_hook(self, hook: PipelineHook) -> "Pipeline":
        """
        添加管道钩子

        Args:
            hook: 管道钩子实例

        Returns:
            自身，用于链式调用
        """
        self.hooks.append(hook)
        return self

    def set_entry_points(self, stage_names: List[str]) -> "Pipeline":
        """
        设置管道的入口点

        Args:
            stage_names: 入口阶段名称列表

        Returns:
            自身，用于链式调用
        """
        for name in stage_names:
            if name not in self.stages:
                raise ValueError(f"Entry point stage '{name}' not found in pipeline")

        self.entry_stages = stage_names
        self._is_validated = False
        return self

    def set_exit_points(self, stage_names: List[str]) -> "Pipeline":
        """
        设置管道的退出点

        Args:
            stage_names: 退出阶段名称列表

        Returns:
            自身，用于链式调用
        """
        for name in stage_names:
            if name not in self.stages:
                raise ValueError(f"Exit point stage '{name}' not found in pipeline")

        self.exit_stages = stage_names
        self._is_validated = False
        return self

    def get_dependency_graph(self) -> Dict[str, Set[str]]:
        """
        获取阶段依赖图

        Returns:
            依赖关系字典，键为阶段名称，值为依赖的阶段集合
        """
        graph = {}
        for name, stage in self.stages.items():
            # 显式依赖
            dependencies = set(stage.get_prerequisites())

            # 隐式依赖 (来自连接)
            for other_name, other_stage in self.stages.items():
                if stage in other_stage.get_next_stages():
                    dependencies.add(other_name)

            graph[name] = dependencies

        return graph

    def validate(self) -> "Pipeline":
        """
        验证管道配置

        检查依赖关系是否合法，是否存在循环依赖等

        Returns:
            自身，用于链式调用

        Raises:
            ValueError: 如果发现配置问题
        """
        if self._is_validated:
            return self

        if not self.stages:
            raise ValueError("Pipeline has no stages")

        # 获取依赖图
        graph = self.get_dependency_graph()

        # 检查每个依赖是否存在
        for stage_name, dependencies in graph.items():
            for dep in dependencies:
                if dep not in self.stages:
                    raise ValueError(f"Stage '{stage_name}' depends on '{dep}' which is not in the pipeline")

        # 尝试拓扑排序，会检测循环依赖
        try:
            sorter = TopologicalSorter(graph)
            self._execution_order = list(sorter.static_order())
        except Exception as e:
            raise ValueError(f"Invalid pipeline configuration: {e}")

        # 确定入口点
        if not self.entry_stages:
            # 自动识别入口点：没有入边的节点
            no_deps = [name for name, deps in graph.items() if not deps]
            if not no_deps:
                raise ValueError("No entry points found in pipeline and none specified")
            self.entry_stages = no_deps
            logger.info(f"Automatically identified entry points: {', '.join(no_deps)}")

        # 确定退出点
        if not self.exit_stages:
            # 自动识别退出点：没有出边的节点
            has_next = set()
            for stage in self.stages.values():
                for next_stage in stage.get_next_stages():
                    has_next.add(next_stage.name)

            no_next = [name for name in self.stages if name not in has_next]
            if not no_next:
                raise ValueError("No exit points found in pipeline and none specified")
            self.exit_stages = no_next
            logger.info(f"Automatically identified exit points: {', '.join(no_next)}")

        self._is_validated = True
        return self

    def _get_executable_stages(self, executed: Set[str]) -> Set[str]:
        """
        获取当前可执行的阶段

        Args:
            executed: 已执行阶段集合

        Returns:
            可执行阶段名称集合
        """
        graph = self.get_dependency_graph()
        result = set()

        for stage_name, dependencies in graph.items():
            if stage_name not in executed and dependencies.issubset(executed):
                result.add(stage_name)

        return result

    async def _execute_stage(self, stage_name: str, context: RequestContext) -> None:
        """
        执行单个阶段

        Args:
            stage_name: 阶段名称
            context: 请求上下文
        """
        stage = self.stages[stage_name]
        try:
            await stage.execute(context)
        except Exception as e:
            if self.error_handler:
                try:
                    self.error_handler(context, stage_name, e)
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
        处理请求

        按照依赖关系执行所有阶段，并处理钩子和错误

        Args:
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
            # 跟踪已执行的阶段
            executed_stages = set()

            # 按照依赖顺序执行阶段
            while len(executed_stages) < len(self.stages):
                # 获取可执行的阶段
                executable = self._get_executable_stages(executed_stages)

                # 如果没有可执行的阶段但还有未执行的阶段，说明存在死锁
                if not executable and len(executed_stages) < len(self.stages):
                    remaining = set(self.stages.keys()) - executed_stages
                    raise RuntimeError(f"Pipeline execution deadlock detected, remaining stages: {remaining}")

                # 并行执行可执行的阶段
                if executable:
                    # 限制并行度
                    batches = []
                    current_batch = []

                    for stage_name in executable:
                        current_batch.append(stage_name)
                        if len(current_batch) >= self.max_parallelism:
                            batches.append(current_batch)
                            current_batch = []

                    if current_batch:
                        batches.append(current_batch)

                    # 按批执行
                    for batch in batches:
                        tasks = [self._execute_stage(stage_name, context) for stage_name in batch]
                        await asyncio.gather(*tasks)

                        # 更新已执行阶段
                        executed_stages.update(batch)

                        # 检查上下文状态，如果已完成或失败则中断执行
                        if context.state in (RequestState.COMPLETED, RequestState.FAILED):
                            break

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
