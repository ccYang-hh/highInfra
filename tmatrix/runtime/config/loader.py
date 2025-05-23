import os
import json
import yaml
from pathlib import Path
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Union

from tmatrix.common.logging import init_logger
from .validator import ConfigValidator

logger = init_logger("runtime/config")


class ConfigLoader:
    """
    TODO, 暂不使用，后续支持增强功能
    配置加载器，负责从文件和环境变量加载配置
    """

    def __init__(self,
                 env_prefix: str = "LLM_",
                 default_config_paths: List[str] = None):
        """
        初始化配置加载器

        Args:
            env_prefix: 环境变量前缀
            default_config_paths: 默认配置文件路径列表，按优先级降序排列
        """
        self.env_prefix = env_prefix
        self.default_config_paths = default_config_paths or [
            "./config.yaml",
            "/etc/llm-platform/config.yaml",
            os.path.expanduser("~/.config/llm-platform/config.yaml")
        ]
        self.validator = ConfigValidator()

    def load_from_file(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """从文件加载配置"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件未找到: {file_path}")

        try:
            with open(path, "r") as f:
                if path.suffix.lower() in (".yaml", ".yml"):
                    return yaml.safe_load(f) or {}
                elif path.suffix.lower() == ".json":
                    return json.load(f)
                else:
                    raise ValueError(f"不支持的配置文件格式: {path.suffix}")
        except Exception as e:
            logger.error(f"从 {file_path} 加载配置时出错: {e}")
            raise

    def load_from_env(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """从环境变量加载配置"""
        config = config or {}
        result = config.copy()

        # 特殊环境变量映射
        special_vars = {
            f"{self.env_prefix}HOST": "host",
            f"{self.env_prefix}PORT": ("port", int),
            f"{self.env_prefix}LOG_LEVEL": "log_level",
            f"{self.env_prefix}CONFIG_PATH": None,  # 这个不进入最终配置
        }

        # 处理特殊映射
        for env_name, config_key in special_vars.items():
            if env_name in os.environ:
                # 忽略特殊的控制变量
                if config_key is None:
                    continue

                # 处理需要类型转换的情况
                if isinstance(config_key, tuple):
                    key, conversion_func = config_key
                    try:
                        self._set_nested_key(result, key, conversion_func(os.environ[env_name]))
                    except (ValueError, TypeError):
                        logger.warning(f"无法转换环境变量 {env_name}")
                else:
                    self._set_nested_key(result, config_key, os.environ[env_name])

        # 处理常规环境变量
        prefix_len = len(self.env_prefix)
        for env_name, env_value in os.environ.items():
            if env_name.startswith(self.env_prefix) and env_name not in special_vars:
                # 移除前缀并转换为小写
                key = env_name[prefix_len:].lower()

                # 尝试类型转换
                try:
                    # 尝试作为数字或布尔值
                    if env_value.lower() == "true":
                        value = True
                    elif env_value.lower() == "false":
                        value = False
                    elif env_value.isdigit():
                        value = int(env_value)
                    elif self._is_float(env_value):
                        value = float(env_value)
                    else:
                        value = env_value

                    self._set_nested_key(result, key, value, separator='_')
                except Exception as e:
                    logger.warning(f"处理环境变量 {env_name} 时出错: {e}")

        return result

    @staticmethod
    def _is_float(value: str) -> bool:
        """检查字符串是否可转换为浮点数"""
        try:
            float(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _set_nested_key(config: Dict[str, Any],
                        key: str,
                        value: Any,
                        separator: str = '.') -> None:
        """
        设置嵌套键的值

        Args:
            config: 配置字典
            key: 键路径，可以是嵌套的 (如 "server.port" 或 "server_port")
            value: 值
            separator: 键路径分隔符
        """
        parts = key.split(separator)

        # 处理数组索引，如 cors_origins_0
        current = config
        for i, part in enumerate(parts[:-1]):
            # 检查是否是数组索引
            if part.isdigit() and isinstance(current, list):
                index = int(part)
                # 确保列表足够长
                while len(current) <= index:
                    current.append({})

                if i == len(parts) - 2:  # 如果是最后一个索引
                    current[index] = value
                    return
                else:
                    # 确保是字典
                    if not isinstance(current[index], dict):
                        current[index] = {}
                    current = current[index]
            else:
                # 常规字典键
                if part not in current:
                    current[part] = {}
                elif not isinstance(current[part], dict):
                    # 如果当前值不是字典，但需要作为字典使用，则转换
                    current[part] = {}

                current = current[part]

        # 设置最终值
        last_part = parts[-1]
        if last_part.isdigit() and isinstance(current, list):
            index = int(last_part)
            # 确保列表足够长
            while len(current) <= index:
                current.append(None)
            current[index] = value
        else:
            current[last_part] = value

    def find_default_config(self) -> Optional[str]:
        """查找默认配置文件"""
        for path in self.default_config_paths:
            if os.path.exists(path):
                return path
        return None

    def load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """加载完整配置"""
        # 首先加载默认配置
        config = self.get_default_config()

        # 如果指定了配置文件，从文件加载
        path_from_env = os.environ.get(f"{self.env_prefix}CONFIG_PATH")
        file_path = config_path or path_from_env or self.find_default_config()

        if file_path:
            try:
                file_config = self.load_from_file(file_path)
                # 合并配置，文件配置优先级高于默认配置
                config = self._merge_configs(config, file_config)
                logger.info(f"从 {file_path} 加载配置成功")
            except Exception as e:
                logger.error(f"从 {file_path} 加载配置失败: {e}")
        else:
            logger.warning("未找到配置文件，使用默认配置")

        # 从环境变量加载配置，环境变量优先级最高
        config = self.load_from_env(config)

        # 验证配置
        errors = self.validator.validate_config(config, "runtime")
        if errors:
            logger.warning(f"配置验证发现问题: {errors}")

        return config

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """获取默认配置"""
        # 返回RuntimeConfig的默认值作为字典
        return dict()

    @staticmethod
    def _merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """递归合并配置"""
        result = base.copy()

        for key, value in override.items():
            # 如果两者都是字典，递归合并
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader._merge_configs(result[key], value)
            else:
                # 否则直接覆盖
                result[key] = value

        return result
    