import copy
import threading
from typing import Any, Dict, List, Optional, Callable, TypeVar

from tmatrix.components.logging import init_logger

from .errors import *
from .config import RuntimeConfig
from .loader import ConfigLoader
from .validator import ConfigValidator

logger = init_logger("runtime/config")

T = TypeVar('T')


class ConfigManager:
    """配置管理器，只负责运行时核心配置"""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, config_path: Optional[str] = None) -> 'ConfigManager':
        """获取单例实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(config_path)
            return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        """初始化配置管理器"""
        self.config_path = config_path
        self.loader = ConfigLoader()
        self.validator = ConfigValidator()

        # 加载原始字典配置
        config_dict = self.loader.load_config(config_path)

        # 转换为RuntimeConfig对象
        self.config = RuntimeConfig.from_dict(config_dict)

        # 验证配置
        errors = self.validator.validate_config(self.config, "runtime")
        if errors:
            logger.error(f"配置文件校验失败: {errors}")
            raise_error(SCHEMA_VALIDATE_ERROR, path=config_path)

        # 配置变更监听器 - 使用(属性名, 新值)的格式
        self._change_listeners = []

    def get_config(self) -> RuntimeConfig:
        """获取完整运行时配置"""
        return copy.deepcopy(self.config)

    def get_value(self, key: str, default: Any = None) -> Any:
        """
        获取配置值

        Args:
            key: 属性路径，可以是嵌套的 (如 "pipelines.main.max_parallelism")
            default: 如果值不存在，返回默认值

        Returns:
            配置值或默认值
        """
        parts = key.split('.')
        value = self.config

        try:
            for part in parts:
                if hasattr(value, part):
                    value = getattr(value, part)
                elif isinstance(value, dict) and part in value:
                    value = value[part]
                elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                    value = value[int(part)]
                else:
                    return default
            return value
        except:
            return default

    def set_value(self, key: str, value: Any) -> None:
        """
        设置配置值

        Args:
            key: 属性路径，可以是嵌套的 (如 "port")
            value: 要设置的值
        """
        parts = key.split('.')
        target = self.config
        attr_path = []

        # 对于简单属性直接设置
        if len(parts) == 1 and hasattr(self.config, parts[0]):
            setattr(self.config, parts[0], value)
            self._notify_listeners(key, value)
            return

        # 对于嵌套属性，需要逐层导航
        for i, part in enumerate(parts[:-1]):
            attr_path.append(part)

            if hasattr(target, part):
                target = getattr(target, part)
            elif isinstance(target, dict) and part in target:
                target = target[part]
            elif isinstance(target, list) and part.isdigit():
                idx = int(part)
                if idx < len(target):
                    target = target[idx]
                else:
                    logger.error(f"设置配置时索引越界: {'.'.join(attr_path)}")
                    return
            else:
                logger.error(f"无法设置配置值: {key}，属性路径不存在")
                return

        # 设置最终值
        last_part = parts[-1]
        attr_path.append(last_part)

        if hasattr(target, last_part):
            setattr(target, last_part, value)
            self._notify_listeners(key, value)
        elif isinstance(target, dict):
            target[last_part] = value
            self._notify_listeners(key, value)
        elif isinstance(target, list) and last_part.isdigit():
            idx = int(last_part)
            if idx < len(target):
                target[idx] = value
                self._notify_listeners(key, value)
            else:
                logger.error(f"设置配置时索引越界: {'.'.join(attr_path)}")
        else:
            logger.error(f"无法设置配置值: {key}，属性不存在")

    def add_change_listener(self, listener: Callable[[str, Any], None]) -> None:
        """添加配置变更监听器"""
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)

    def remove_change_listener(self, listener: Callable[[str, Any], None]) -> None:
        """移除配置变更监听器"""
        if listener in self._change_listeners:
            self._change_listeners.remove(listener)

    def _notify_listeners(self, key: str, value: Any) -> None:
        """通知所有监听器配置已变更"""
        for listener in self._change_listeners:
            try:
                listener(key, value)
            except Exception as e:
                logger.error(f"配置变更监听器出错: {e}")

    def reload(self) -> RuntimeConfig:
        """重新加载配置"""
        # 加载原始字典配置
        new_config_dict = self.loader.load_config(self.config_path)

        # 转换为RuntimeConfig对象
        new_config = RuntimeConfig.from_dict(new_config_dict)

        # 验证配置
        errors = self.validator.validate_config(new_config, "runtime")
        if errors:
            logger.warning(f"重载配置验证发现问题: {errors}")
            # 继续使用新配置，但记录问题

        # 查找并通知变更
        old_dict = self.config.to_dict()
        new_dict = new_config.to_dict()
        changes = self._find_changes(old_dict, new_dict)

        # 更新配置
        self.config = new_config

        # 通知监听器
        for key, value in changes:
            self._notify_listeners(key, value)

        return self.config

    def _find_changes(self, old: Dict[str, Any], new: Dict[str, Any], prefix: str = "") -> List[tuple]:
        """查找配置变更"""
        changes = []

        # 查找所有键
        all_keys = set(old.keys()) | set(new.keys())

        for key in all_keys:
            full_key = f"{prefix}.{key}" if prefix else key

            # 键在新配置中不存在
            if key not in new:
                changes.append((full_key, None))
                continue

            # 键在旧配置中不存在
            if key not in old:
                changes.append((full_key, new[key]))
                continue

            # 值类型不同
            if type(old[key]) != type(new[key]):
                changes.append((full_key, new[key]))
                continue

            # 递归检查嵌套字典
            if isinstance(old[key], dict) and isinstance(new[key], dict):
                nested_changes = self._find_changes(old[key], new[key], full_key)
                changes.extend(nested_changes)
            # 比较其他值
            elif old[key] != new[key]:
                changes.append((full_key, new[key]))

        return changes

    def validate_section(self, section_name: str, schema_name: str = None) -> List[str]:
        """
        验证特定配置段

        Args:
            section_name: 配置段名称
            schema_name: 使用的schema名称，默认使用与section_name相同的schema

        Returns:
            错误消息列表
        """
        if not schema_name:
            schema_name = section_name

        # 获取配置段
        section = self.get_value(section_name)
        if section is None:
            return [f"配置段不存在: {section_name}"]

        # 如果是对象，转换为字典
        if hasattr(section, 'to_dict'):
            section = section.to_dict()

        # 验证
        return self.validator.validate_config(section, schema_name)
