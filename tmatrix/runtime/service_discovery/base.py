import abc
import time
import threading
from typing import List

from .types import Endpoint


class ServiceDiscovery(abc.ABC):
    """服务发现基类"""
    @abc.abstractmethod
    def get_endpoints(self) -> List[Endpoint]:
        """获取所有可用端点"""
        pass

    def get_endpoints_by_model(self, model_name: str) -> List[Endpoint]:
        """获取指定模型的所有端点"""
        return [ep for ep in self.get_endpoints() if ep.model_name == model_name]

    @abc.abstractmethod
    def register_endpoint(self, endpoint: Endpoint, *args, **kwargs):
        pass

    @abc.abstractmethod
    def remove_endpoint(self, endpoint_id: str, *args, **kwargs):
        pass

    @abc.abstractmethod
    def health(self) -> bool:
        pass

    def close(self):
        pass


class CachedServiceDiscovery(ServiceDiscovery):
    """基于本地缓存的服务发现基类"""
    def __init__(self, cache_ttl: float = 1.0):
        self._endpoints: List[Endpoint] = []
        self._last_update: float = 0
        self._cache_ttl = cache_ttl
        self._lock = threading.RLock()
        self._updating = False

    def get_endpoints(self) -> List[Endpoint]:
        """使用缓存获取端点，避免频繁查询"""
        now = time.time()

        # 快速路径：缓存有效，直接返回
        if now - self._last_update < self._cache_ttl:
            return self._endpoints

        with self._lock:
            # 双重检查，避免多线程重复刷新
            if now - self._last_update < self._cache_ttl or self._updating:
                return self._endpoints

            self._updating = True
            try:
                # 子类实现的实际刷新方法
                endpoints = self._fetch_endpoints()
                self._endpoints = endpoints
                self._last_update = time.time()
            finally:
                self._updating = False

            return self._endpoints

    @abc.abstractmethod
    def _fetch_endpoints(self) -> List[Endpoint]:
        """获取最新端点信息，子类实现"""
        pass

    def register_endpoint(self, endpoint: Endpoint, *args, **kwargs):
        """注册端点（手动添加），并立即刷新缓存"""
        with self._lock:
            self._endpoints = [ep for ep in self._endpoints if ep.endpoint_id != endpoint.endpoint_id]
            self._endpoints.append(endpoint)
            self._last_update = time.time()

    def remove_endpoint(self, endpoint_id: str, *args, **kwargs):
        """移除端点，并立即刷新缓存"""
        with self._lock:
            self._endpoints = [ep for ep in self._endpoints if ep.endpoint_id != endpoint_id]
            self._last_update = time.time()

    def health(self) -> bool:
        return len(self._endpoints) > 0