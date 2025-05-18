from ..types import ErrorCategory
from ..registry import error_group, ErrorDef


SCHEMA_NOT_DEFINITION = "110000"
SCHEMA_VALIDATE_ERROR = "110001"


@error_group("RUNTIME/CONFIG")
class ConfigErrors:
    """认证相关错误定义"""

    # 更清晰的错误定义，不需要空方法
    SCHEMA_NOT_DEFINITION = ErrorDef(
        code="110000",
        message="SCHEMA未定义",
        status_code=401,
        category=ErrorCategory.SECURITY
    )

    SCHEMA_VALIDATE_ERROR = ErrorDef(
        code="110001",
        message="Runtime配置校验失败, 配置文件路径: {path}",
        status_code=403,
        category=ErrorCategory.SECURITY
    )
