import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler

from .formatter import ColorFormatter, JsonFormatter


def get_console_handler(config):
    fmt = '[%(asctime)s] %(levelname)s %(message)s (%(filename)s:%(lineno)d)'
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, config['level'], 'INFO'))
    handler.setFormatter(ColorFormatter(fmt=fmt, color=config.get('color', True)))
    return handler


def get_file_handler(config, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, config['filename'])
    level = getattr(logging, config.get('level', 'DEBUG'))

    if config.get('when'):  # 时间轮转
        handler = TimedRotatingFileHandler(
            log_file, when=config['when'],
            backupCount=int(config.get('backupCount', 7)), encoding='utf-8')
    elif config.get('maxBytes') and int(config['maxBytes']) > 0:  # 按大小轮转
        handler = RotatingFileHandler(
            log_file, maxBytes=int(config.get('maxBytes')),
            backupCount=int(config.get('backupCount', 5)), encoding='utf-8')
    else:
        handler = logging.FileHandler(log_file, encoding='utf-8')
    handler.setLevel(level)

    # 根据 config 选择格式化器
    file_fmt_type = config.get('formatter', 'plain')  # 支持 plain/json
    if file_fmt_type == 'json':
        handler.setFormatter(JsonFormatter())
    else:
        fmt = '[%(asctime)s] %(levelname)s %(message)s (%(filename)s:%(lineno)d)'
        handler.setFormatter(logging.Formatter(fmt))

    return handler
