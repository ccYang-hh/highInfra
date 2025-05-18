import enum


class PluginManagedEvent(enum.Enum):
    """插件管理器事件枚举"""
    MANAGER_INITIALIZED = "manager_initialized"  # 管理器初始化完成
    MANAGER_SHUTDOWN = "manager_shutdown"        # 管理器关闭

    PLUGIN_REGISTERED = "plugin_registered"      # 插件注册
    PLUGIN_UNREGISTERED = "plugin_unregistered"  # 插件注销
    PLUGIN_LOADED = "plugin_loaded"              # 插件加载
    PLUGIN_UNLOADED = "plugin_unloaded"          # 插件卸载
    PLUGIN_RELOADED = "plugin_reloaded"          # 插件重新加载
    PLUGIN_CONFIG_UPDATED = "plugin_config_updated"  # 插件配置更新

    REGISTRY_CONFIG_UPDATED = "registry_config_updated"  # 注册表配置更新

    # 用于系统内部状态汇报
    PLUGIN_LOAD_ERROR = "plugin_load_error"              # 插件加载错误
    PLUGIN_DEPENDENCY_ERROR = "plugin_dependency_error"  # 插件依赖错误

