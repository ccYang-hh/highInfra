import json
import re
import uuid
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from tmatrix.common.logging import init_logger
from tmatrix.runtime.utils import HTTPXClientWrapper
from tmatrix.runtime.pipeline import PipelineStage
from tmatrix.runtime.plugins import Plugin
from tmatrix.runtime.core import RequestContext, RequestState

logger = init_logger("plugins/request_type_analyzer")


class OpenAIRequestTypeAnalyzer(PipelineStage):
    def __init__(self, stage_name: str):
        """初始化流处理阶段"""
        super().__init__(stage_name)

    async def process(self, context: RequestContext) -> None:
        pass


@dataclass
class RAGChunk:
    """表示一个RAG检索块的数据类"""
    content: str
    metadata: Dict[str, Any]
    index: int


class OpenAIRequestAnalyzer:
    """OpenAI API请求分析器"""

    # RAG内容的常见标记模式
    RAG_PATTERNS = [
        r"<context>(.*?)</context>",  # 基本XML标记
        r"\[CONTEXT\](.*?)\[/CONTEXT\]",  # 方括号标记
        r"Source \d+:(.*?)(?=Source \d+:|$)"  # "Source N:" 格式
    ]

    def __init__(self):
        self.session_store = {}  # 简单的会话存储

    def analyze_request(self, request_data: Dict) -> Dict[str, Any]:
        """
        分析OpenAI API请求并返回详细信息

        Args:
            request_data: OpenAI API请求数据

        Returns:
            包含分析结果的字典
        """
        result = {
            "request_type": self._determine_request_type(request_data),
            "session_info": self._extract_session_info(request_data),
            "rag_info": None
        }

        if result["request_type"] == "rag":
            result["rag_info"] = self._analyze_rag_content(request_data)

        return result

    def _determine_request_type(self, request_data: Dict) -> str:
        """
        确定请求类型：首次请求、历史请求或RAG请求
        """
        messages = request_data.get("messages", [])

        # 检查是否有RAG内容
        if self._has_rag_content(messages):
            return "rag"

        # 检查是否是首次请求
        user_messages = [msg for msg in messages if msg.get("role") == "user"]
        assistant_messages = [msg for msg in messages if msg.get("role") == "assistant"]

        if len(assistant_messages) == 0 and len(user_messages) == 1:
            return "first_time"
        else:
            return "history"

    def _has_rag_content(self, messages: List[Dict]) -> bool:
        """
        检查消息列表中是否包含RAG内容
        """
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                for pattern in self.RAG_PATTERNS:
                    if re.search(pattern, content):
                        return True

            # 检查是否使用了函数调用或工具格式添加RAG内容
            if "function_call" in message or "tool_calls" in message:
                return True

        return False

    def _analyze_rag_content(self, request_data: Dict) -> Dict:
        """
        分析RAG内容，提取chunks并返回详细信息
        """
        messages = request_data.get("messages", [])
        chunks = []
        chunk_count = 0

        for message in messages:
            content = message.get("content", "")
            if not isinstance(content, str):
                continue

            # 查找所有可能的RAG块
            for pattern in self.RAG_PATTERNS:
                matches = re.finditer(pattern, content)
                for i, match in enumerate(matches):
                    chunk_count += 1
                    chunk_text = match.group(1).strip()

                    # 尝试从块中提取元数据
                    metadata = self._extract_metadata(chunk_text)
                    chunks.append(RAGChunk(
                        content=chunk_text,
                        metadata=metadata,
                        index=chunk_count
                    ))

        # 查找工具调用或函数调用中的RAG内容
        chunks.extend(self._extract_chunks_from_tools(request_data))

        return {
            "chunk_count": len(chunks),
            "chunks": chunks
        }

    def _extract_chunks_from_tools(self, request_data: Dict) -> List[RAGChunk]:
        """从工具调用或函数调用中提取RAG块"""
        chunks = []
        messages = request_data.get("messages", [])

        for message in messages:
            # 检查函数调用
            if "function_call" in message:
                try:
                    func_args = json.loads(message["function_call"].get("arguments", "{}"))
                    if "context" in func_args:
                        chunks.append(RAGChunk(
                            content=func_args["context"],
                            metadata={"source": "function_call"},
                            index=len(chunks) + 1
                        ))
                except json.JSONDecodeError:
                    pass

            # 检查工具调用
            if "tool_calls" in message:
                for tool_call in message["tool_calls"]:
                    try:
                        if "function" in tool_call:
                            func_args = json.loads(tool_call["function"].get("arguments", "{}"))
                            if "context" in func_args:
                                chunks.append(RAGChunk(
                                    content=func_args["context"],
                                    metadata={"source": "tool_call"},
                                    index=len(chunks) + 1
                                ))
                    except json.JSONDecodeError:
                        pass

        return chunks

    def _extract_metadata(self, chunk_text: str) -> Dict[str, Any]:
        """
        尝试从块文本中提取元数据
        """
        metadata = {}

        # 查找可能的元数据模式，如 [source: wikipedia]
        source_match = re.search(r"\[source:\s*([^\]]+)\]", chunk_text)
        if source_match:
            metadata["source"] = source_match.group(1).strip()

        # 查找URL模式
        url_match = re.search(r"https?://\S+", chunk_text)
        if url_match:
            metadata["url"] = url_match.group(0)

        return metadata

    def _extract_session_info(self, request_data: Dict) -> Dict:
        """
        提取用户会话信息
        """
        # 尝试从请求头获取会话ID
        headers = request_data.get("headers", {})
        session_id = headers.get("x-session-id")

        # 如果请求头中没有会话ID，则查找或创建
        if not session_id:
            user_id = self._extract_user_id(request_data)
            if user_id in self.session_store:
                session_id = self.session_store[user_id]
            else:
                session_id = str(uuid.uuid4())
                self.session_store[user_id] = session_id

        return {
            "session_id": session_id,
            "user_id": self._extract_user_id(request_data),
            "is_new_session": session_id not in self.session_store.values()
        }

    def _extract_user_id(self, request_data: Dict) -> str:
        """
        从请求中提取用户ID，如果没有则生成一个
        """
        # 尝试从请求头获取用户ID
        headers = request_data.get("headers", {})
        user_id = headers.get("x-user-id")

        if not user_id:
            # 尝试从请求体获取用户ID
            user_id = request_data.get("user", "")

        if not user_id:
            # 如果仍然没有用户ID，则生成一个基于请求特征的唯一标识
            messages = request_data.get("messages", [])
            if messages:
                # 使用第一条消息的某些特征
                first_message = messages[0]
                content_hash = hash(first_message.get("content", ""))
                user_id = f"anonymous-{content_hash}"
            else:
                user_id = f"anonymous-{uuid.uuid4()}"

        return user_id


# 示例用法
def process_openai_request(request_data):
    analyzer = OpenAIRequestAnalyzer()
    analysis_result = analyzer.analyze_request(request_data)

    print(f"请求类型: {analysis_result['request_type']}")
    print(f"会话信息: {analysis_result['session_info']}")

    if analysis_result['rag_info']:
        print(f"RAG块数量: {analysis_result['rag_info']['chunk_count']}")
        for i, chunk in enumerate(analysis_result['rag_info']['chunks']):
            print(f"RAG块 {i + 1}:")
            print(f"  内容: {chunk.content[:50]}...")
            print(f"  元数据: {chunk.metadata}")

    return analysis_result
