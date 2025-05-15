from typing import List, Tuple

from tmatrix.components.errors.registry import ErrorRegistry
from tmatrix.components.errors.types import ErrorContext, ErrorCategory
from tmatrix.components.errors.error_handler import ErrorHandler
from tmatrix.components.errors.adapters import JsonOutputAdapter


Registry = ErrorRegistry()


def register_errors(module_name: str, module_errors: List[Tuple]) -> None:
    module = Registry.get_module(module_name)

    define_fn = module.define
    for params in module_errors:
        define_fn(*params)


__all__ = [
    'Registry',
    'ErrorRegistry',
    'ErrorHandler',
    'ErrorContext',
    'ErrorCategory',
    'JsonOutputAdapter',

    'register_errors',
]
