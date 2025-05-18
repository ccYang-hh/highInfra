from typing import Any, Dict, List, Union
from jsonschema import validate, ValidationError

from tmatrix.components.logging import init_logger
from tmatrix.components.errors import Errors
from tmatrix.components.errors.modules import ConfigErrors
from .schema import get_schema

logger = init_logger("runtime/config")


class ConfigValidator:
    """
    配置验证器，用于验证配置的正确性
    """

    def __init__(self):
        """初始化验证器"""
        # 注册的验证模式
        self.schemas = {}
        self.register_schema("runtime", get_schema("runtime"))

    def register_schema(self, name: str, schema: Dict[str, Any]) -> None:
        """
        注册验证模式

        Args:
            name: 模式名称
            schema: JSON Schema
        """
        self.schemas[name] = schema

    def validate_config(self, config: Union[Dict[str, Any]],
                        schema_name: str = "runtime") -> List[str]:
        """
        验证配置

        Args:
            config: 要验证的配置
            schema_name: 使用的模式名称

        Returns:
            错误消息列表，如果验证通过则为空
        """
        if schema_name not in self.schemas:
            return [f"未知的 Schema: {schema_name}"]

        schema = self.schemas[schema_name]

        try:
            validate(instance=config, schema=schema)
            return []
        except ValidationError as e:
            # 格式化验证错误
            path = ".".join(str(p) for p in e.path)
            logger.error(f"配置验证错误 {path}: {e.message}")
            Errors.raise_error(ConfigErrors.SCHEMA_NOT_DEFINITION)
