from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Dict, Any

from tmatrix.components.logging import init_logger
from tmatrix.runtime.plugins import PluginConfig

logger = init_logger("runtime/plugins")


class RouterStrategy(Enum):
    RoundRobin = auto()
    Session = auto()
    KVCacheAware = auto()


@dataclass
class RouterConfig(PluginConfig):
    name: str = "router"
    version: str = "1.0.0"
    description: str = "vLLM Router For PD"

    default_strategy: RouterStrategy = RouterStrategy.RoundRobin
    support_strategies: List[RouterStrategy] = field(default_factory=lambda: [
        RouterStrategy.RoundRobin,
        RouterStrategy.Session,
        RouterStrategy.KVCacheAware
    ])

    # Session路由配置
    session_key: str = "x-session-id"

    # KVCache路由配置
    lmcache_controller_port: int = 8000
    kv_cache_enabled: bool = False
