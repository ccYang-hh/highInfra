import uuid
import asyncio
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from concurrent.futures import ThreadPoolExecutor


from tmatrix.runtime.service_discovery import (
    KVRole, KVEventsConfig, Endpoint, EndpointType, TransportType, EndpointStatus, ServiceDiscoveryType,
    get_etcd_service_discovery
)
from tmatrix.runtime.services import get_prefix_cache_service


# 单线程执行同步操作（endpoint是非高频调度API）
executor = ThreadPoolExecutor(max_workers=1)


async def run_sync(func, *args, **kwargs):
    """在工作线程中执行同步函数"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))


# TODO 考虑将API模型与内部模型统一，减少转换开销
class EndpointAPI(BaseModel):
    endpoint_id: Optional[str] = Field(None, description="端点ID")
    address: str = Field(..., description="服务地址")
    model_name: str = Field(..., description="模型名称")
    instance_name: str = Field(..., description="引擎实例标识")
    endpoint_type: List[EndpointType] = Field(..., description="端点类型")
    transport_type: TransportType = Field(TransportType.HTTP, description="传输类型")
    priority: int = Field(0, description="优先级")
    kv_role: KVRole = Field(KVRole.BOTH, description="KV角色")
    kv_event_config: Optional[KVEventsConfig] = Field(None, description="KV事件配置")
    ttl: Optional[int] = Field(None, description="生存时间(秒)")
    status: EndpointStatus = Field(EndpointStatus.HEALTHY, description="健康状态")
    added_timestamp: Optional[float] = Field(None, description="添加时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")

    def to_internal(self) -> Endpoint:
        """转换为内部Endpoint模型"""
        kv_event_config = self.kv_event_config or KVEventsConfig()
        return Endpoint(
            endpoint_id=self.endpoint_id or str(uuid.uuid4()),
            endpoint_type=self.endpoint_type,
            address=self.address,
            model_name=self.model_name,
            instance_name=self.instance_name,
            transport_type=self.transport_type,
            priority=self.priority,
            kv_role=self.kv_role,
            kv_event_config=kv_event_config,
            metadata=self.metadata
        )

    @classmethod
    def from_internal(cls, endpoint: Endpoint) -> 'EndpointAPI':
        """从内部Endpoint模型创建API模型"""
        return cls(
            endpoint_id=endpoint.endpoint_id,
            address=endpoint.address,
            model_name=endpoint.model_name,
            instance_name=endpoint.instance_name,
            endpoint_type=endpoint.endpoint_type,
            transport_type=endpoint.transport_type,
            status=endpoint.status,
            priority=endpoint.priority,
            kv_role=endpoint.kv_role,
            kv_event_config=endpoint.kv_event_config,
            added_timestamp=endpoint.added_timestamp,
            metadata=endpoint.metadata
        )


router = APIRouter(prefix="/api/v1/endpoints")


# API路由
@router.post("", response_model=EndpointAPI, status_code=201)
async def create_endpoint(data: EndpointAPI):
    """注册新的服务端点"""
    discovery = get_etcd_service_discovery()

    # 1.注册endpoint
    internal_endpoint = data.to_internal()
    await run_sync(discovery.register_endpoint, internal_endpoint, data.ttl)

    # 2.启动该endpoint对应下游引擎的KVEvent订阅任务
    prefix_cache_service = get_prefix_cache_service()
    instance_name = internal_endpoint.instance_name
    endpoint = internal_endpoint.kv_event_config.endpoint
    replay_endpoint = internal_endpoint.kv_event_config.replay_endpoint
    prefix_cache_service.add_vllm_instance(instance_name, endpoint, replay_endpoint)

    # 返回创建的端点信息
    return EndpointAPI.from_internal(internal_endpoint)


@router.get("", response_model=List[EndpointAPI])
async def list_endpoints():
    """获取所有注册的端点"""
    discovery = get_etcd_service_discovery()
    assert discovery is not None, "服务发现组件异常!"

    # 获取并转换
    endpoints = await run_sync(discovery.get_endpoints)
    return [EndpointAPI.from_internal(ep) for ep in endpoints]


@router.get("/{endpoint_id}", response_model=EndpointAPI)
async def get_endpoint(endpoint_id: str):
    """获取特定端点"""
    discovery = get_etcd_service_discovery()
    assert discovery is not None, "服务发现组件异常!"

    # 获取所有端点
    endpoints = await run_sync(discovery.get_endpoints)

    # 查找特定端点
    for ep in endpoints:
        if ep.endpoint_id == endpoint_id:
            return EndpointAPI.from_internal(ep)

    raise HTTPException(status_code=404, detail="端点未找到")


@router.put("/{endpoint_id}", response_model=EndpointAPI)
async def update_endpoint(endpoint_id: str, data: EndpointAPI):
    """更新端点信息"""
    # 确保ID一致
    data.endpoint_id = endpoint_id
    discovery = get_etcd_service_discovery()
    assert discovery is not None, "服务发现组件异常!"

    # 转换并更新
    internal_endpoint = data.to_internal()
    await run_sync(discovery.register_endpoint, internal_endpoint, data.ttl)

    return EndpointAPI.from_internal(internal_endpoint)


@router.delete("/all", status_code=200)
async def delete_all_endpoint():
    """删除端点，该API应置于delete_endpoint前，防止接口匹配被抢占"""
    discovery = get_etcd_service_discovery()
    assert discovery is not None, "服务发现组件异常!"

    endpoints = discovery.get_endpoints()
    await run_sync(discovery.remove_all_endpoints)
    return {
        "status": "success",
        "endpoints": [item.endpoint_id for item in endpoints]
    }


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(endpoint_id: str):
    """删除端点"""
    discovery = get_etcd_service_discovery()
    assert discovery is not None, "服务发现组件异常!"

    await run_sync(discovery.remove_endpoint, endpoint_id)
    return {
        "info": "删除成功",
        "endpoint_id": endpoint_id
    }


@router.get("/health")
async def health_check():
    """健康检查"""
    discovery = get_etcd_service_discovery()
    assert discovery is not None, "服务发现组件异常!"

    health_status = await run_sync(discovery.health)
    return {
        "status": "healthy" if health_status else "unhealthy",
        "service_discovery_type": ServiceDiscoveryType.ETCD.value,
    }
