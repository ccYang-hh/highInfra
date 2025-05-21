from .endpoints import router as endpoint_router

SYSTEM_ROUTERS = {
    "endpoint": endpoint_router
}

__all__ = [
    'SYSTEM_ROUTERS'
]