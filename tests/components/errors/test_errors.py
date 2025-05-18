from tmatrix.components.errors import *


def test_error_framework():
    print("=== 开始测试错误处理框架 ===")

    # 1. 定义错误组
    @error_group("USER")
    class UserErrors:
        NOT_FOUND = ErrorDef(code="001", message="用户不存在: {user_id}", status_code=404)
        UNAUTHORIZED = ErrorDef(code="002", message="未授权访问", status_code=401)

    print("1. 错误组定义完成")

    # 2. 测试抛出错误
    try:
        Errors.raise_error(UserErrors.NOT_FOUND, user_id="123")
    except AppError as e:
        print(f"2. 成功捕获错误: {e.info.code} - {e.message}")

    # 3. 测试JSON处理器
    json_handler = create_handler('json')
    error = Errors.error(UserErrors.NOT_FOUND, user_id="456")
    json_result = json_handler.to_json(error)
    print(f"3. JSON处理器输出: {json_result}")

    # 4. 测试Web处理器
    web_handler = create_handler('web')
    status, body, headers = web_handler.to_response(error)
    print(f"4. Web处理器输出: 状态码={status}, 响应头={headers}")
    print(f"   响应体: {body}")

    print("=== 测试完成 ===")


if __name__ == "__main__":
    test_error_framework()
