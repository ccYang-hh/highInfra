import time
from tmatrix.common.logging import init_logger
from tmatrix.runtime.config import get_config_manager

logger = init_logger("tests/runtime/config")


if __name__ == "__main__":
    config_manager = get_config_manager("/home/run/tmatrix/config.json")
    try:
        while True:
            print("等待文件变更...")
            time.sleep(5)
    except KeyboardInterrupt:
        config_manager.close()

