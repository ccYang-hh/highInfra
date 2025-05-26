import abc
import asyncio
import enum
import logging
import socket
import threading
import time
from typing import Dict, List
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel
from uhashring import HashRing

from tmatrix.runtime.service_discovery import Endpoint
from tmatrix.runtime.core import RequestContext


@dataclass
class EngineStats:
    url: str
    cpu_percent: int = 0
    gpu_utilization: int = 0
    memory_usage: int = 0
    last_updated: float = field(default_factory=lambda: time.time())


@dataclass
class RequestStats:
    url: str
    qps: int = 0
    latency: int = 0
    success_rate: float = 100.0
    last_updated: float = field(default_factory=lambda: time.time())


class RouteStrategy(abc.ABC):
    """路由策略的基类"""

    def __init__(self):
        self._initialized = False

    async def initialize(self):
        await self._initialize()
        self._initialized = True

    async def _initialize(self):
        pass

    @abc.abstractmethod
    async def route_prefill_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """根据路由策略选择合适的Prefill实例URL"""
        pass

    @abc.abstractmethod
    async def route_decode_request(
            self,
            endpoints: Dict[str, List[Endpoint]],
            engine_stats: Dict[str, EngineStats],
            request_stats: Dict[str, RequestStats],
            context: RequestContext
    ) -> str:
        """根据路由策略选择合适的Decode实例URL"""
        pass

    @property
    def initialized(self):
        return self._initialized
