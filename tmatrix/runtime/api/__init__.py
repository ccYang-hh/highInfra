from .endpoints import router as endpoint_router
from .metrics import router as metrics_router

SYSTEM_ROUTERS = {
    "endpoint": endpoint_router,
    "metrics": metrics_router
}

__all__ = [
    'SYSTEM_ROUTERS'
]