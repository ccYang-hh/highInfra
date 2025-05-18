import argparse


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="tMatrix System")
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Configuration file path"
    )
    parser.add_argument(
        "--host",
        type=str,
        help="Host to bind the server to"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port to bind the server to"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level"
    )
    return parser.parse_args()
