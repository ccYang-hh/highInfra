import os
os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'

import torch
from tmatrix.components.logging import init_logger
from tmatrix.cache_accelerators.kvshare import KVShareManager

logger = init_logger(__name__)


# UP 15% —— 50%
if __name__ == '__main__':
    # 创建KVShare管理器
    kvshare = KVShareManager(
        model_name_or_path="/home/ai/models/Qwen2-7B",  # 可以替换为其他模型
        embedding_model="/home/ai/models/bge-m3",
        device="cuda:1",
        similarity_threshold=0.85,
        cache_dir="./kv_cache_store"
    )

    # 准备语义相似的测试提示
    test_prompts = [
        "解释一下量子力学的基本原理和核心概念",
        "帮我解释一下量子力学的基本原理和核心概念",
        "请简单介绍一下量子力学的核心概念",
        "量子力学的基础理论和核心概念是什么？",
    ]
    # test_prompts = [
    #     # 1. 基准输入
    #     "请详细阐述一下人工智能的基本原理，包括它的发展历史、核心思想和重要算法，尤其要结合神经网络、深度学习等关键技术，并用实际应用中的案例说明人工智能是如何影响现代社会的。",
    #
    #     # 2. 语序微调，词语替换（应命中）
    #     "请系统讲解人工智能的基本原理，内容包括其发展历程、核心理论、重要算法，特别要结合神经网络与深度学习等技术，并举出现实应用案例说明人工智能对现代社会带来的影响。",
    #
    #     # 3. 少量细节增删，大体结构不动（应命中）
    #     "请全面说明人工智能的主要原理，包含其历史演进、关键思想和代表性核心算法，尤其结合深度学习和神经网络这类前沿技术，并通过实际生活中的应用案例描述人工智能改变社会的方式。",
    #
    #     # 4. 极小细节变动，结构完全贴合（应命中）
    #     "详细介绍一下人工智能的基本原理，包括其历史进程、主要思想及代表性算法，重点结合如神经网络、深度学习等方向，并以真实应用实例说明人工智能为何对现代社会有着重要影响。",
    #
    #     # 5. 扩展技术内容，并变更案例方向（降低相似度，不应命中）
    #     "请介绍人工智能领域的常用算法，比如决策树、支持向量机、神经网络和深度学习等，另外叙述人工智能目前在医疗影像、智能客服等行业中典型的应用场景。",
    #
    #     # 6. 内容扩展到AI伦理和社会讨论（相关性进一步下降）
    #     "请你用较长篇幅讨论人工智能技术的崛起，以及它在社会伦理、隐私保护和就业形态方面引发的挑战，并适当评述AI未来的发展趋势。",
    #
    #     # 7. 完全无关
    #     "能否写一篇关于健康生活方式的长文，包括合理饮食、科学锻炼和心理调适等方面的实际建议，适合年轻职场人群。",
    # ]

    # 测试每个提示
    results = []
    for i, prompt in enumerate(test_prompts):
        logger.info(f"\n\n--- Testing prompt {i+1}/{len(test_prompts)} ---")
        logger.info(f"Prompt: {prompt}")

        result = kvshare.generate(prompt, max_new_tokens=100)

        logger.info(f"Output: {result['output']}")
        logger.info(f"Performance: {result['performance']}")

        results.append({
            "prompt": prompt,
            "output": result["output"],
            "performance": result["performance"]
        })

    # 显示结果汇总
    print("\n\n--- Results Summary ---")
    total_time = 0
    cache_hits = 0
    for i, result in enumerate(results):
        perf = result["performance"]
        total_time += perf["total_time"]
        if perf["used_cached_kv"]:
            cache_hits += 1
        print(f"{i+1}. {'[CACHE HIT]' if perf['used_cached_kv'] else '[CACHE MISS]'} Time: {perf['total_time']:.2f}s")

    # 显示缓存命中率和平均生成时间
    print(f"\nCache hit rate: {cache_hits/len(results)*100:.1f}%")
    print(f"Average time per prompt: {total_time/len(results):.2f}s")