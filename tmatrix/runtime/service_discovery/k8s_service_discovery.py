import time
import threading
from typing import List, Optional

from .types import TransportType, Endpoint, EndpointType, PodServiceLabel
from .base import CachedServiceDiscovery

from tmatrix.common.logging import init_logger
logger = init_logger(__name__)


def _check_ready(pod) -> bool:
    cs = pod.status.container_statuses or []
    return all(getattr(status, "ready", False) for status in cs)


# TODO, Support Async Realized Version
class K8SServiceDiscovery(CachedServiceDiscovery):
    """基于缓存的K8S服务发现"""
    def __init__(self, namespace: str, port: int,
                 label_selector: Optional[str] = None,
                 cache_ttl: float = 3.0,
                 enable_model_probe: bool = True):
        super().__init__(cache_ttl=cache_ttl)
        # K8s sdk初始化
        from kubernetes import client, config, watch
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        self.api = client.CoreV1Api()
        self.namespace = namespace
        self.port = port
        self.label_selector = label_selector
        self.enable_model_probe = enable_model_probe
        self._pod_meta_watch_thread = threading.Thread(target=self._watch_pods, daemon=True)
        self._last_refresh = 0
        self._refresh_interval = cache_ttl
        self._watcher_running = True
        self._watch_pod_thread_started = False
        # 启动pod事件watch，及时刷新缓存
        self.w = watch.Watch()
        self._start_watch_thread()

    def _start_watch_thread(self):
        if not self._watch_pod_thread_started:
            self._pod_meta_watch_thread.start()
            self._watch_pod_thread_started = True

    # def _get_model_name(self, pod_ip: str) -> str:
    #     """可选主动探针：http访问服务获取模型名"""
    #     url = f"http://{pod_ip}:{self.port}/v1/models"
    #     try:
    #         headers = {}
    #         if os.getenv("VLLM_API_KEY"):
    #             headers["Authorization"] = f"Bearer {os.environ['VLLM_API_KEY']}"
    #         resp = requests.get(url, headers=headers, timeout=2)
    #         resp.raise_for_status()
    #         # 假定openai兼容接口
    #         return resp.json()["data"][0]["id"]
    #     except Exception:
    #         return ""

    def _fetch_endpoints(self) -> List[Endpoint]:
        """缓存失效时/首次调用时/手动刷新时，主动从k8s list拉取数据"""
        try:
            pods = self.api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=self.label_selector
            )
            eps = []
            for pod in pods.items:
                if pod.status.phase != "Running" or not pod.status.pod_ip:
                    continue
                if not _check_ready(pod):
                    continue

                pod_ip = pod.status.pod_ip
                model_name = pod.metadata.labels.get(PodServiceLabel.MODEL_NAME.value, "default")
                # TODO, Support Model Probe
                if self.enable_model_probe:
                    logger.warning("model_probe is not implemented yet in K8SServiceDiscovery.")
                transport = pod.metadata.labels.get(PodServiceLabel.SERVICE_TRANSPORT_TYPE.value, "http")
                try:
                    transport_type = TransportType(transport)
                except Exception:
                    transport_type = TransportType.HTTP

                endpoint = Endpoint(
                    endpoint_id=pod.metadata.name,
                    address=f"{pod_ip}:{self.port}",
                    model_name=model_name,
                    endpoint_type=EndpointType.CHAT_COMPLETION,
                    transport_type=transport_type,
                    added_timestamp=time.time(),
                    priority=int(pod.metadata.labels.get(PodServiceLabel.SERVICE_PRIORITY.value, "0")),
                    metadata=dict(pod.metadata.labels)
                )
                eps.append(endpoint)
            return eps
        except Exception as e:
            logger.error(f"fetch_endpoints error: {e}")
            return self._endpoints  # 使用旧的缓存数据

    def _watch_pods(self):
        """
        监听Pod变化以触发缓存刷新

        策略：
            1.只对【Running、Ready 状态发生变化】【Pod 新增/就绪】【Pod 删除/异常终止】等影响现有高可用端点池的事件触发刷新
            2.某些 MODIFIED 事件如日志或 status 字段变化、非 Ready 变动可忽略，无需强制刷新
        """
        while self._watcher_running:
            try:
                for event in self.w.stream(...):
                    pod = event["object"]
                    event_type = event["type"]
                    # 仅关键事件刷新
                    if event_type in ("ADDED", "DELETED"):
                        self._last_update = 0
                    elif event_type == "MODIFIED":
                        # 检查是否为容器ready或pod phase变化等
                        before_status = getattr(pod, "status", None)
                        if before_status and (_check_ready(pod) or pod.status.phase not in ["Running", "Succeeded"]):
                            self._last_update = 0
            except Exception as e:
                logger.error(f"K8s watch error: {e}")
                time.sleep(1)

    def close(self):
        self._watcher_running = False
        self.w.stop()
        self._pod_meta_watch_thread.join()

    def health(self) -> bool:
        return len(self._endpoints) > 0 and all(_check_ready(pod) for pod in self._endpoints)