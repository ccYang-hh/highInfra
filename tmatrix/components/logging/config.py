import os
import yaml

DEFAULT_CONFIG = {
    'log_level': 'INFO',
    'log_dir': './logs',
    'console': {
        'enable': True,
        'level': 'INFO',
        'color': True
    },
    'file': {
        'enable': True,
        'level': 'DEBUG',
        'filename': 'tMatrix_application.log',
        'when': 'midnight',    # Rotate at midnight
        'backupCount': 7,      # Keep last 7 days
        'maxBytes': 0,         # 0 means ignore, handled by 'when'
        'formatter': 'json',   # plain 或 json
    }
}


def load_yaml_config(path: str):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def get_env_config():
    """从环境变量读取相关日志配置"""
    env_map = {
        'log_level': os.getenv('TMATRIX_LOG_LEVEL'),
        'log_dir': os.getenv('TMATRIX_LOG_DIR'),
        # 控制台配置
        'console': {
            'enable': os.getenv('TMATRIX_LOG_CONSOLE_ENABLE'),
            'level': os.getenv('TMATRIX_LOG_CONSOLE_LEVEL'),
            'color': os.getenv('TMATRIX_LOG_CONSOLE_COLOR'),
        },
        # 文件配置
        'file': {
            'enable': os.getenv('TMATRIX_LOG_FILE_ENABLE'),
            'level': os.getenv('TMATRIX_LOG_FILE_LEVEL'),
            'filename': os.getenv('TMATRIX_LOG_FILE_FILENAME'),
            'when': os.getenv('TMATRIX_LOG_FILE_WHEN'),
            'backupCount': os.getenv('TMATRIX_LOG_FILE_BACKUPCOUNT'),
            'maxBytes': os.getenv('TMATRIX_LOG_FILE_MAXBYTES'),
            'formatter': os.getenv('TMATRIX_LOG_FILE_FORMATTER'),
        }
    }
    # 转换布尔型与整数
    def parse(v): return v if v is None else (v.lower() in ['true', '1'] if v in ['True', 'False', 'true', 'false', '1', '0'] else int(
        v) if (isinstance(v, str) and v.isdigit()) else v)
    env_map['console'] = {k: parse(v)
                          for k, v in env_map['console'].items() if v is not None}
    env_map['file'] = {k: parse(v)
                       for k, v in env_map['file'].items() if v is not None}
    return {k: v for k, v in env_map.items() if v}


def merge_dict(d1, d2):
    # d2项覆盖d1
    for k, v in d2.items():
        if isinstance(v, dict) and k in d1:
            d1[k] = merge_dict(d1.get(k, {}), v)
        else:
            d1[k] = v
    return d1


def get_final_config(path='logging_config.yaml'):
    file_config = load_yaml_config(path)
    env_config = get_env_config()
    config = merge_dict(DEFAULT_CONFIG.copy(), file_config)
    config = merge_dict(config, env_config)
    return config
