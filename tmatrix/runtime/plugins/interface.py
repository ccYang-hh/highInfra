from fastapi import APIRouter
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Callable, Type, TypeVar, Generic

from tmatrix.common.logging import init_logger
from tmatrix.runtime.plugins import IPluginConfig, PluginConfig, FileSourcePluginConfig
from tmatrix.runtime.pipeline.stages import PipelineStage
from tmatrix.runtime.pipeline.hooks import PipelineHook, StageHook
from tmatrix.runtime.pipeline.pipeline import Pipeline

logger = init_logger("runtime/plugins")

# 定义配置类型变量，用于泛型
T = TypeVar('T', bound='IPluginConfig')


class Plugin(Generic[T], ABC):
    """
    插件接口

    插件是系统扩展的主要机制，可以提供新的功能或修改现有功能
    插件可以注册管道阶段、钩子、API路由等，并管理自己的配置

    限制:
        1.要求所有Plugin必须包含name、version字段
    """
    plugin_name: str
    plugin_version: str
    plugin_description: Optional[str]

    def __init__(self):
        self._initialized = False
        self.config: Optional[T] = None
        if (getattr(self.__class__, 'plugin_name', None) in (None, '') or
                getattr(self.__class__, 'plugin_version', None) in (None, '')):
            raise ValueError("plugin_name 和 plugin_version 不能是空值")

    @classmethod
    def get_name(cls) -> str:
        """
        获取插件名称

        返回值应该是唯一的，用于标识插件

        Returns:
            插件名称
        """
        return cls.plugin_name

    @classmethod
    def get_version(cls) -> str:
        """
        获取插件版本

        Returns:
            插件版本
        """
        return cls.plugin_version

    @classmethod
    def get_description(cls) -> str:
        """
        获取插件描述

        Returns:
            插件描述
        """
        return cls.plugin_description

    async def initialize(self, config: Optional[T] = None, config_dir: Optional[str] = None) -> None:
        """
        初始化插件

        在系统启动时调用，可以执行资源分配、连接建立等操作

        Args:
            config: 插件配置 (可为None，此时插件会创建默认配置)
            config_dir: 配置目录 (仅在config为None且使用文件配置时有效)
        """
        if config is None:
            config = self.create_config(config_dir)

        self.config = config
        self._initialized = True

        # 子类特定的初始化逻辑
        await self._on_initialize()

    @abstractmethod
    async def _on_initialize(self) -> None:
        """
        插件特定的初始化逻辑（由子类实现）
        """
        pass

    async def shutdown(self) -> None:
        """
        关闭插件

        在系统关闭时调用，应该释放所有资源
        """
        if self._initialized:
            # 子类特定的关闭逻辑
            await self._on_shutdown()

        self._initialized = False
        self.config = None

    @abstractmethod
    async def _on_shutdown(self) -> None:
        """
        插件特定的关闭逻辑（由子类实现）
        """
        pass

    def get_config_class(self) -> Optional[Type[T]]:
        """
        获取插件配置类

        每个插件子类必须实现此方法，返回其使用的强类型配置类

        Returns:
            配置类
        """
        return None

    def create_config(self, config_dir: Optional[str] = None) -> Optional[T]:
        """
        创建插件配置实例

        根据配置类型创建相应的配置实例

        Args:
            config_dir: 配置目录 (仅对使用文件配置的插件有效)

        Returns:
            插件配置实例
        """
        config_class = self.get_config_class()

        # 边缘异常场景处理
        if config_class is None:
            return None

        # 简单配置的创建
        if issubclass(config_class, PluginConfig):
            config = config_class(self.__class__.get_name())
            return config

        # 文件配置的创建
        elif issubclass(config_class, FileSourcePluginConfig):
            # 文件配置需要插件名称和配置目录
            config_dir_to_use = config_dir or "./configs"
            config = config_class(self.__class__.get_name(), config_dir=config_dir_to_use)
            return config

        # 其他类型配置的创建 (防止未来可能出现的其他配置类型)
        else:
            raise TypeError(f"初始化插件配置失败")

    async def update_config(self, config_dict: Dict[str, Any]) -> None:
        """
        更新插件配置

        Args:
            config_dict: 新配置字典
        """
        if not self._initialized or not self.config:
            raise RuntimeError("插件未初始化，无法更新配置")

        # 更新配置属性，根据具体配置类型处理
        if isinstance(self.config, PluginConfig):
            # 直接更新简单配置的属性
            for key, value in config_dict.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
        elif isinstance(self.config, FileSourcePluginConfig) and hasattr(self.config, "update_from_dict"):
            # 使用文件配置的update_from_dict方法
            self.config.update_from_dict(config_dict)

        # 子类特定的配置更新逻辑
        await self._on_config_update(config_dict)

    async def _on_config_update(self, config_dict: Dict[str, Any]) -> None:
        """
        插件特定的配置更新逻辑（可由子类重写）

        Args:
            config_dict: 更新的配置字典
        """
        pass

    @abstractmethod
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
