import signal
import uvicorn

from tmatrix.components.logging import init_logger
from .core import RuntimeCore
from .args import parse_args

logger = init_logger("runtime/app")


def main():
    args = parse_args()

    # 创建运行时
    runtime = RuntimeCore(config_path=args.config)
    app, config = runtime.app, runtime.config

    # 设置信号处理
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        # 此处可设置退出标志等操作，实际关闭由FastAPI处理

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动服务器
    logger.info(f"Starting server on {config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
