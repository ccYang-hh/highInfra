import logging
import json


class ColorFormatter(logging.Formatter):
    """控制日志打印色彩的Formatter"""
    color_map = {
        logging.DEBUG: "\x1b[37m",  # 灰色
        logging.INFO: "\x1b[32m",  # 绿色
        logging.WARNING: "\x1b[33m",  # 黄色
        logging.ERROR: "\x1b[31m",  # 红色
        logging.CRITICAL: "\x1b[1;31m"  # 粗体红色
    }
    reset = "\x1b[0m"

    def __init__(self, fmt=None, datefmt='%Y-%m-%d %H:%M:%S', color=True):
        super().__init__(fmt, datefmt)
        self.color = color

    def format(self, record):
        # 保存原始levelname，避免污染其他formatter
        original_levelname = record.levelname
        try:
            if self.color and record.levelno in self.color_map:
                record.levelname = f"{self.color_map[record.levelno]}{record.levelname}{self.reset}"
            return super().format(record)
        finally:
            # 恢复原始levelname，确保不会影响后续处理
            record.levelname = original_levelname


class JsonFormatter(logging.Formatter):
    """控制日志的json转储格式的Formatter"""

    def format(self, record):
        # 获取干净的levelname，不受颜色代码影响
        levelname = logging.getLevelName(record.levelno)

        d = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": levelname,  # 使用干净的levelname
            "module": record.module,
            "line": record.lineno,
            "message": record.getMessage(),
            "logger": record.name
        }
        if record.exc_info:
            d["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)