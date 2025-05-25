"""
独立运行入口
用于调试和独立部署监控系统
"""
import asyncio
import argparse
import sys
from pathlib import Path

from .core.config import MonitorConfig, get_default_config
from .service import MonitorService

from tmatrix.common.logging import init_logger
logger = init_logger("monitor/service")


async def main_async(args):
    """异步主函数"""
    # 加载配置
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            logger.error(f"Config file not found: {args.config}")
            sys.exit(1)

        try:
            config = MonitorConfig.from_file(args.config)
            logger.info(f"Loaded config from {args.config}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            sys.exit(1)
    else:
        config = get_default_config()
        logger.info("Using default configuration. Use --config to specify a config file.")

    # 验证配置
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Invalid configuration: {e}")
        sys.exit(1)

    logger.info("Starting monitor service in standalone mode")

    # 创建并启动服务
    service = MonitorService(config)

    try:
        await service.initialize()
        await service.start()

        logger.info("Monitor service is running. Press Ctrl+C to stop.")
        await service.wait_until_stop()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Service error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await service.stop()
        logger.info("Service stopped")


def main():
    """主入口函数"""
    parser = argparse.ArgumentParser(description='Metrics Monitor Service')
    parser.add_argument(
        '--config', '-c',
        type=str,
        help='Configuration file path (JSON format)'
    )

    args = parser.parse_args()

    # 运行服务
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nReceived keyboard interrupt, exiting")
    except Exception as e:
        print(f"Unhandled exception: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
