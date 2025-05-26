import json
import threading
import time
import uuid
from typing import List, Optional, Dict

import etcd3
import etcd3.events

from tmatrix.common.logging import init_logger
from .base import CachedServiceDiscovery
from .types import TransportType, Endpoint, EndpointStatus, EndpointType, KVEventsConfig, KVRole

logger = init_logger(__name__)


def get_etcd_service_discovery(host="localhost", port="2379", prefix="/tmatrix/endpoints"):
    return EtcdServiceDiscoverySingleton.get_instance(host, port, prefix)


class EtcdServiceDiscoverySingleton:
    """ETCD服务发现单例"""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, host, port, prefix):
        if cls._instance is None:
            with cls._lock:
                cls._instance = EtcdServiceDiscovery(
                    etcd_host=host,
                    etcd_port=port,
                    prefix=prefix
                )
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

        # 初始化时，先强制刷新一次endpoints
        endpoints = self._fetch_endpoints()
        self._endpoints = endpoints
        self._last_update = time.time()

    def _fetch_endpoints(self) -> List[Endpoint]:
        """从etcd拉取全部endpoint实例"""
        try:
            endpoints = []
            for value, meta in self.client.get_prefix(f"{self.prefix}/"):
                try:
                    data = json.loads(value.decode())

                    # 处理 endpoint_type 列表
                    endpoint_types = []
                    for ep_type in data.get("endpoint_type", []):
                        try:
                            endpoint_types.append(EndpointType(ep_type))
                        except ValueError:
                            logger.warning(f"Unknown endpoint type: {ep_type}")

                    # 处理 kv_event_config
                    event_config_data = data.get("kv_event_config", {})
                    if event_config_data:
                        kv_event_config = KVEventsConfig(
                            publisher=event_config_data.get("publisher", "zmq"),
                            endpoint=event_config_data.get("endpoint", "tcp://*:5557"),
                            replay_endpoint=event_config_data.get("replay_endpoint")
                        )
                    else:
                        kv_event_config = KVEventsConfig()

                    ep = Endpoint(
                        endpoint_id=data.get("endpoint_id", str(uuid.uuid4())),
                        endpoint_type=endpoint_types,
                        address=data.get("address", ""),
                        model_name=data.get("model_name", "default"),
                        instance_name=data.get("instance_name", "unknown"),
                        transport_type=TransportType(data.get("transport_type", "http")),
                        status=EndpointStatus(data.get("status", "healthy")),
                        added_timestamp=data.get("added_timestamp", time.time()),
                        priority=int(data.get("priority", 0)),
                        kv_role=KVRole(data.get("kv_role", "both")),
                        kv_event_config=kv_event_config,
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

        # 序列化 event_config
        kv_event_config_data = {
            "publisher": endpoint.kv_event_config.publisher,
            "endpoint": endpoint.kv_event_config.endpoint,
            "replay_endpoint": endpoint.kv_event_config.replay_endpoint,
        }

        data = {
            "endpoint_id": endpoint.endpoint_id,
            "endpoint_type": endpoint_types,
            "address": endpoint.address,
            "model_name": endpoint.model_name,
            "instance_name": endpoint.instance_name,
            "transport_type": endpoint.transport_type.value,
            "status": EndpointStatus.HEALTHY.value,   # TODO, 在注册endpoint前, 应该校验服务的健康状态
            "added_timestamp": time.time(),
            "priority": endpoint.priority,
            "kv_role": endpoint.kv_role.value,  # 添加 KV 角色
            "kv_event_config": kv_event_config_data,  # 添加事件配置
            "metadata": endpoint.metadata
        }
        lease = None
        if ttl:
            lease = self.client.lease(ttl)
            self.client.put(key, json.dumps(data), lease=lease)
            self._leases[endpoint.endpoint_id] = lease
        else:
            self.client.put(key, json.dumps(data))

    def remove_endpoint(self, endpoint_id: str,  *args, **kwargs):
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

    def remove_all_endpoints(self):
        """删除所有Endpoint"""
        try:
            self.client.delete_prefix(self.prefix)
        except Exception as e:
            logger.error(f"remove_all_endpoints error: {e}")
            return

    def close(self):
        self._running = False
        if self._watch_thread.is_alive():
            self._watch_thread.join(timeout=2)

    # TODO, Support Checking Inference Service Status
    def health(self) -> bool:
        return self._running
