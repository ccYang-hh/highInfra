from typing import Dict, Optional

from .types import ErrorCategory, ErrorContext, ErrorInfo


class ErrorDef:
    """错误定义帮助类，用于创建ErrorInfo"""
    def __init__(self,
                 code: str,
                 message: str,
                 status_code: int = 500,
                 category: ErrorCategory = ErrorCategory.SYSTEM):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.category = category

    def __set_name__(self, owner, name):
        # 当作为类变量使用时，自动注册到错误组
        if hasattr(owner, '_error_module'):
            module = owner._error_module
            registry = ErrorRegistry()
            error_info = registry.register(
                module, self.code, self.message,
                self.status_code, self.category
            )
            # 替换自身为ErrorInfo实例
            setattr(owner, name, error_info)


def error_group(module_name: str):
    """错误组装饰器，简化错误定义"""
    def decorator(cls):
        cls._error_module = module_name.upper()
        # 处理已存在的类变量
        for name, attr in list(cls.__dict__.items()):
            if isinstance(attr, ErrorDef):
                # 这会触发ErrorDef.__set_name__
                setattr(cls, name, attr)
        return cls
    return decorator


class ErrorRegistry:
    """错误注册表单例"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ErrorRegistry, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._errors: Dict[str, ErrorInfo] = {}
        # 注册系统默认错误
        self.register("SYSTEM", "UNKNOWN", "系统未知错误", 500)

    def register(self,
                 module: str,
                 code: str,
                 message: str,
                 status_code: int = 500,
                 category: ErrorCategory = ErrorCategory.SYSTEM) -> ErrorInfo:
        """注册新错误"""
        full_code = f"{module.upper()}/{code}"

        if full_code in self._errors:
            raise ValueError(f"错误码 {full_code} 已注册")

        info = ErrorInfo(
            code=full_code,
            message=message,
            status_code=status_code,
            category=category
        )

        self._errors[full_code] = info
        return info

    def get(self, code: str) -> Optional[ErrorInfo]:
        """获取错误信息"""
        # 尝试直接获取
        if code in self._errors:
            return self._errors[code]

        # 尝试添加模块前缀后获取
        if "/" not in code:
            # 查找所有可能匹配的错误
            for full_code in self._errors:
                if full_code.endswith("/" + code):
                    return self._errors[full_code]

        return None

    def get_or_unknown(self, code: str) -> ErrorInfo:
        """获取错误信息或返回未知错误"""
        return self.get(code) or self._errors["SYSTEM/UNKNOWN"]
