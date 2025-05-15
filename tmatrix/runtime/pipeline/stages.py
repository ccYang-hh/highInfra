import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, TypeVar

from tmatrix.components.logging import init_logger
from ..context import RequestContext

logger = init_logger("runtime/pipeline")

# 用于支持泛型返回值的类型变量
T = TypeVar('T')


class PipelineStage(ABC):
    """
    管道处理阶段抽象基类

    管道中的每个处理阶段必须继承此类，并实现process方法
    """

    def __init__(self, name: Optional[str] = None, enabled: bool = True):
        """
        初始化管道阶段

        Args:
            name: 阶段名称，默认为类名
            enabled: 是否启用此阶段
        """
        self._name = name or self.__class__.__name__
        self._enabled = enabled
        self._next_stages: List["PipelineStage"] = []
        self._prerequisites: Set[str] = set()
        self._config: Dict[str, Any] = {}
        self._hooks: Dict[str, List["StageHook"]] = {
            "before": [],
            "after": [],
            "error": []
        }

    @property
    def name(self) -> str:
        """获取阶段名称"""
        return self._name

    @property
    def enabled(self) -> bool:
        """获取阶段是否启用"""
        return self._enabled

    def enable(self) -> "PipelineStage":
        """启用阶段"""
        self._enabled = True
        return self

    def disable(self) -> "PipelineStage":
        """禁用阶段"""
        self._enabled = False
        return self

    def configure(self, config: Dict[str, Any]) -> "PipelineStage":
        """
        配置阶段

        Args:
            config: 配置字典

        Returns:
            自身，用于链式调用
        """
        self._config.update(config)
        return self

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        获取配置值

        Args:
            key: 配置键
            default: 默认值

        Returns:
            配置值或默认值
        """
        return self._config.get(key, default)

    def requires(self, stage_name: str) -> "PipelineStage":
        """
        指定此阶段依赖的前置阶段

        Args:
            stage_name: 前置阶段名称

        Returns:
            自身，用于链式调用
        """
        self._prerequisites.add(stage_name)
        return self

    def get_prerequisites(self) -> Set[str]:
        """
        获取所有前置依赖

        Returns:
            前置阶段名称集合
        """
        return self._prerequisites

    def add_next_stage(self, stage: "PipelineStage") -> "PipelineStage":
        """
        添加后续阶段

        Args:
            stage: 后续阶段实例

        Returns:
            自身，用于链式调用
        """
        if stage not in self._next_stages:
            self._next_stages.append(stage)
        return self

    def get_next_stages(self) -> List["PipelineStage"]:
        """
        获取后续阶段

        Returns:
            后续阶段列表
        """
        return self._next_stages

    def add_hook(self, hook: "StageHook", hook_type: str = "after") -> "PipelineStage":
        """
        添加阶段钩子

        Args:
            hook: 钩子实例
            hook_type: 钩子类型 (before, after, error)

        Returns:
            自身，用于链式调用
        """
        if hook_type in self._hooks:
            self._hooks[hook_type].append(hook)
        else:
            raise ValueError(f"Unknown hook type: {hook_type}")
        return self

    async def _execute_hooks(self, hook_type: str, context: RequestContext, **kwargs) -> None:
        """
        执行指定类型的所有钩子

        Args:
            hook_type: 钩子类型
            context: 请求上下文
            **kwargs: 其他参数
        """
        for hook in self._hooks.get(hook_type, []):
            try:
                if hook_type == "before":
                    await hook.before_stage(self, context)
                elif hook_type == "after":
                    await hook.after_stage(self, context)
                elif hook_type == "error":
                    error = kwargs.get("error")
                    if error:
                        await hook.on_error(self, context, error)
            except Exception as e:
                logger.error(f"Error executing {hook_type} hook {hook.__class__.__name__}: {e}")

    @abstractmethod
    async def process(self, context: RequestContext) -> None:
        """
        处理请求上下文

        这是管道阶段的核心方法，负责实际业务逻辑的处理

        Args:
            context: 请求上下文对象
        """
        pass

    async def execute(self, context: RequestContext) -> None:
        """
        执行阶段处理，包括钩子的调用

        Args:
            context: 请求上下文
        """
        if not self._enabled:
            logger.debug(f"Stage {self.name} is disabled, skipping")
            return

        start_time = time.time()

        # 执行前置钩子
        await self._execute_hooks("before", context)

        # 执行处理逻辑
        try:
            logger.debug(f"Executing stage {self.name} for request {context.request_id}")
            await self.process(context)
            duration = time.time() - start_time
            logger.debug(f"Stage {self.name} completed in {duration:.4f}s")

            # 执行后置钩子
            await self._execute_hooks("after", context)

        except Exception as e:
            logger.error(f"Error in stage {self.name}: {e}", exc_info=True)

            # 执行错误钩子
            await self._execute_hooks("error", context, error=e)

            # 传播异常
            raise

    def __str__(self) -> str:
        status = "enabled" if self._enabled else "disabled"
        next_stages = ", ".join([stage.name for stage in self._next_stages]) or "none"
        return f"Stage({self.name}, {status}, next=[{next_stages}])"
