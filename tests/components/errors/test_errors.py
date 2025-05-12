from tmatrix.components.errors.registry import ErrorRegistry
from tmatrix.components.errors.types import ErrorContext
from tmatrix.components.errors.error_handler import ErrorHandler
from tmatrix.components.errors.adapters import JsonOutputAdapter, WebOutputAdapter

if __name__ == '__main__':
    registry = ErrorRegistry()
    module = registry.get_module('matrix')
    error_def = module.define("1000", "这是一个错误测试信息")
    err = module.create_error("1000", ErrorContext(request_id="99999"))
    print(err)

    handler = ErrorHandler()
    handler.add_adapter(JsonOutputAdapter())
    results = handler.handle(err)
    print(results)
