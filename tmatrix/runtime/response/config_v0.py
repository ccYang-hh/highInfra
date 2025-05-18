# import os
# import yaml
# import json
# import threading
# from pathlib import Path
# from typing import Any, Dict, List, Optional, Callable
#
# from tmatrix.components.logging import init_logger
# logger = init_logger("runtime/plugins")
#
#
# class PluginConfig:
#     """
#     插件配置基类
#     为插件提供独立的配置管理能力
#     """
#
#     def __init__(self,
#                  plugin_name: str,
#                  config_dir: Optional[str] = None,
#                  defaults: Dict[str, Any] = None,
#                  schema: Dict[str, Any] = None):
#         """
#         初始化插件配置
#
#         Args:
#             plugin_name: 插件名称，用于配置文件命名
#             config_dir: 配置目录，默认为 "config/plugins"
#             defaults: 默认配置
#             schema: 配置验证模式
#         """
#         self.plugin_name = plugin_name
#         self.config_dir = config_dir or "config/plugins"
#         self.defaults = defaults or {}
#         self.schema = schema or {}
#
#         # 当前配置
#         self.config = self.defaults.copy()
#
#         # 配置文件路径
#         self.config_path = self._get_config_path()
#
#         # 配置变更监听器
#         self._change_listeners = []
#
#         # 文件监视线程
#         self._watch_thread = None
#         self._watch_stop_event = threading.Event()
#
#         # 加载配置
#         self.load()
#
#     def _get_config_path(self) -> Path:
#         """获取配置文件路径"""
#         # 环境变量中的配置路径优先
#         env_path = os.environ.get(f"PLUGIN_{self.plugin_name.upper()}_CONFIG")
#         if env_path:
#             return Path(env_path)
#
#         # 默认路径: config_dir/plugin_name.yaml
#         return Path(self.config_dir) / f"{self.plugin_name}.yaml"
#
#     def load(self) -> Dict[str, Any]:
#         """
#         加载配置
#
#         Returns:
#             当前配置
#         """
#         # 先使用默认值
#         config = self.defaults.copy()
#
#         # 然后从文件加载
#         if self.config_path.exists():
#             try:
#                 with open(self.config_path, "r") as f:
#                     suffix = self.config_path.suffix.lower()
#                     if suffix in (".yaml", ".yml"):
#                         file_config = yaml.safe_load(f) or {}
#                     elif suffix == ".json":
#                         file_config = json.load(f)
#                     else:
#                         logger.warning(f"Unsupported config file format: {suffix}")
#                         file_config = {}
#
#                 # 合并配置
#                 self._merge_configs(config, file_config)
#                 logger.info(f"Loaded plugin config from {self.config_path}")
#             except Exception as e:
#                 logger.error(f"Error loading plugin config from {self.config_path}: {e}")
#
#         # 从环境变量加载特定于插件的配置
#         prefix = f"PLUGIN_{self.plugin_name.upper()}_"
#         for name, value in os.environ.items():
#             if name.startswith(prefix):
#                 # 提取键
#                 key = name[len(prefix):].lower()
#
#                 # 转换值
#                 if value.lower() == "true":
#                     typed_value = True
#                 elif value.lower() == "false":
#                     typed_value = False
#                 elif value.isdigit():
#                     typed_value = int(value)
#                 elif self._is_float(value):
#                     typed_value = float(value)
#                 else:
#                     typed_value = value
#
#                 # 设置配置
#                 self._set_nested_key(config, key, typed_value, separator='_')
#
#         # 更新当前配置
#         old_config = self.config
#         self.config = config
#
#         # 通知变更
#         self._notify_changes(old_config, config)
#
#         return config
#
#     def _is_float(self, value: str) -> bool:
#         """检查字符串是否可转换为浮点数"""
#         try:
#             float(value)
#             return True
#         except ValueError:
#             return False
#
#     def save(self, config: Optional[Dict[str, Any]] = None) -> None:
#         """
#         保存配置到文件
#
#         Args:
#             config: 要保存的配置，默认为当前配置
#         """
#         config = config or self.config
#
#         try:
#             # 确保目录存在
#             os.makedirs(self.config_path.parent, exist_ok=True)
#
#             with open(self.config_path, "w") as f:
#                 suffix = self.config_path.suffix.lower()
#                 if suffix in (".yaml", ".yml"):
#                     yaml.dump(config, f, default_flow_style=False)
#                 elif suffix == ".json":
#                     json.dump(config, f, indent=2)
#                 else:
#                     logger.warning(f"Unsupported config file format: {suffix}")
#
#             logger.info(f"Saved plugin config to {self.config_path}")
#         except Exception as e:
#             logger.error(f"Error saving plugin config to {self.config_path}: {e}")
#
#     def get(self, key: str, default: Any = None) -> Any:
#         """
#         获取配置值
#
#         Args:
#             key: 配置键，支持用点号分隔的嵌套路径
#             default: 默认值
#
#         Returns:
#             配置值或默认值
#         """
#         parts = key.split('.')
#         value = self.config
#
#         for part in parts:
#             if isinstance(value, dict) and part in value:
#                 value = value[part]
#             else:
#                 return default
#
#         return value
#
#     def set(self, key: str, value: Any, save: bool = True) -> None:
#         """
#         设置配置值
#
#         Args:
#             key: 配置键，支持用点号分隔的嵌套路径
#             value: 新值
#             save: 是否保存到文件
#         """
#         # 保存旧配置用于比较
#         old_config = self.config.copy()
#
#         # 设置新值
#         parts = key.split('.')
#         target = self.config
#
#         for part in parts[:-1]:
#             if part not in target:
#                 target[part] = {}
#             target = target[part]
#
#         # 设置值
#         target[parts[-1]] = value
#
#         # 保存到文件
#         if save:
#             self.save()
#
#         # 通知监听器
#         self._notify_change(key, value)
#
#     def _set_nested_key(self,
#                         config: Dict[str, Any],
#                         key: str,
#                         value: Any,
#                         separator: str = '.') -> None:
#         """设置嵌套键的值"""
#         parts = key.split(separator)
#         target = config
#
#         for part in parts[:-1]:
#             if part not in target:
#                 target[part] = {}
#             target = target[part]
#
#         target[parts[-1]] = value
#
#     def _merge_configs(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
#         """递归合并配置"""
#         for key, value in override.items():
#             if key in base and isinstance(base[key], dict) and isinstance(value, dict):
#                 self._merge_configs(base[key], value)
#             else:
#                 base[key] = value
#
#         return base
#
#     def add_change_listener(self, listener: Callable[[str, Any], None]) -> None:
#         """添加配置变更监听器"""
#         if listener not in self._change_listeners:
#             self._change_listeners.append(listener)
#
#     def remove_change_listener(self, listener: Callable[[str, Any], None]) -> None:
#         """移除配置变更监听器"""
#         if listener in self._change_listeners:
#             self._change_listeners.remove(listener)
#
#     def _notify_change(self, key: str, value: Any) -> None:
#         """通知单个配置变更"""
#         for listener in self._change_listeners:
#             try:
#                 listener(key, value)
#             except Exception as e:
#                 logger.error(f"Error in plugin config change listener: {e}")
#
#     def _notify_changes(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
#         """通知所有配置变更"""
#         # 查找所有变更
#         changes = self._find_changes(old_config, new_config)
#
#         # 通知每个变更
#         for key, value in changes:
#             self._notify_change(key, value)
#
#     def _find_changes(self, old: Dict[str, Any], new: Dict[str, Any], prefix: str = "") -> List[tuple]:
#         """查找配置变更"""
#         changes = []
#
#         # 查找所有键
#         all_keys = set(old.keys()) | set(new.keys())
#
#         for key in all_keys:
#             full_key = f"{prefix}.{key}" if prefix else key
#
#             # 键在新配置中不存在
#             if key not in new:
#                 changes.append((full_key, None))
#                 continue
#
#             # 键在旧配置中不存在
#             if key not in old:
#                 changes.append((full_key, new[key]))
#                 continue
#
#             # 值类型不同
#             if type(old[key]) != type(new[key]):
#                 changes.append((full_key, new[key]))
#                 continue
#
#             # 递归检查嵌套字典
#             if isinstance(old[key], dict) and isinstance(new[key], dict):
#                 nested_changes = self._find_changes(old[key], new[key], full_key)
#                 changes.extend(nested_changes)
#             # 比较其他值
#             elif old[key] != new[key]:
#                 changes.append((full_key, new[key]))
#
#         return changes
#
#     def start_watching(self, interval: int = 5) -> None:
#         """
#         开始监视配置文件变化
#
#         Args:
#             interval: 检查间隔(秒)
#         """
#         if self._watch_thread and self._watch_thread.is_alive():
#             return
#
#         self._watch_stop_event.clear()
#         self._watch_thread = threading.Thread(
#             target=self._watch_config_file,
#             args=(interval,),
#             daemon=True
#         )
#         self._watch_thread.start()
#         logger.info(f"Started watching config file {self.config_path}")
#
#     def stop_watching(self) -> None:
#         """停止监视配置文件变化"""
#         if self._watch_thread and self._watch_thread.is_alive():
#             self._watch_stop_event.set()
#             self._watch_thread.join(timeout=1.0)
#             logger.info("Stopped watching config file")
#
#     def _watch_config_file(self, interval: int) -> None:
#         """配置文件监视线程"""
#         last_mtime = 0
#
#         while not self._watch_stop_event.is_set():
#             try:
#                 # 检查文件是否存在
#                 if self.config_path.exists():
#                     mtime = os.path.getmtime(self.config_path)
#
#                     # 检查是否已修改
#                     if mtime > last_mtime:
#                         logger.info(f"Config file {self.config_path} changed, reloading")
#                         self.load()
#                         last_mtime = mtime
#             except Exception as e:
#                 logger.error(f"Error watching config file: {e}")
#
#             # 等待下一次检查
#             self._watch_stop_event.wait(interval)
#