import os
import time
import torch
from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM
from sentence_transformers import SentenceTransformer
import faiss
import pickle

from tmatrix.components.logging import init_logger
logger = init_logger(__name__)

from .delta_tree import DeltaOperation, DeltaTree
from .kv_editor import KVCache, GQAKVCache


class KVShareManager:
    """KVShare系统的主要管理类"""
    def __init__(self,
                 model_name_or_path="Qwen/Qwen2-7B",
                 embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                 device="cuda" if torch.cuda.is_available() else "cpu",
                 similarity_threshold=0.85,
                 max_cache_items=1000,
                 cache_dir="./kv_cache_store"):

        self.device = device
        self.similarity_threshold = similarity_threshold
        self.max_cache_items = max_cache_items
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        # 初始化模型
        logger.info(f"Loading model {model_name_or_path}...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)
        self.model = AutoModelForCausalLM.from_pretrained(model_name_or_path, torch_dtype=torch.float16).to(device)
        self.model_config = self.model.config  # 保存模型配置

        # 检测模型是否使用GQA
        is_gqa_model = hasattr(self.model_config, "num_key_value_heads") and self.model_config.num_key_value_heads < self.model_config.num_attention_heads
        if is_gqa_model:
            logger.info(f"检测到GQA模型: 注意力头={self.model_config.num_attention_heads}, KV头={self.model_config.num_key_value_heads}")

        # 获取模型隐藏维度 - 适配GQA模型
        if is_gqa_model:
            # GQA模型中，隐藏维度是每个头的维度
            self.hidden_dim = self.model_config.hidden_size // self.model_config.num_attention_heads
        else:
            # 标准模型
            self.hidden_dim = self.model_config.hidden_size

        self.num_layers = self.model_config.num_hidden_layers

        # 初始化嵌入模型
        logger.info(f"Loading embedding model {embedding_model}...")
        self.embedding_model = SentenceTransformer(embedding_model).to(device)

        # 初始化向量索引
        self.index = None
        self.kv_cache_mapping = {}  # 将向量索引ID映射到KV缓存ID
        self.cache_access_time = {}  # 缓存访问时间，用于LRU替换

        # 获取模型隐藏维度和层数
        # self.hidden_dim = self.model.config.hidden_size // self.model.config.num_attention_heads
        # self.num_layers = self.model.config.num_hidden_layers

        # 初始化DELTA Tree
        self.delta_tree = DeltaTree()

        # 加载现有缓存(如果有)
        self._load_cache()

        # 缓存计数器
        self.cache_hit = 0
        self.cache_miss = 0

    def _load_cache(self):
        """加载缓存向量索引和KV缓存映射"""
        index_path = os.path.join(self.cache_dir, "faiss_index.bin")
        mapping_path = os.path.join(self.cache_dir, "kv_cache_mapping.pkl")

        if os.path.exists(index_path) and os.path.exists(mapping_path):
            try:
                logger.info("Loading existing cache...")
                self.index = faiss.read_index(index_path)
                with open(mapping_path, 'rb') as f:
                    cache_data = pickle.load(f)
                    self.kv_cache_mapping = cache_data.get("mapping", {})
                    self.cache_access_time = cache_data.get("access_time", {})
                logger.info(f"Loaded {self.index.ntotal} cached examples")
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
                self._init_new_index()
        else:
            self._init_new_index()

    def _init_new_index(self):
        """初始化新的FAISS索引"""
        logger.info("Initializing new vector index...")
        embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
        self.index = faiss.IndexFlatIP(embedding_dim)  # 使用内积相似度
        self.kv_cache_mapping = {}
        self.cache_access_time = {}

    def _save_cache(self):
        """保存缓存向量索引和KV缓存映射"""
        index_path = os.path.join(self.cache_dir, "faiss_index.bin")
        mapping_path = os.path.join(self.cache_dir, "kv_cache_mapping.pkl")

        try:
            faiss.write_index(self.index, index_path)
            with open(mapping_path, 'wb') as f:
                cache_data = {
                    "mapping": self.kv_cache_mapping,
                    "access_time": self.cache_access_time
                }
                pickle.dump(cache_data, f)
            logger.info(f"Saved {self.index.ntotal} examples to cache")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

    def _embed_text(self, text):
        """将文本转换为嵌入向量"""
        embedding = self.embedding_model.encode(text, convert_to_numpy=True)
        return embedding.reshape(1, -1)  # 确保是2D数组

    def _find_similar_prompts(self, embedding, top_k=5):
        """查找相似的提示文本"""
        if self.index.ntotal == 0:
            return []

        # 搜索最相似的向量
        scores, indices = self.index.search(embedding, min(top_k, self.index.ntotal))
        logger.info(f"当前请求与历史请求的相似性得分是：{scores.tolist()}")

        similar_prompts = []
        for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if score >= self.similarity_threshold:
                cache_id = self.kv_cache_mapping.get(int(idx))
                if cache_id:
                    cache_path = os.path.join(self.cache_dir, f"{cache_id}.pkl")
                    if os.path.exists(cache_path):
                        # 更新访问时间
                        self.cache_access_time[cache_id] = time.time()

                        with open(cache_path, 'rb') as f:
                            cache_data = pickle.load(f)

                        similar_prompts.append({
                            "id": int(idx),
                            "cache_id": cache_id,
                            "prompt": cache_data["prompt"],
                            "tokenized": cache_data["tokenized"],
                            "score": float(score)
                        })

        return similar_prompts

    def _select_best_candidate(self, tokenized_source, candidates):
        """选择最佳的相似请求候选"""
        if not candidates:
            return None

        best_score = -float('inf')
        best_candidate = None

        for candidate in candidates:
            tokenized_target = candidate["tokenized"]

            # 计算编辑操作
            source_tokens = [self.tokenizer.decode([t]) for t in tokenized_source]
            target_tokens = [self.tokenizer.decode([t]) for t in tokenized_target]

            edit_operations = self.delta_tree.compute_edit_operations(source_tokens, target_tokens)

            # 计算编辑得分 (编辑操作越少越好)
            non_keep_ops = sum(1 for op in edit_operations if op.op_type != 'keep')
            edit_score = 1.0 - (non_keep_ops / max(len(source_tokens), 1))

            # 综合考虑语义相似度和编辑效率
            combined_score = 0.7 * candidate["score"] + 0.3 * edit_score

            if combined_score > best_score:
                best_score = combined_score
                best_candidate = candidate

        return best_candidate

    def _evict_cache_if_needed(self):
        """如果缓存超过最大大小，则驱逐最旧的项目"""
        if self.index.ntotal >= self.max_cache_items:
            # 使用LRU策略
            oldest_time = float('inf')
            oldest_cache_id = None

            for cache_id, access_time in self.cache_access_time.items():
                if access_time < oldest_time:
                    oldest_time = access_time
                    oldest_cache_id = cache_id

            if oldest_cache_id:
                # 找到对应的索引ID
                index_id = None
                for idx, cid in self.kv_cache_mapping.items():
                    if cid == oldest_cache_id:
                        index_id = idx
                        break

                if index_id is not None:
                    # 从映射中移除
                    del self.kv_cache_mapping[index_id]

                    # 从访问时间中移除
                    if oldest_cache_id in self.cache_access_time:
                        del self.cache_access_time[oldest_cache_id]

                    # 删除缓存文件
                    cache_path = os.path.join(self.cache_dir, f"{oldest_cache_id}.pkl")
                    if os.path.exists(cache_path):
                        os.remove(cache_path)

                    logger.info(f"Evicted cache item {oldest_cache_id}")

                    # 我们不能直接从FAISS索引中删除项目，这需要重建索引
                    # 这里我们选择保留索引，但使其映射无效
                    # 在实际应用中，可能需要定期重建索引

    def generate(self, prompt, max_new_tokens=50, temperature=0.7, top_p=0.9):
        """生成文本响应"""
        start_time = time.time()

        # 1. 将输入文本转换为token ID
        input_ids = self.tokenizer.encode(prompt, return_tensors='pt').to(self.device)
        tokenized_prompt = input_ids[0].tolist()

        # 2. 计算输入文本的嵌入向量
        prompt_embedding = self._embed_text(prompt)

        # 3. 查找相似的历史提示
        similar_prompts = self._find_similar_prompts(prompt_embedding)
        logger.info(f"找到 {len(similar_prompts)} 个相似输入")

        # 4. 选择最佳候选
        best_candidate = self._select_best_candidate(tokenized_prompt, similar_prompts)
        if best_candidate is not None:
            best_candidate_prompt, best_candidate_score = best_candidate["prompt"], best_candidate["score"]
            logger.info(f"当前请求的最佳候选序列是: {best_candidate_prompt}, 相似性得分是:  {best_candidate_score}")
        else:
            logger.info(f"找不到与当前请求相似的历史序列！")

        search_time = time.time() - start_time
        kv_cache_editing_time = 0
        generation_time = 0

        # 5. 处理生成
        if best_candidate:
            self.cache_hit += 1
            edit_start_time = time.time()

            # 加载候选的KV缓存
            cache_path = os.path.join(self.cache_dir, f"{best_candidate['cache_id']}.pkl")
            with open(cache_path, 'rb') as f:
                cache_data = pickle.load(f)

            # 获取源和目标token
            source_tokens = best_candidate["tokenized"]
            target_tokens = tokenized_prompt

            # 计算编辑操作
            source_token_texts = [self.tokenizer.decode([t]) for t in source_tokens]
            target_token_texts = [self.tokenizer.decode([t]) for t in target_tokens]

            edit_operations = self.delta_tree.compute_edit_operations(source_token_texts, target_token_texts)
            logger.info(f"delta变更操作: {edit_operations}")

            # 计算非keep操作的比例
            non_keep_ratio = sum(1 for op in edit_operations if op.op_type != 'keep') / len(edit_operations)
            logger.info(f"Non-keep operations 占比: {non_keep_ratio:.2f}")

            # 编辑KV缓存
            kv_cache = GQAKVCache.from_model_output(cache_data["kv_cache"], self.device, self.model_config)

            # 计算真正需要的编辑操作
            filtered_operations = []
            for op in edit_operations:
                if op.op_type != 'keep':  # keep操作保持原样
                    filtered_operations.append(op)

            # 应用编辑操作
            if filtered_operations:
                modified_kv_cache = kv_cache.apply_operations(
                    filtered_operations, target_tokens, self.model, self.tokenizer
                )
            else:
                modified_kv_cache = kv_cache

            kv_cache_editing_time = time.time() - edit_start_time
            gen_start_time = time.time()

            # 6. 使用修改后的KV缓存生成
            output = self.generate_with_kv_cache(
                input_ids, modified_kv_cache, max_new_tokens, temperature, top_p
            )

            generation_time = time.time() - gen_start_time
        else:
            self.cache_miss += 1
            logger.info("No suitable KV cache found, generating from scratch")
            gen_start_time = time.time()

            # 常规生成
            output, kv_cache_tuple = self.generate_and_cache(
                input_ids, max_new_tokens, temperature, top_p
            )

            generation_time = time.time() - gen_start_time

            # 7. 存储生成的KV缓存供后续使用
            self._evict_cache_if_needed()

            cache_id = f"cache_{int(time.time() * 1000)}"
            cache_path = os.path.join(self.cache_dir, f"{cache_id}.pkl")

            with open(cache_path, 'wb') as f:
                pickle.dump({
                    "prompt": prompt,
                    "tokenized": tokenized_prompt,
                    "kv_cache": kv_cache_tuple
                }, f)

            # 存储向量索引
            vector_id = self.index.ntotal
            self.index.add(prompt_embedding)
            self.kv_cache_mapping[vector_id] = cache_id
            self.cache_access_time[cache_id] = time.time()

            # 保存缓存
            self._save_cache()

        total_time = time.time() - start_time

        # 8. 返回结果和性能指标
        return {
            "output": output,
            "performance": {
                "total_time": total_time,
                "search_time": search_time,
                "kv_cache_editing_time": kv_cache_editing_time,
                "generation_time": generation_time,
                "used_cached_kv": best_candidate is not None,
                "cache_hit_rate": self.cache_hit / (self.cache_hit + self.cache_miss) if (
                                                                                                     self.cache_hit + self.cache_miss) > 0 else 0
            }
        }

    def generate_with_kv_cache(self, input_ids, kv_cache, max_new_tokens=50, temperature=0.7, top_p=0.9):
        """使用预处理的KV缓存生成文本"""
        # 获取KV缓存元组
        past_key_values = kv_cache.to_tuple()

        # 序列长度
        seq_len = kv_cache.get_seq_len()

        # 创建注意力掩码
        attention_mask = torch.ones((input_ids.shape[0], seq_len + input_ids.shape[1]), device=self.device)

        # 计算位置ID
        position_ids = torch.arange(seq_len, seq_len + input_ids.shape[1], dtype=torch.long,
                                    device=self.device).unsqueeze(0)

        # 使用模型生成
        try:
            with torch.no_grad():
                outputs = self.model(
                    input_ids=input_ids,
                    past_key_values=past_key_values,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                    use_cache=True
                )

                # 使用模型生成
                generated_ids = self.model.generate(
                    input_ids=input_ids,
                    past_key_values=outputs.past_key_values,
                    attention_mask=attention_mask,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    do_sample=temperature > 0,
                    pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
                    use_cache=True
                )
        except Exception as e:
            logger.warning(f"Error using KV cache: {e}, falling back to standard generation")
            # 失败后回退到标准生成
            try:
                generated_ids = self.model.generate(
                    input_ids=input_ids,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    do_sample=temperature > 0,
                    pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id
                )
            except Exception as e2:
                logger.error(f"Error in fallback generation: {e2}")
                return f"Error generating text: {e2}"

        # 解码生成的文本
        generated_text = self.tokenizer.decode(generated_ids[0], skip_special_tokens=True)
        return generated_text

    def generate_and_cache(self, input_ids, max_new_tokens=50, temperature=0.7, top_p=0.9):
        """从头生成文本并获取KV缓存"""
        # 首先运行前向传播以获取KV缓存
        with torch.no_grad():
            outputs = self.model(input_ids, use_cache=True)
            past_key_values = outputs.past_key_values

        # 使用模型生成
        with torch.no_grad():
            generated_ids = self.model.generate(
                input_ids=input_ids,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                do_sample=temperature > 0,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
                use_cache=True,
                return_dict_in_generate=True,
                output_scores=False
            )

        # 解码生成的文本
        generated_text = self.tokenizer.decode(generated_ids.sequences[0], skip_special_tokens=True)

        return generated_text, past_key_values
