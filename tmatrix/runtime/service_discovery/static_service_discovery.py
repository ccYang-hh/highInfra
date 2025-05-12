import time
import uuid
import json
import threading
from typing import List

from .types import Endpoint
from .base import CachedServiceDiscovery


class StaticServiceDiscovery(CachedServiceDiscovery):
    """静态服务发现，用于固定端点"""

    def __init__(self, endpoints: List[Endpoint]):
        super().__init__(float('inf'))  # 永不过期
        self._static_endpoints = endpoints

    def _fetch_endpoints(self) -> List[Endpoint]:
        return self._static_endpoints
