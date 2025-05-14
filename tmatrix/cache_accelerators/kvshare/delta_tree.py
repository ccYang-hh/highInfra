import difflib
from typing import List

from tmatrix.components.logging import init_logger
logger = init_logger(__name__)


class DeltaOperation:
    """表示编辑操作的类"""
    def __init__(self, op_type, position, token=None):
        self.op_type = op_type  # 'keep', 'delete', 'add', 'replace'
        self.position = position  # 操作位置
        self.token = token  # 对于'add'和'replace'操作需要的token

    def __repr__(self):
        if self.op_type in ['add', 'replace']:
            return f"{self.op_type}@{self.position}:{self.token}"
        return f"{self.op_type}@{self.position}"


class DeltaTree:
    """DELTA Tree实现，用于计算和管理编辑操作"""
    def __init__(self):
        self.operations_cache = {}  # 缓存已计算的编辑操作

    def compute_edit_operations(self, source_tokens: List[str], target_tokens: List[str]) -> List[DeltaOperation]:
        """计算两个token序列之间的编辑操作"""
        # 检查缓存
        cache_key = f"{','.join(source_tokens)}_{','.join(target_tokens)}"
        if cache_key in self.operations_cache:
            return self.operations_cache[cache_key]

        # 使用difflib计算差异
        diff = list(difflib.ndiff(source_tokens, target_tokens))

        operations = []
        source_pos = 0
        target_pos = 0

        for d in diff:
            if d.startswith('  '):  # 相同的token
                operations.append(DeltaOperation('keep', source_pos))
                source_pos += 1
                target_pos += 1
            elif d.startswith('- '):  # 需要删除的token
                operations.append(DeltaOperation('delete', source_pos))
                source_pos += 1
            elif d.startswith('+ '):  # 需要添加的token
                token = d[2:]
                operations.append(DeltaOperation('add', target_pos, token))
                target_pos += 1

        # 缓存结果
        self.operations_cache[cache_key] = operations
        return operations
