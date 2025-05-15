from fastapi import APIRouter
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Callable, Type

from tmatrix.components.logging import init_logger
from tmatrix.runtime.plugins import PluginConfig
from tmatrix.runtime.pipeline.stages import PipelineStage
from tmatrix.runtime.pipeline.hooks import PipelineHook, StageHook
from tmatrix.runtime.pipeline.pipeline import Pipeline

logger = init_logger("runtime/plugins")


class Plugin(ABC):
    """
    插件接口

    插件是系统扩展的主要机制，可以提供新的功能或修改现有功能
    插件可以注册管道阶段、钩子、API路由等，并管理自己的配置
    """

    @abstractmethod
    def get_name(self) -> str:
        """
        获取插件名称

        返回值应该是唯一的，用于标识插件

        Returns:
            插件名称
        """
        pass

    @abstractmethod
    def get_version(self) -> str:
        """
        获取插件版本

        Returns:
            插件版本
        """
        pass

    def get_description(self) -> str:
        """
        获取插件描述

        Returns:
            插件描述
        """
        return "No description provided"

    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        初始化插件

        在系统启动时调用，可以执行资源分配、连接建立等操作

        Args:
            config: 插件配置
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """
        关闭插件

        在系统关闭时调用，应该释放所有资源
        """
        pass

    def get_config_class(self) -> Optional[Type[PluginConfig]]:
        """
        获取插件配置类

        返回此插件使用的配置类，如果插件有自定义配置需求

        Returns:
            配置类或None
        """
        return None

    def create_config(self, config_dir: Optional[str] = None) -> Optional[PluginConfig]:
        """
        创建插件配置实例

        Args:
            config_dir: 配置目录

        Returns:
            插件配置实例或None
        """
        config_class = self.get_config_class()
        if config_class:
            return config_class(self.get_name(), config_dir)
        return None

    async def update_config(self, config: Dict[str, Any]) -> None:
        """
        更新插件配置

        插件可以重写此方法以支持热重载配置

        Args:
            config: 新配置
        """
        pass

    def get_pipeline_stages(self) -> List[PipelineStage]:
        """
        获取插件提供的管道阶段

        Returns:
            管道阶段列表
        """
        return []

    def get_stage_hooks(self) -> List[StageHook]:
        """
        获取插件提供的阶段钩子

        Returns:
            阶段钩子列表
        """
        return []

    def get_pipeline_hooks(self) -> List[PipelineHook]:
        """
        获取插件提供的管道钩子

        Returns:
            管道钩子列表
        """
        return []

    def get_api_router(self) -> Optional[APIRouter]:
        """
        获取插件提供的API路由

        Returns:
            API路由器或None
        """
        return None

    def get_dependencies(self) -> Set[str]:
        """
        获取插件依赖

        返回此插件依赖的其他插件名称

        Returns:
            依赖插件名称集合
        """
        return set()

    def configure_pipeline(self, pipeline: Pipeline) -> None:
        """
        配置管道

        允许插件自定义管道的配置，如阶段连接、钩子添加等

        Args:
            pipeline: 待配置的管道
        """
        pass

    def get_extension_points(self) -> Dict[str, Callable]:
        """
        获取插件提供的扩展点

        扩展点是其他插件可以使用的函数或对象

        Returns:
            扩展点字典，键为扩展点名称，值为实现对象
        """
        return {}

    def __str__(self) -> str:
        return f"Plugin({self.get_name()} v{self.get_version()})"
