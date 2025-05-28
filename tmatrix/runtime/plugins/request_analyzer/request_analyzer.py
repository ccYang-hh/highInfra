from typing import List, Optional

from tmatrix.runtime.pipeline import PipelineStage
from tmatrix.runtime.plugins import Plugin
from .type_analyzer import TypeAnalyzeStage
from .scene_analyzer import SceneAnalyzeStage


class RequestAnalyzer(Plugin):
    plugin_name = "request_analyzer"
    plugin_version = "0.0.1"

    def __init__(self):
        super().__init__()
        self.type_analyzer_stage: Optional[PipelineStage] = TypeAnalyzeStage("type_analyzer")
        self.scene_analyzer_stage: Optional[PipelineStage] = SceneAnalyzeStage("scene_analyzer")

    def get_pipeline_stages(self) -> List[PipelineStage]:
        """获取管道阶段"""
        return [self.type_analyzer_stage, self.scene_analyzer_stage]
