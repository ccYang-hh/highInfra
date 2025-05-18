import re
from typing import Dict, List, Optional, Set, Any, Union, Callable, Pattern, Tuple
from fastapi import FastAPI, Request, Response, HTTPException

from tmatrix.components.logging import init_logger

logger = init_logger("runtime/api")


class APIPipelineMapping:
    """
    API与Pipeline映射关系管理
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = APIPipelineMapping()
        return cls._instance

    def __init__(self):
        """初始化API与Pipeline映射"""
        # 精确路径映射：{路径: pipeline_name}
        self.exact_mappings: Dict[str, str] = {}

        # 前缀映射：{前缀: pipeline_name}
        self.prefix_mappings: Dict[str, str] = {}

        # 正则表达式映射：[(正则表达式, pipeline_name)]
        self.regex_mappings: List[Tuple[Pattern, str]] = []

        # 全局默认Pipeline
        self.default_pipeline: Optional[str] = None

    def map_exact(self, path: str, pipeline_name: str) -> None:
        """
        映射精确路径到Pipeline

        Args:
            path: API路径
            pipeline_name: Pipeline名称
        """
        self.exact_mappings[path] = pipeline_name
        logger.info(f"Mapped exact path '{path}' to pipeline '{pipeline_name}'")

    def map_prefix(self, prefix: str, pipeline_name: str) -> None:
        """
        映射路径前缀到Pipeline

        Args:
            prefix: API路径前缀
            pipeline_name: Pipeline名称
        """
        # 确保前缀以/开头
        if not prefix.startswith('/'):
            prefix = '/' + prefix

        # 确保前缀以/结尾
        if not prefix.endswith('/'):
            prefix = prefix + '/'

        self.prefix_mappings[prefix] = pipeline_name
        logger.info(f"Mapped prefix '{prefix}' to pipeline '{pipeline_name}'")

    def map_regex(self, pattern: str, pipeline_name: str) -> None:
        """
        映射正则表达式到Pipeline

        Args:
            pattern: 路径匹配的正则表达式
            pipeline_name: Pipeline名称
        """
        regex = re.compile(pattern)
        self.regex_mappings.append((regex, pipeline_name))
        logger.info(f"Mapped regex '{pattern}' to pipeline '{pipeline_name}'")

    def set_default_pipeline(self, pipeline_name: str) -> None:
        """
        设置默认Pipeline

        Args:
            pipeline_name: Pipeline名称
        """
        self.default_pipeline = pipeline_name
        logger.info(f"Set default API pipeline to '{pipeline_name}'")

    def get_pipeline_for_path(self, path: str) -> Optional[str]:
        """
        获取路径对应的Pipeline名称

        Args:
            path: API路径

        Returns:
            Pipeline名称或None
        """
        # 1. 检查精确匹配
        if path in self.exact_mappings:
            return self.exact_mappings[path]

        # 2. 检查前缀匹配
        for prefix, pipeline_name in self.prefix_mappings.items():
            if path.startswith(prefix):
                return pipeline_name

        # 3. 检查正则表达式匹配
        for regex, pipeline_name in self.regex_mappings:
            if regex.match(path):
                return pipeline_name

        # 4. 返回默认Pipeline
        return self.default_pipeline
