import sys
import signal
import uvicorn
from fastapi import FastAPI, Request, BackgroundTasks

from tmatrix.components.logging import init_logger
from .core import RuntimeCore
from .args_parser import parse_args

logger = init_logger("runtime/app")


async def startup(app: FastAPI, runtime: RuntimeCore):
    """启动函数"""
    try:
        # 初始化运行时
        await runtime.startup()
    except Exception as e:
        logger.error(f"Error during startup: {e}", exc_info=True)
        # 强制退出
        sys.exit(1)


async def shutdown(app: FastAPI, runtime: RuntimeCore):
    """关闭函数"""
    try:
        await runtime.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)


def setup_app(runtime: RuntimeCore) -> FastAPI:
    """设置FastAPI应用"""
    app = runtime.app

    # 注册启动和关闭事件
    @app.on_event("startup")
    async def on_startup():
        await startup(app, runtime)

    @app.on_event("shutdown")
    async def on_shutdown():
        await shutdown(app, runtime)

    # 注册核心API路由
    @app.post("/v1/completions")
    async def completions(request: Request, background_tasks: BackgroundTasks):
        return await runtime.get_api_handler("main")(request, background_tasks)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request, background_tasks: BackgroundTasks):
        return await runtime.get_api_handler("main")(request, background_tasks)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request, background_tasks: BackgroundTasks):
        # 可以使用专门的embeddings管道
        if "embeddings" in runtime.pipelines:
            return await runtime.get_api_handler("embeddings")(request, background_tasks)
        # 回退到主管道
        return await runtime.get_api_handler("main")(request, background_tasks)

    return app


def main():
    args = parse_args()

    # 创建运行时
    runtime = RuntimeCore(config_path=args.config)

    # 设置应用
    app = setup_app(runtime)

    # 设置信号处理
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        # 我们只是设置了退出标志，实际关闭由FastAPI处理

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 从配置或命令行获取主机和端口
    host = args.host or runtime.config.get("host", "0.0.0.0")
    port = args.port or runtime.config.get("port", 8000)

    # 启动服务器
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
