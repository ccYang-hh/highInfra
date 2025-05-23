from dataclasses import dataclass
from fastapi import APIRouter
from typing import Dict, List, Optional, Callable

from tmatrix.common.logging import init_logger
from tmatrix.runtime.plugins import PluginManager
from tmatrix.runtime.config import PipelineConfig
from .pipeline import Pipeline

logger = init_logger("runtime/pipeline")


@dataclass
class PipelineBuilderConfig:
    pipelines: List[PipelineConfig]
    default: Optional[PipelineConfig] = None


class PipelineBuilder:
    """Pipeline构建器"""

    def __init__(self,
                 plugin_manager: 'PluginManager',
                 pipeline_build_config: PipelineBuilderConfig,
    ):
        """
        初始化PipelineBuilder

        Args:
            plugin_manager: 插件管理器，用于获取插件资源
            pipeline_build_config: Pipeline配置集合
        """
        self.plugin_manager = plugin_manager
        self.pipeline_build_config = pipeline_build_config
        self.pipelines: Dict[str, Pipeline] = {}

    def _create_error_handler(self) -> Optional[Callable]:
        """
        创建错误处理器
            1. TODO 支持动态配置Error Handler

        Args:
            handler_name: 错误处理器名称

        Returns:
            错误处理函数，如果不存在则返回None
        """
        # 尝试动态导入
        logger.warning(f"当前Pipeline尚未支持动态Error Handler")

        def error_handler():
            logger.warning(f"当前Pipeline尚未支持动态Error Handler，暂不处理")
            raise RuntimeError("Pipeline内部错误!")

        return error_handler

    def build_pipeline(self, config: PipelineConfig) -> Pipeline:
        """
        构建单个Pipeline

        Args:
            config: Pipeline配置

        Returns:
            构建的Pipeline实例
        """
        # 创建Pipeline
        pipeline = Pipeline(name=config.pipeline_name)

        # 为每个插件添加资源
        for plugin_name in config.plugins:
            # 获取并添加阶段
            stages = self.plugin_manager.get_pipeline_stages(plugin_name)
            for stage in stages:
                pipeline.add_stage(stage)

            # 获取并添加Pipeline钩子
            pipeline_hooks = self.plugin_manager.get_pipeline_hooks(plugin_name)
            for hook in pipeline_hooks:
                pipeline.add_hook(hook)

            # # 获取插件路由
            # try:
            #     plugin_router = self.plugin_manager.get_api_router(plugin_name)
            #     pipeline.
            #     for route in plugin_routes:
            #         pipeline.add_route(path=route.path, method=route.method)
            # except (AttributeError, NotImplementedError):
            #     # 如果插件管理器不支持get_routes方法，忽略错误
            #     pass

        # 设置错误处理器
        handler = self._create_error_handler()
        if handler:
            pipeline.set_error_handler(handler)

        # 添加配置中的路由
        for route in config.routes:
            pipeline.add_route(route)

        # 验证Pipeline
        pipeline.validate()

        return pipeline

    def build_pipelines(self) -> Dict[str, Pipeline]:
        """
        构建所有Pipeline

        Returns:
            构建的Pipeline字典
        """
        results = {}

        # 构建默认Pipeline（如果存在）
        if self.pipeline_build_config.default:
            try:
                default_config = self.pipeline_build_config.default
                default_pipeline = self.build_pipeline(default_config)
                results['default'] = default_pipeline
            except Exception as e:
                logger.error(f"构建默认Pipeline失败: {e}")

        # 构建其他Pipeline
        for pipeline in self.pipeline_build_config.pipelines:
            if pipeline.pipeline_name == 'default' and 'default' in results:
                continue  # 避免重复构建默认Pipeline

            try:
                pipeline = self.build_pipeline(pipeline)
                results[pipeline.name] = pipeline
            except Exception as e:
                logger.error(f"构建Pipeline '{pipeline.name}'失败: {e}", exc_info=True)
                continue

        self.pipelines.update(results)
        return results

    def get_pipeline(self, name: str) -> Optional[Pipeline]:
        """
        获取已构建的Pipeline

        Args:
            name: Pipeline名称

        Returns:
            Pipeline实例，如果不存在则返回None
        """
        return self.pipelines.get(name)

    def get_api_router(self) -> APIRouter:
        """
        获取所有Pipeline的API路由器

        Returns:
            FastAPI的APIRouter实例
        """
        router = APIRouter()

        for name, pipeline in self.pipelines.items():
            # 将每个Pipeline的路由器嵌套到主路由器
            pipeline_router = pipeline.get_api_router()
            router.include_router(pipeline_router)

        return router
