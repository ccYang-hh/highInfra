from typing import Dict, Any

# 运行时核心配置模式
RUNTIME_SCHEMA = {
    "type": "object",
    "properties": {
        # 运行时基础配置
        "app_name": {"type": "string"},
        "app_version": {"type": "string"},
        "app_description": {"type": "string"},
        "host": {"type": "string"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "log_level": {"type": "string", "enum": ["debug", "info", "warning", "error", "critical"]},
        "service_discovery": {"type": "string", "enum": ["static", "k8s", "etcd"]},
        "cors_origins": {
            "type": "array",
            "items": {"type": "string"}
        },
        "static_path": {"type": "string"},

        # 性能和监控配置
        "worker_threads": {"type": "integer", "minimum": 1},
        "max_batch_size": {"type": "integer", "minimum": 1},
        "request_timeout": {"type": "integer", "minimum": 1},
        "enable_metrics": {"type": "boolean"},
        "metrics_port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "enable_auth": {"type": "boolean"},
        "auth_config": {"type": "object"},

        # 插件配置
        "plugin_registry": {
            "type": "object",
            "properties": {
                "plugins": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "module": {"type": "string"},
                            "path": {"type": "string"},
                            "priority": {"type": "integer"},
                            "config_path": {"type": "string"}
                        }
                    }
                },
                "discovery_paths": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "auto_discovery": {"type": "boolean"}
            }
        },

        # 管道配置
        "pipelines": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "max_parallelism": {"type": "integer", "minimum": 1},
                    "stages": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"}
                            },
                            "required": ["from", "to"]
                        }
                    },
                    "entry_points": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "exit_points": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        }
    },
    "required": ["host", "port"]
}


def get_schema(schema_name: str) -> Dict[str, Any]:
    """获取指定名称的配置模式"""
    schemas = {
        "runtime": RUNTIME_SCHEMA,
    }
    return schemas.get(schema_name, {})
