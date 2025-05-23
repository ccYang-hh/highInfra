import os
import logging

from .config import get_final_config
from .handlers import get_console_handler, get_file_handler


def init_logger(name=None):
    config_path = os.getenv('TMATRIX_LOG_CONFIG_PATH')
    if config_path is None:
        config = get_final_config()
    else:
        config = get_final_config(config_path)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.get('log_level', 'INFO')))
    logger.handlers.clear()

    if config['console'].get('enable', True):
        logger.addHandler(get_console_handler(config['console']))

    if config['file'].get('enable', True):
        logger.addHandler(get_file_handler(config['file'], config.get('log_dir', './logs')))

    logger.propagate = False
    return logger
