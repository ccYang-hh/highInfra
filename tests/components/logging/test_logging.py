from tmatrix.common.logging.logger import init_logger

if __name__ == '__main__':
    logger = init_logger("normal")

    logger.debug("调试日志")
    logger.info("常规信息")
    logger.warning("警告！")
    logger.error("出错了")
    logger.critical("严重错误！")