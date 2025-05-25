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
        "max_batch_size": {"type": "integer", "minimum": 1},
        "request_timeout": {"type": "integer", "minimum": 1},
        "enable_monitor": {"type": "boolean"},
        "monitor_config": {"type": "string"},
        "enable_auth": {"type": "boolean"},
        "auth_config": {"type": "object"},

        "etcd_config": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
            }
        },

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
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pipeline_name": {"type": "string"},
                    "plugins": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "routes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "method": {"type": "string"},
                            },
                            "required": ["path", "method"],
                        }
                    },
                }
            },
            "required": ["plugin_name", "plugins", "routes"]
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
