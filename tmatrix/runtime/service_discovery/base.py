import abc
from typing import List

from .types import EndpointInfo


class ServiceDiscovery(abc.ABC):
    @abc.abstractmethod
    def get_endpoint_info(self) -> List[EndpointInfo]:
        pass

    def get_health(self) -> bool:
        pass

    def close(self):
        pass
