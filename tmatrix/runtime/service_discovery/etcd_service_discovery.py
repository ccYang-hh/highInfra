import json
import threading
import time
import uuid
from typing import List, Optional, Dict

import etcd3
import etcd3.events

from tmatrix.common.logging import init_logger
from .base import CachedServiceDiscovery
from .types import TransportType, Endpoint

logger = init_logger(__name__)


def get_etcd_service_discovery(etcd_host: str = "localhost", etcd_port: int = 2379,
                               prefix: str = "tmatrix.endpoints", cache_ttl: float = 1.0) -> "EtcdServiceDiscovery":
    return EtcdServiceDiscoverySingleton.get_instance(etcd_host, etcd_port, prefix, cache_ttl)


class EtcdServiceDiscoverySingleton:
    _instance: Optional["EtcdServiceDiscovery"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, etcd_host: str = "localhost", etcd_port: int = 2379,
                     prefix: str = "tmatrix.endpoints", cache_ttl: float = 1.0) -> "EtcdServiceDiscovery":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = EtcdServiceDiscovery(etcd_host, etcd_port, prefix, cache_ttl)
        return cls._instance


class EtcdServiceDiscovery(CachedServiceDiscovery):
    """基于ETCD的服务发现, 支持注册/下线、事件实时感知与缓存"""

    def __init__(self, etcd_host: str = "localhost", etcd_port: int = 2379,
                 prefix: str = "tmatrix.endpoints", cache_ttl: float = 1.0):
        super().__init__(cache_ttl)
        self.client = etcd3.client(host=etcd_host, port=etcd_port)
        self.prefix = prefix.rstrip("/")
        self._watch_thread = threading.Thread(target=self._watch_changes, daemon=True)
        self._running = True
        self._watch_thread.start()
        self._leases: Dict[str, "etcd3.Lease"] = {}

    def _fetch_endpoints(self) -> List[Endpoint]:
        """从etcd拉取全部endpoint实例"""
        try:
            endpoints = []
            for value, meta in self.client.get_prefix(f"{self.prefix}/"):
                try:
                    data = json.loads(value.decode())
                    ep = Endpoint(
                        endpoint_id=data.get("endpoint_id", str(uuid.uuid4())),
                        endpoint_type=data.get("endpoint_type", []),
                        address=data.get("address", ""),
                        model_name=data.get("model_name", "default"),
                        transport_type=TransportType(data.get("transport_type", "http")),
                        added_timestamp=data.get("added_timestamp", time.time()),
                        priority=int(data.get("priority", 0)),
                        metadata=data.get("metadata", {})
                    )
                    endpoints.append(ep)
                except Exception as e:
                    logger.error(f"Decode endpoint error: {e}")
            return endpoints
        except Exception as e:
            logger.error(f"Error fetch etcd endpoints: {e}")
            return self._endpoints

    def _watch_changes(self):
        """监视etcd变化"""
        while self._running:
            try:
                events_iter, cancel = self.client.watch_prefix(f"{self.prefix}/")
                for event in events_iter:
                    if isinstance(event, etcd3.events.PutEvent):
                        # 端点增改
                        print('PUT:', event.key)
                        self._last_update = 0
                    elif isinstance(event, etcd3.events.DeleteEvent):
                        # 端点下线
                        print('DELETE:', event.key)
                        self._last_update = 0
                # 防止漏掉关闭
                if not self._running:
                    cancel()
            except Exception as e:
                logger.error(f"Etcd watch error: {e}")
                time.sleep(1)

    def register_endpoint(self, endpoint: Endpoint, ttl: Optional[int] = None, *args, **kwargs):
        """注册一个端点到 etcd，并可选使用 lease 自动过期"""
        key = f"{self.prefix}/{endpoint.endpoint_id}"
        endpoint_types = [endpoint_type.value for endpoint_type in endpoint.endpoint_type]
        data = {
            "endpoint_id": endpoint.endpoint_id,
            "endpoint_type": endpoint_types,
            "address": endpoint.address,
            "model_name": endpoint.model_name,
            "transport_type": endpoint.transport_type.value,
            "added_timestamp": time.time(),
            "priority": endpoint.priority,
            "metadata": endpoint.metadata
        }
        lease = None
        if ttl:
            lease = self.client.lease(ttl)
            self.client.put(key, json.dumps(data), lease=lease)
            self._leases[endpoint.endpoint_id] = lease
        else:
            self.client.put(key, json.dumps(data))

    def remove_endpoint(self, endpoint_id: str, *args, **kwargs):
        """根据 id+模型 精确下线一个 endpoint，同时撤销 lease"""
        key = f"{self.prefix}/{endpoint_id}"
        self.client.delete(key)
        lease = self._leases.pop(endpoint_id, None)
        if lease:
            try:
                lease.revoke()
            except Exception as e:
                logger.error(f"remove_endpoint error: {e}")
                return

    def close(self):
        self._running = False
        if self._watch_thread.is_alive():
            self._watch_thread.join(timeout=2)

    # TODO, Support Checking Inference Service Status
    def health(self) -> bool:
        return self._running
