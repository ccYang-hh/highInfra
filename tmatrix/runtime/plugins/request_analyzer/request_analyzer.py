from typing import List

from tmatrix.runtime.plugins import Plugin
from tmatrix.runtime.pipeline import PipelineStage
from .type_analyze import RequestTypeAnalyzer


class RequestAnalyzer(Plugin):
    plugin_name: str = "request_analyzer"
    plugin_version: str = "0.0.1"

    def __init__(self):
        super().__init__()
        self.request_type_analyzer = RequestTypeAnalyzer("request_type_analyzer")

    def get_pipeline_stages(self) -> List[PipelineStage]:
        return [self.request_type_analyzer]
