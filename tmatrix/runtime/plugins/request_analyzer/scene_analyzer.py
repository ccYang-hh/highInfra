from tmatrix.vars import InferenceScene
from tmatrix.common.logging import init_logger
from tmatrix.runtime.pipeline import PipelineStage
from tmatrix.runtime.service_discovery import get_etcd_service_discovery, KVRole
from tmatrix.runtime.core import RequestContext, RequestState

logger = init_logger("plugins/scene_analyzer")


class SceneAnalyzeStage(PipelineStage):
    """场景识别"""
    def __init__(self, stage_name: str):
        super().__init__(stage_name)

    async def process(self, context: RequestContext) -> None:
        context.set_state(RequestState.PREPROCESSING)

        # 获取已注册的Endpoints
        etcd_service_discovery = get_etcd_service_discovery()
        endpoints = etcd_service_discovery.get_endpoints()

        scene = InferenceScene.MULTI_INSTANCE
        if len(endpoints) == 1:
            scene = InferenceScene.SINGLE
        else:
            for endpoint in endpoints:
                # 只要存在Producer或者Consumer，就视为PD分离场景
                if endpoint.kv_role in [KVRole.PRODUCER, KVRole.CONSUMER]:
                    scene = InferenceScene.PD_DISAGGREGATION

        context.scene = scene
