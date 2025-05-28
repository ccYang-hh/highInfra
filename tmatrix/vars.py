from dataclasses import dataclass
from enum import Enum, StrEnum


class InferenceScene(StrEnum):
    """推理场景"""
    PD_DISAGGREGATION = "PD_DISAGGREGATION"  # PD分离
    MULTI_INSTANCE = "MULTI_INSTANCE"        # 多实例（非PD分离）
    SINGLE = "SINGLE"                        # 单实例


class RequestType(StrEnum):
    """请求类型"""
    FIRST_TIME = "FIRST_TIME"
    HISTORY = "HISTORY"
    RAG = "RAG"
