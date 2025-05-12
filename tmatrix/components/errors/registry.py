from typing import Dict, Optional

from .types import ErrorCategory, ErrorContext, ErrorDefinition
from .base import BaseError


class ErrorRegistry:
    """错误注册中心，管理所有模块的错误定义"""

    _instance = None  # 单例模式实现

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ErrorRegistry, cls).__new__(cls)
            cls._instance._modules = {}         # 模块名称 -> 错误定义字典
            cls._instance._error_codes = set()  # 已注册的错误码集合，用于检测重复
            cls._instance._definition_cache = {}  # 错误定义缓存\
        return cls._instance

    def get_module(self, module_name: str) -> 'ErrorModule':
        """获取模块注册器，如果不存在则创建"""
        module_name = module_name.upper()  # 规范化模块名称

        if module_name not in self._modules:
            self._modules[module_name] = {}

        return ErrorModule(self, module_name)

    def register_error(self, module_name: str, code: str, error_def: ErrorDefinition) -> None:
        """注册错误定义"""
        module_name = module_name.upper()

        # 检查错误码是否已存在
        if code in self._error_codes:
            raise ValueError(f"错误码 {code} 已被注册")

        # 注册错误定义
        if module_name not in self._modules:
            self._modules[module_name] = {}

        self._modules[module_name][code] = error_def
        self._error_codes.add(code)

        # 添加到缓存
        self._definition_cache[code] = error_def

    def get_error_def(self, code: str) -> Optional[ErrorDefinition]:
        """根据错误码获取错误定义"""
        # 先查缓存
        if code in self._definition_cache:
            return self._definition_cache[code]

        # 遍历所有模块查找错误定义
        for module_errors in self._modules.values():
            if code in module_errors:
                self._definition_cache[code] = module_errors[code]
                return module_errors[code]
        return None

    def list_all_errors(self) -> Dict[str, Dict[str, ErrorDefinition]]:
        """列出所有注册的错误"""
        return self._modules.copy()

    def clear(self) -> None:
        """清除所有注册的错误（主要用于测试）"""
        self._modules.clear()
        self._error_codes.clear()
        self._definition_cache.clear()


class ErrorModule:
    """模块错误注册器，用于管理特定模块的错误"""

    def __init__(self, registry: ErrorRegistry, module_name: str):
        self.registry = registry
        self.module_name = module_name.upper()  # 规范化模块名称

    def define(
            self,
            code_suffix: str,
            message: str,
            status_code: int = 500,
            category: ErrorCategory = ErrorCategory.SYSTEM
    ) -> ErrorDefinition:
        """定义并注册错误"""
        # 构建完整错误码
        full_code = f"{self.module_name}/{code_suffix}"

        # 创建错误定义
        error_def = ErrorDefinition(
            code=full_code,
            message=message,
            status_code=status_code,
            category=category
        )

        # 在注册中心注册错误
        self.registry.register_error(self.module_name, full_code, error_def)

        return error_def

    def create_error(
            self,
            code_suffix: str,
            context: Optional[ErrorContext] = None,
            **kwargs
    ) -> BaseError:
        """创建特定错误的实例"""
        full_code = f"{self.module_name}/{code_suffix}"
        error_def = self.registry.get_error_def(full_code)

        if not error_def:
            raise ValueError(f"未找到错误定义 {full_code}")

        return BaseError(error_def, context, **kwargs)

    def raise_error(
            self,
            code_suffix: str,
            context: Optional[ErrorContext] = None,
            **kwargs
    ) -> None:
        """创建并抛出错误"""
        raise self.create_error(code_suffix, context, **kwargs)