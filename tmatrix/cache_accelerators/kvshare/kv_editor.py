import torch

from tmatrix.components.logging import init_logger
logger = init_logger(__name__)


class KVCache:
    """KV缓存管理类，实现细粒度的KV缓存编辑操作"""
    def __init__(self, num_layers, hidden_dim, device):
        self.num_layers = num_layers
        self.hidden_dim = hidden_dim
        self.device = device
        self.cache = {}

        for i in range(num_layers):
            self.cache[str(i)] = {
                'k': None,
                'v': None
            }

    @classmethod
    def from_model_output(cls, past_key_values, device):
        """从模型输出创建KV缓存"""
        num_layers = len(past_key_values)
        if num_layers == 0:
            return None

        # 获取隐藏维度
        k, v = past_key_values[0]
        hidden_dim = k.shape[-1]

        # 创建KV缓存对象
        kv_cache = cls(num_layers, hidden_dim, device)

        # 填充缓存
        for i, (k, v) in enumerate(past_key_values):
            kv_cache.cache[str(i)] = {
                'k': k.detach(),
                'v': v.detach()
            }

        return kv_cache

    # def apply_operations(self, operations, new_tokens=None, model=None, tokenizer=None):
    #     """应用编辑操作到KV缓存"""
    #     if not operations:
    #         return self
    #
    #     # 创建新的KV缓存对象
    #     new_cache = KVCache(self.num_layers, self.hidden_dim, self.device)
    #
    #     # 收集需要计算的token位置
    #     tokens_to_compute = []
    #     compute_positions = []
    #
    #     # 根据操作类型处理KV值
    #     for op in operations:
    #         if op.op_type == 'add' or op.op_type == 'replace':
    #             if op.token and new_tokens:
    #                 pos = op.position
    #                 if pos < len(new_tokens):
    #                     tokens_to_compute.append(new_tokens[pos])
    #                     compute_positions.append(pos)
    #
    #     # 计算新token的KV值
    #     new_kv_values = None
    #     if tokens_to_compute and model and tokenizer:
    #         new_kv_values = self._compute_kv_for_tokens(tokens_to_compute, model, tokenizer)
    #
    #     # 对每一层应用操作
    #     for layer_idx in range(self.num_layers):
    #         layer_name = str(layer_idx)
    #         source_k = self.cache[layer_name]['k']
    #         source_v = self.cache[layer_name]['v']
    #
    #         if source_k is None or source_v is None:
    #             continue
    #
    #         # 获取源张量的完整形状信息
    #         source_k_shape = source_k.shape
    #         source_v_shape = source_v.shape
    #
    #         # 创建用于构建新KV的列表
    #         new_k_list = []
    #         new_v_list = []
    #
    #         # 跟踪源和目标位置
    #         src_pos = 0
    #         tgt_pos = 0
    #         compute_idx = 0
    #
    #         # 应用编辑操作
    #         for op in operations:
    #             if op.op_type == 'keep':
    #                 # 保留原始KV值
    #                 if src_pos < source_k.shape[1]:
    #                     new_k_list.append(source_k[:, src_pos:src_pos+1, :])
    #                     new_v_list.append(source_v[:, src_pos:src_pos+1, :])
    #                 src_pos += 1
    #                 tgt_pos += 1
    #
    #             elif op.op_type == 'delete':
    #                 # 跳过该位置的KV值
    #                 src_pos += 1
    #
    #             elif op.op_type == 'add':
    #                 # 添加新的KV值
    #                 if new_kv_values and tgt_pos in compute_positions:
    #                     idx = compute_positions.index(tgt_pos)
    #                     new_k = new_kv_values[layer_idx][0][:, idx:idx+1, :]
    #                     new_v = new_kv_values[layer_idx][1][:, idx:idx+1, :]
    #                     new_k_list.append(new_k)
    #                     new_v_list.append(new_v)
    #                     compute_idx += 1
    #                 else:
    #                     # 使用零张量作为占位符
    #                     k_shape = list(source_k.shape)
    #                     v_shape = list(source_v.shape)
    #                     k_shape[1] = 1
    #                     v_shape[1] = 1
    #                     new_k_list.append(torch.zeros(k_shape, dtype=source_k.dtype, device=source_k.device))
    #                     new_v_list.append(torch.zeros(v_shape, dtype=source_v.dtype, device=source_v.device))
    #                 tgt_pos += 1
    #
    #             elif op.op_type == 'replace':
    #                 # 替换KV值
    #                 if new_kv_values and tgt_pos in compute_positions:
    #                     idx = compute_positions.index(tgt_pos)
    #                     new_k = new_kv_values[layer_idx][0][:, idx:idx+1, :]
    #                     new_v = new_kv_values[layer_idx][1][:, idx:idx+1, :]
    #                     new_k_list.append(new_k)
    #                     new_v_list.append(new_v)
    #                     compute_idx += 1
    #                 else:
    #                     # 使用零张量作为占位符
    #                     k_shape = list(source_k.shape)
    #                     v_shape = list(source_v.shape)
    #                     k_shape[1] = 1
    #                     v_shape[1] = 1
    #                     new_k_list.append(torch.zeros(k_shape, dtype=source_k.dtype, device=source_k.device))
    #                     new_v_list.append(torch.zeros(v_shape, dtype=source_v.dtype, device=source_v.device))
    #                 src_pos += 1
    #                 tgt_pos += 1
    #
    #         # 拼接KV值
    #         if new_k_list and new_v_list:
    #             try:
    #                 new_cache.cache[layer_name]['k'] = torch.cat(new_k_list, dim=1)
    #                 new_cache.cache[layer_name]['v'] = torch.cat(new_v_list, dim=1)
    #             except Exception as e:
    #                 logger.error(f"Error concatenating KV values: {e}")
    #                 # 回退到空缓存
    #                 new_cache.cache[layer_name]['k'] = torch.zeros((source_k.shape[0], 0, source_k.shape[2]),
    #                                                                dtype=source_k.dtype, device=source_k.device)
    #                 new_cache.cache[layer_name]['v'] = torch.zeros((source_v.shape[0], 0, source_v.shape[2]),
    #                                                                dtype=source_v.dtype, device=source_v.device)
    #         else:
    #             # 创建空缓存
    #             new_cache.cache[layer_name]['k'] = torch.zeros((source_k.shape[0], 0, source_k.shape[2]),
    #                                                            dtype=source_k.dtype, device=source_k.device)
    #             new_cache.cache[layer_name]['v'] = torch.zeros((source_v.shape[0], 0, source_v.shape[2]),
    #                                                            dtype=source_v.dtype, device=source_v.device)
    #
    #     return new_cache

    def apply_operations(self, operations, new_tokens=None, model=None, tokenizer=None):
        """应用编辑操作到KV缓存 - 改进版本"""
        if not operations:
            return self

        # 创建新的KV缓存对象
        new_cache = KVCache(self.num_layers, self.hidden_dim, self.device)

        # 收集需要计算的token位置
        tokens_to_compute = []
        compute_positions = []

        # 根据操作类型处理KV值
        for op in operations:
            if op.op_type == 'add' or op.op_type == 'replace':
                if op.token and new_tokens:
                    pos = op.position
                    if pos < len(new_tokens):
                        tokens_to_compute.append(new_tokens[pos])
                        compute_positions.append(pos)

        # 计算新token的KV值
        new_kv_values = None
        if tokens_to_compute and model and tokenizer:
            new_kv_values = self._compute_kv_for_tokens(tokens_to_compute, model, tokenizer)

        # 对每一层应用操作
        for layer_idx in range(self.num_layers):
            layer_name = str(layer_idx)
            source_k = self.cache[layer_name]['k']
            source_v = self.cache[layer_name]['v']

            if source_k is None or source_v is None:
                continue

            # 获取源张量的完整形状信息
            source_k_shape = source_k.shape
            source_v_shape = source_v.shape

            # 创建用于构建新KV的列表
            new_k_list = []
            new_v_list = []

            # 跟踪源和目标位置
            src_pos = 0
            tgt_pos = 0
            compute_idx = 0

            # 应用编辑操作
            for op in operations:
                if op.op_type == 'keep':
                    # 保留原始KV值
                    if src_pos < source_k.shape[1]:
                        new_k_list.append(source_k[:, src_pos:src_pos+1, :])
                        new_v_list.append(source_v[:, src_pos:src_pos+1, :])
                    src_pos += 1
                    tgt_pos += 1

                elif op.op_type == 'delete':
                    # 跳过该位置的KV值
                    src_pos += 1

                elif op.op_type == 'add' or op.op_type == 'replace':
                    # 添加或替换KV值
                    if new_kv_values and tgt_pos in compute_positions:
                        idx = compute_positions.index(tgt_pos)
                        try:
                            new_k = new_kv_values[layer_idx][0][:, idx:idx+1, :]
                            new_v = new_kv_values[layer_idx][1][:, idx:idx+1, :]

                            # 关键修复：确保形状匹配
                            if new_k.shape != source_k[:, 0:1, :].shape:
                                # 记录形状不匹配的详细信息
                                logger.debug(f"Shape mismatch - Expected: {source_k[:, 0:1, :].shape}, Got: {new_k.shape}")

                                # 调整新计算的KV值形状以匹配源KV缓存
                                # 这里假设最后一个维度是隐藏维度，可以保持不变
                                if len(new_k.shape) == len(source_k_shape):
                                    # 重塑为匹配的形状，保持序列长度维度为1
                                    new_shape = list(source_k_shape)
                                    new_shape[1] = 1
                                    new_k = new_k.reshape(new_shape)
                                    new_v = new_v.reshape(list(source_v_shape)[:-1] + [source_v_shape[-1]])
                                else:
                                    # 处理维度数量不同的情况
                                    # 例如，将[heads, batch, seq, dim]转换为[batch, seq, heads*dim]
                                    if len(new_k.shape) > len(source_k_shape):
                                        # 将多头注意力扁平化为单个维度
                                        new_k = new_k.permute(1, 2, 0, 3).reshape(source_k_shape[0], 1, source_k_shape[2])
                                        new_v = new_v.permute(1, 2, 0, 3).reshape(source_v_shape[0], 1, source_v_shape[2])
                                    else:
                                        # 添加缺失的维度
                                        logger.warning("Dimension mismatch that cannot be automatically resolved")
                                        raise ValueError("Cannot reshape KV values to match source shape")

                            new_k_list.append(new_k)
                            new_v_list.append(new_v)
                        except Exception as e:
                            logger.error(f"Error processing KV values at position {tgt_pos}: {e}")
                            # 使用零张量作为占位符
                            new_k_list.append(torch.zeros((source_k_shape[0], 1, source_k_shape[2]),
                                                          dtype=source_k.dtype, device=source_k.device))
                            new_v_list.append(torch.zeros((source_v_shape[0], 1, source_v_shape[2]),
                                                          dtype=source_v.dtype, device=source_v.device))
                        compute_idx += 1
                    else:
                        # 使用零张量作为占位符
                        new_k_list.append(torch.zeros((source_k_shape[0], 1, source_k_shape[2]),
                                                      dtype=source_k.dtype, device=source_k.device))
                        new_v_list.append(torch.zeros((source_v_shape[0], 1, source_v_shape[2]),
                                                      dtype=source_v.dtype, device=source_v.device))

                    if op.op_type == 'replace':
                        src_pos += 1
                    tgt_pos += 1

            # 拼接KV值
            if new_k_list and new_v_list:
                try:
                    # 检查所有张量形状是否一致（除序列长度外）
                    shapes_k = [t.shape for t in new_k_list]
                    shapes_v = [t.shape for t in new_v_list]
                    logger.debug(f"Layer {layer_idx} K shapes: {shapes_k}")

                    new_cache.cache[layer_name]['k'] = torch.cat(new_k_list, dim=1)
                    new_cache.cache[layer_name]['v'] = torch.cat(new_v_list, dim=1)
                except Exception as e:
                    logger.error(f"Error concatenating KV values: {e}")
                    # 回退到空缓存
                    new_cache.cache[layer_name]['k'] = torch.zeros((source_k_shape[0], 0, source_k_shape[2]),
                                                                   dtype=source_k.dtype, device=source_k.device)
                    new_cache.cache[layer_name]['v'] = torch.zeros((source_v_shape[0], 0, source_v_shape[2]),
                                                                   dtype=source_v.dtype, device=source_v.device)
            else:
                # 创建空缓存
                new_cache.cache[layer_name]['k'] = torch.zeros((source_k_shape[0], 0, source_k_shape[2]),
                                                               dtype=source_k.dtype, device=source_k.device)
                new_cache.cache[layer_name]['v'] = torch.zeros((source_v_shape[0], 0, source_v_shape[2]),
                                                               dtype=source_v.dtype, device=source_v.device)

        return new_cache

        def _compute_kv_for_tokens(self, tokens, model, tokenizer):
            """单独计算指定token的KV值"""
            token_ids = torch.tensor([tokens], device=self.device)

            # 使用模型计算KV值
            with torch.no_grad():
                outputs = model(token_ids, use_cache=True)

            # 提取KV缓存
            return outputs.past_key_values

        def to_tuple(self):
            """将KV缓存转换为元组格式，用于模型输入"""
            past_key_values = []

            for i in range(self.num_layers):
                layer_name = str(i)
                k = self.cache[layer_name]['k']
                v = self.cache[layer_name]['v']

                if k is None or v is None:
                    # 创建空张量
                    dummy_shape = (1, 0, self.hidden_dim)
                    k = torch.zeros(dummy_shape, dtype=torch.float16, device=self.device)
                    v = torch.zeros(dummy_shape, dtype=torch.float16, device=self.device)

                past_key_values.append((k, v))

            return tuple(past_key_values)

        def get_seq_len(self):
            """获取序列长度"""
            for layer_name in self.cache:
                k = self.cache[layer_name]['k']
                if k is not None:
                    return k.shape[1]
            return 0


class GQAKVCache:
    """KV缓存管理类，实现细粒度的KV缓存编辑操作，兼容GQA、MQA"""

    def __init__(self, num_layers, hidden_dim, device, model_config=None):
        self.num_layers = num_layers
        self.hidden_dim = hidden_dim
        self.device = device
        self.cache = {}
        self.model_config = model_config  # 保存模型配置

        self.is_gqa = model_config is not None and hasattr(model_config,
                                                           "num_key_value_heads") and model_config.num_key_value_heads < model_config.num_attention_heads
        self.num_attention_heads = model_config.num_attention_heads if model_config else None
        self.num_kv_heads = model_config.num_key_value_heads if self.is_gqa and model_config else self.num_attention_heads

        logger.info(
            f"KVCache初始化: GQA模型={self.is_gqa}, 注意力头={self.num_attention_heads}, KV头={self.num_kv_heads}")

        for i in range(num_layers):
            self.cache[str(i)] = {
                'k': None,
                'v': None
            }

    @classmethod
    def from_model_output(cls, past_key_values, device, model_config=None):
        """从模型输出创建KV缓存"""
        num_layers = len(past_key_values)
        if num_layers == 0:
            return None

        # 获取隐藏维度
        k, v = past_key_values[0]
        # 记录和日志原始KV形状
        k_shape = k.shape
        v_shape = v.shape
        logger.info(f"原始KV缓存形状: K={k_shape}, V={v_shape}")

        # 检测形状来确定KV表示
        # Qwen2等GQA模型可能使用[batch, seq, heads, dim]或其他自定义格式
        hidden_dim = k.shape[-1]

        # 创建KV缓存对象
        kv_cache = cls(num_layers, hidden_dim, device, model_config)

        # 填充缓存
        for i, (k, v) in enumerate(past_key_values):
            kv_cache.cache[str(i)] = {
                'k': k.detach(),
                'v': v.detach()
            }

        return kv_cache

    def apply_operations(self, operations, new_tokens=None, model=None, tokenizer=None):
        """应用编辑操作到KV缓存 - 改进版本"""
        if not operations:
            return self

        # 创建新的KV缓存对象
        new_cache = GQAKVCache(self.num_layers, self.hidden_dim, self.device, self.model_config)

        # 收集需要计算的token位置
        tokens_to_compute = []
        compute_positions = []

        # 根据操作类型处理KV值
        for op in operations:
            if op.op_type == 'add' or op.op_type == 'replace':
                if op.token and new_tokens:
                    pos = op.position
                    if pos < len(new_tokens):
                        tokens_to_compute.append(new_tokens[pos])
                        compute_positions.append(pos)

        # 计算新token的KV值
        new_kv_values = None
        if tokens_to_compute and model and tokenizer:
            try:
                # 使用模型计算新token的KV值
                new_kv_values = self._compute_kv_for_tokens(tokens_to_compute, model, tokenizer)
                # if new_kv_values:
                #     # 记录新计算的KV值形状，用于调试
                #     for i, (k, v) in enumerate(new_kv_values):
                #         logger.debug(f"层{i}新计算的KV形状: K={k.shape}, V={v.shape}")
            except Exception as e:
                logger.error(f"计算Delta KV值时出错: {e}")

        # 对每一层应用操作
        for layer_idx in range(self.num_layers):
            layer_name = str(layer_idx)
            source_k = self.cache[layer_name]['k']
            source_v = self.cache[layer_name]['v']

            if source_k is None or source_v is None:
                continue

            # 获取源张量的完整形状信息
            source_k_shape = list(source_k.shape)
            source_v_shape = list(source_v.shape)

            # 创建用于构建新KV的列表
            new_k_list = []
            new_v_list = []

            # 跟踪源和目标位置
            src_pos = 0
            tgt_pos = 0
            compute_idx = 0

            # 应用编辑操作
            for op in operations:
                if op.op_type == 'keep':
                    # 保留原始KV值
                    if src_pos < source_k.shape[1]:
                        # new_k_list.append(source_k[:, src_pos:src_pos+1, :])
                        # new_v_list.append(source_v[:, src_pos:src_pos+1, :])
                        new_k_list.append(source_k[:, src_pos:src_pos + 1])
                        new_v_list.append(source_v[:, src_pos:src_pos + 1])
                    src_pos += 1
                    tgt_pos += 1

                elif op.op_type == 'delete':
                    # 跳过该位置的KV值
                    src_pos += 1

                elif op.op_type == 'add' or op.op_type == 'replace':
                    # 添加或替换KV值
                    if new_kv_values and tgt_pos in compute_positions:
                        idx = compute_positions.index(tgt_pos)
                        try:
                            if layer_idx < len(new_kv_values):
                                new_k = new_kv_values[layer_idx][0]
                                new_v = new_kv_values[layer_idx][1]

                                # 处理序列维度
                                if len(new_k.shape) >= 2 and idx < new_k.shape[1]:
                                    # 如果新计算的KV包含多个token，选择正确的位置
                                    new_k = new_k[:, idx:idx + 1]
                                    new_v = new_v[:, idx:idx + 1]

                                # GQA适配：确保形状匹配
                                if self.is_gqa:
                                    # 检查形状是否需要调整
                                    if new_k.shape != source_k[:, 0:1].shape:
                                        logger.debug(
                                            f"需要调整KV形状: 源={source_k[:, 0:1].shape}, 新={new_k.shape}")

                                        # 创建与源KV相同形状的零张量
                                        adjusted_k = torch.zeros_like(source_k[:, 0:1])
                                        adjusted_v = torch.zeros_like(source_v[:, 0:1])

                                        # 如果维度完全不同，只使用零张量
                                        # 如果维度数量相同但大小不同，尝试复制有效部分
                                        if len(new_k.shape) == len(source_k_shape):
                                            # 复制尽可能多的数据
                                            # 对于GQA模型，最后两个维度是[heads, dim]
                                            if len(new_k.shape) == 4:  # [batch, seq, heads, dim]
                                                # 复制头维度和隐藏维度的最小公共部分
                                                min_heads = min(new_k.shape[2], source_k_shape[2])
                                                min_dim = min(new_k.shape[3], source_k_shape[3])
                                                adjusted_k[:, :, :min_heads, :min_dim] = new_k[:, :, :min_heads,
                                                                                         :min_dim]
                                                adjusted_v[:, :, :min_heads, :min_dim] = new_v[:, :, :min_heads,
                                                                                         :min_dim]

                                        new_k = adjusted_k
                                        new_v = adjusted_v

                                new_k_list.append(new_k)
                                new_v_list.append(new_v)
                            else:
                                # 使用零张量作为占位符
                                dummy_k = torch.zeros_like(source_k[:, 0:1])
                                dummy_v = torch.zeros_like(source_v[:, 0:1])
                                new_k_list.append(dummy_k)
                                new_v_list.append(dummy_v)
                        except Exception as e:
                            logger.error(f"处理位置{tgt_pos}的KV值时出错: {e}")
                            # 使用零张量作为占位符
                            dummy_k = torch.zeros_like(source_k[:, 0:1])
                            dummy_v = torch.zeros_like(source_v[:, 0:1])
                            new_k_list.append(dummy_k)
                            new_v_list.append(dummy_v)
                        compute_idx += 1
                    else:
                        # 使用零张量作为占位符
                        dummy_k = torch.zeros_like(source_k[:, 0:1])
                        dummy_v = torch.zeros_like(source_v[:, 0:1])
                        new_k_list.append(dummy_k)
                        new_v_list.append(dummy_v)

                    if op.op_type == 'replace':
                        src_pos += 1
                    tgt_pos += 1

            # 拼接KV值
            if new_k_list and new_v_list:
                try:
                    # 检查所有张量形状是否一致
                    shapes_consistent = True
                    first_shape = new_k_list[0].shape
                    for i, tensor in enumerate(new_k_list):
                        if tensor.shape != first_shape:
                            logger.error(f"形状不一致: 索引{i}的形状为{tensor.shape}，首个形状为{first_shape}")
                            shapes_consistent = False
                            break

                    if shapes_consistent:
                        new_cache.cache[layer_name]['k'] = torch.cat(new_k_list, dim=1)
                        new_cache.cache[layer_name]['v'] = torch.cat(new_v_list, dim=1)
                    else:
                        raise ValueError("张量形状不一致，无法拼接")
                except Exception as e:
                    logger.error(f"拼接KV值时出错: {e}")
                    # 回退到空缓存 - 创建与源形状相同但序列长度为0的张量
                    empty_k_shape = list(source_k_shape)
                    empty_k_shape[1] = 0  # 序列维度为0

                    empty_v_shape = list(source_v_shape)
                    empty_v_shape[1] = 0  # 序列维度为0

                    new_cache.cache[layer_name]['k'] = torch.zeros(empty_k_shape, dtype=source_k.dtype,
                                                                   device=source_k.device)
                    new_cache.cache[layer_name]['v'] = torch.zeros(empty_v_shape, dtype=source_v.dtype,
                                                                   device=source_v.device)
            else:
                # 创建空缓存
                empty_k_shape = list(source_k_shape)
                empty_k_shape[1] = 0  # 序列维度为0

                empty_v_shape = list(source_v_shape)
                empty_v_shape[1] = 0  # 序列维度为0

                new_cache.cache[layer_name]['k'] = torch.zeros(empty_k_shape, dtype=source_k.dtype,
                                                               device=source_k.device)
                new_cache.cache[layer_name]['v'] = torch.zeros(empty_v_shape, dtype=source_v.dtype,
                                                               device=source_v.device)

        return new_cache

    def _compute_kv_for_tokens(self, tokens, model, tokenizer):
        """单独计算指定token的KV值"""
        # 将tokens转换为tensor
        if isinstance(tokens[0], int):
            token_ids = torch.tensor([tokens], device=self.device)
        else:
            token_ids = tokenizer(tokens, add_special_tokens=False, return_tensors="pt").input_ids.to(self.device)

        # 使用模型计算KV值
        with torch.no_grad():
            try:
                outputs = model(token_ids, use_cache=True)

                # 记录新计算的KV缓存形状
                if outputs.past_key_values:
                    k, v = outputs.past_key_values[0]
                    logger.debug(f"新计算的KV缓存形状: K={k.shape}, V={v.shape}")

                return outputs.past_key_values
            except Exception as e:
                logger.error(f"计算KV值时出错: {e}")
                return None

    def to_tuple(self):
        """将KV缓存转换为元组格式，用于模型输入 - 适配GQA"""
        past_key_values = []

        for i in range(self.num_layers):
            layer_name = str(i)
            k = self.cache[layer_name]['k']
            v = self.cache[layer_name]['v']

            if k is None or v is None:
                # 创建空张量 - 根据是否为GQA模型使用不同形状
                if self.is_gqa and self.num_kv_heads is not None:
                    # GQA模型的空KV缓存
                    # 典型形状: [batch_size, seq_len, num_kv_heads, head_dim]
                    if self.model_config:
                        head_dim = self.model_config.hidden_size // self.model_config.num_attention_heads
                        k = torch.zeros((1, 0, self.num_kv_heads, head_dim), dtype=torch.float16, device=self.device)
                        v = torch.zeros((1, 0, self.num_kv_heads, head_dim), dtype=torch.float16, device=self.device)
                    else:
                        # 回退到通用形状
                        k = torch.zeros((1, 0, 1, self.hidden_dim), dtype=torch.float16, device=self.device)
                        v = torch.zeros((1, 0, 1, self.hidden_dim), dtype=torch.float16, device=self.device)
                else:
                    # 标准模型的空KV缓存
                    k = torch.zeros((1, 0, self.hidden_dim), dtype=torch.float16, device=self.device)
                    v = torch.zeros((1, 0, self.hidden_dim), dtype=torch.float16, device=self.device)

            past_key_values.append((k, v))

        return tuple(past_key_values)

    def get_seq_len(self):
        """获取序列长度"""
        for layer_name in self.cache:
            k = self.cache[layer_name]['k']
            if k is not None:
                return k.shape[1]
        return 0

