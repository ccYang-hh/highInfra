from fastapi import APIRouter

from tmatrix.runtime.metrics import MetricsRegistry


router = APIRouter(prefix="/api/v1/metrics")


@router.get("")
async def list_metrics():
    """获取业务系统所有性能指标数据"""
    metrics = await MetricsRegistry().get_all_metrics()
    return metrics