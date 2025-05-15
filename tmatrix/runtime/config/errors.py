from tmatrix.components.errors import register_errors
from tmatrix.components.errors import Registry as ErrorsRegistry

SCHEMA_NOT_DEFINITION = "110000"
SCHEMA_VALIDATE_ERROR = "110001"

MODULE_ERRORS = [
    (SCHEMA_NOT_DEFINITION, "SCHEMA未定义"),
    (SCHEMA_VALIDATE_ERROR, "Runtime配置校验失败, 配置文件路径: {path}")
]

register_errors("runtime/config", MODULE_ERRORS)


def raise_error(code: str, **kwargs):
    ErrorsRegistry.raise_error_without_context("runtime/config".upper(), **kwargs)
