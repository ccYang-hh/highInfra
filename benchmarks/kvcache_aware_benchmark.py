import asyncio
import aiohttp
import json
import time
import random
import numpy as np
from typing import List, Dict, Any
from dataclasses import dataclass
import argparse
from transformers import AutoTokenizer


@dataclass
class TestRequest:
    prompt: str
    prompt_len: int
    expected_output_len: int


@dataclass
class RequestResult:
    success: bool
    prompt_len: int
    output_tokens: int
    ttft: float  # Time to first token
    latency: float  # Total latency
    generated_text: str
    error: str = None
    itl: List[float] = None  # Inter-token latencies
    request_id: int = None


class CustomBenchmark:
    def __init__(self, model_name: str = "qwen3", max_tokens: int = 512):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2-7B", trust_remote_code=True)

    def generate_all_test_prompts(self, total_count: int, target_length: int = 512) -> List[TestRequest]:
        """生成指定总数量的测试prompt，每个都完全不同"""
        prompts = []

        # 扩展的基础prompt模板，确保有足够的多样性
        base_prompts = [
            "请详细分析人工智能在医疗领域的应用前景，包括但不限于诊断辅助、药物研发、个性化治疗等方面。",
            "描述区块链技术的核心原理，并分析其在金融、供应链管理、数字身份验证等领域的具体应用案例。",
            "解释量子计算的基本概念，探讨其相比传统计算的优势，以及在密码学、优化问题求解等方面的潜在影响。",
            "分析气候变化对全球经济的影响，包括对农业、能源、保险等行业的具体冲击和适应策略。",
            "探讨元宇宙概念的技术实现路径，包括VR/AR技术、区块链、云计算等技术的融合应用。",
            "解释机器学习中深度学习的工作原理，分析其在图像识别、自然语言处理、推荐系统等领域的应用。",
            "描述5G技术的技术特点，分析其对物联网、自动驾驶、远程医疗等应用场景的推动作用。",
            "探讨可再生能源的发展趋势，包括太阳能、风能、储能技术的技术进步和成本下降趋势。",
            "分析大数据技术在智慧城市建设中的应用，包括交通优化、环境监测、公共安全等方面。",
            "解释边缘计算的技术原理，分析其在工业互联网、自动驾驶、智能家居等场景中的应用价值。",
            "讨论人工智能伦理的重要性，包括算法偏见、隐私保护、就业影响等关键议题。",
            "分析云计算的发展历程，探讨其对企业数字化转型的推动作用和未来发展趋势。",
            "解释物联网技术的核心架构，分析其在智能制造、智慧农业等领域的应用潜力。",
            "探讨数字货币的技术原理，分析其对传统金融体系的影响和监管挑战。",
            "描述自动驾驶技术的发展现状，分析技术瓶颈、安全挑战和商业化前景。",
            "分析网络安全威胁的演变趋势，探讨防护策略和技术解决方案的发展方向。",
            "解释生物信息学的基本概念，分析其在精准医疗、药物发现等领域的应用价值。",
            "探讨虚拟现实技术的发展现状，分析其在教育、娱乐、医疗等领域的应用前景。",
            "分析工业4.0的核心技术要素，探讨其对制造业转型升级的推动作用。",
            "解释神经网络的基本原理，分析深度学习在各个领域的突破性应用。",
            "探讨智能机器人的发展现状，分析其在服务业、制造业中的应用前景和技术挑战。",
            "分析金融科技的创新趋势，探讨数字支付、智能投顾、风险管理等领域的技术变革。",
            "解释计算机视觉的核心技术，分析其在安防监控、医学影像、自动驾驶等领域的应用。",
            "探讨语音识别技术的发展历程，分析其在智能助手、客服系统中的应用价值。",
            "分析智慧交通系统的技术架构，探讨其对城市交通效率提升的作用和实施挑战。",
            "解释分布式系统的基本概念，分析其在大规模互联网应用中的重要性和技术难点。",
            "探讨人机交互技术的演进方向，分析触控、语音、手势等交互方式的发展趋势。",
            "分析数据挖掘技术的应用场景，探讨其在商业智能、推荐系统中的价值和方法。",
            "解释云原生技术的核心理念，分析容器、微服务架构对软件开发的影响。",
            "探讨增强现实技术的实现原理，分析其在工业培训、医疗教育中的应用潜力。",
            "分析自然语言处理的技术发展，探讨机器翻译、文本生成等应用的突破和挑战。",
            "解释区块链在供应链中的应用模式，分析其对产品溯源、防伪验证的价值。",
            "探讨智能制造的技术体系，分析工业物联网、数字孪生等技术的融合应用。",
            "分析移动互联网的发展趋势，探讨5G时代的新应用场景和商业模式。",
            "解释知识图谱的构建方法，分析其在搜索引擎、智能问答中的应用价值。",
            "探讨量子通信的技术原理，分析其在信息安全、国防通信中的战略意义。",
            "分析边缘AI的技术特点，探讨其在智能终端、实时决策中的应用优势。",
            "解释DevOps文化的核心理念，分析其对软件开发流程和团队协作的改进作用。",
            "探讨数字孪生技术的实现方法，分析其在制造业、城市规划中的应用前景。",
            "分析低代码开发平台的技术架构，探讨其对软件开发效率和门槛的影响。",
            "解释联邦学习的基本原理，分析其在隐私保护和数据共享中的技术价值。",
            "探讨脑机接口技术的发展现状，分析其在医疗康复、人机融合中的应用潜力。",
            "分析时间序列分析的方法体系，探讨其在金融预测、设备维护中的应用价值。",
            "解释容器编排技术的核心概念，分析Kubernetes等平台对云计算的推动作用。",
            "探讨智能客服系统的技术实现，分析对话AI、情感计算等技术的融合应用。",
            "分析图神经网络的技术原理，探讨其在社交网络分析、推荐系统中的应用。",
            "解释零信任安全架构的设计理念，分析其对企业网络安全防护的革新意义。",
            "探讨多模态AI的技术发展，分析视觉、语音、文本等模态融合的应用价值。",
            "分析流计算技术的核心特点，探讨其在实时数据处理、监控预警中的应用。",
            "解释AutoML的技术原理，分析其对机器学习模型开发和部署的简化作用。",
            "探讨数字化转型的技术路径，分析云计算、大数据、AI等技术的协同价值。",
            "分析图像生成技术的发展历程，探讨GAN、扩散模型等技术的创新突破。",
            "解释推荐算法的技术演进，分析协同过滤、深度学习等方法的优缺点。",
            "探讨智能运维的技术体系，分析AIOps在故障预测、自动化运维中的应用。",
            "分析代码生成AI的技术原理，探讨其对软件开发效率和质量的影响。",
            "解释分布式存储的技术架构，分析其在大数据处理、云存储中的关键作用。",
            "探讨混合现实技术的实现方法，分析其在工业设计、远程协作中的应用价值。",
            "分析强化学习的算法原理，探讨其在游戏AI、机器人控制中的突破性应用。",
            "解释微服务架构的设计模式，分析其对大型系统开发和维护的优化作用。",
            "探讨量子机器学习的理论基础，分析其相比经典机器学习的潜在优势。",
            "分析隐私计算的技术方案，探讨同态加密、安全多方计算等技术的应用前景。"
        ]

        # 不同的技术角度和场景
        perspectives = [
            "从技术实现角度", "从商业应用角度", "从社会影响角度", "从发展趋势角度",
            "从安全风险角度", "从成本效益角度", "从用户体验角度", "从监管政策角度",
            "从国际合作角度", "从创新突破角度", "从产业生态角度", "从人才培养角度"
        ]

        # 不同的分析维度
        dimensions = [
            "技术成熟度", "市场接受度", "投资回报率", "竞争优势", "可持续发展",
            "标准化程度", "生态建设", "人才需求", "基础设施", "政策支持",
            "国际竞争", "创新能力", "应用深度", "推广难度", "社会效益"
        ]

        for i in range(total_count):
            # 选择不同的基础prompt
            base_prompt = base_prompts[i % len(base_prompts)]

            # 选择不同的分析角度和维度
            perspective = perspectives[i % len(perspectives)]
            dimension = dimensions[i % len(dimensions)]

            # 为每个请求生成独特的上下文
            unique_context = f"""
背景信息：这是第{i + 1}个独特的测试问题。请{perspective}进行深入分析。
重点关注：{dimension}以及相关的技术细节和实际案例。
分析要求：请从多个维度进行全面阐述，确保回答具有足够的深度和广度。
特别说明：本问题编号为{i + 1}，请结合当前技术发展现状和市场环境进行分析。
评估标准：技术可行性、商业价值、实施难度、风险评估、未来发展潜力等。
            """

            # 添加独特的技术要求
            tech_requirements = f"""
技术深度要求：请详细说明核心技术原理、关键技术挑战、解决方案对比。
应用场景分析：请举例说明具体的应用场景、成功案例、失败教训。
发展趋势预测：请分析未来3-5年的发展趋势、技术演进路径、市场机遇。
风险挑战评估：请识别主要风险点、技术瓶颈、监管挑战、竞争威胁。
实施建议：请提供具体的实施路径、资源配置、时间规划、成功关键要素。
"""

            full_prompt = base_prompt + unique_context + tech_requirements

            # 计算token长度并调整到目标长度
            tokens = self.tokenizer.encode(full_prompt)
            current_length = len(tokens)

            # 如果长度不够，继续添加内容
            if current_length < target_length:
                padding_needed = target_length - current_length
                additional_padding = f"补充要求{i + 1}：请从系统性思维出发，结合理论分析和实践验证，提供具有指导意义的深度分析。" * (
                            padding_needed // 30 + 1)
                full_prompt += additional_padding
                tokens = self.tokenizer.encode(full_prompt)
                current_length = len(tokens)

            # 如果长度超出，进行截断
            if current_length > target_length:
                tokens = tokens[:target_length]
                full_prompt = self.tokenizer.decode(tokens)
                current_length = target_length

            prompts.append(TestRequest(
                prompt=full_prompt,
                prompt_len=current_length,
                expected_output_len=self.max_tokens
            ))

        return prompts

    def get_prompt_preview(self, prompt: str, chars_count: int = 60) -> str:
        """获取prompt的前N个字符作为预览"""
        if len(prompt) <= chars_count:
            return prompt
        return prompt[:chars_count] + "..."

    async def send_request(self, session: aiohttp.ClientSession, host: str, port: int,
                           request: TestRequest, request_id: int = None,
                           log_request: bool = False) -> RequestResult:
        """发送单个请求到指定的服务"""
        url = f"http://{host}:{port}/v1/completions"

        if log_request:
            preview = self.get_prompt_preview(request.prompt, 60)
            print(f"发起请求 #{request_id}, 输入长度：{request.prompt_len}, 内容：{preview}")

        payload = {
            "model": self.model_name,
            "prompt": request.prompt,
            "max_tokens": request.expected_output_len,
            "stream": True,
            "temperature": 0.0
        }

        headers = {
            "Content-Type": "application/json"
        }

        start_time = time.perf_counter()
        ttft = None
        generated_text = ""
        itl_times = []
        last_token_time = start_time
        error_msg = None

        try:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_msg = f"HTTP {response.status}: {await response.text()}"
                    if log_request:
                        print(f"请求 #{request_id} 失败: {error_msg}")
                    return RequestResult(
                        success=False,
                        prompt_len=request.prompt_len,
                        output_tokens=0,
                        ttft=0,
                        latency=0,
                        generated_text="",
                        error=error_msg,
                        request_id=request_id
                    )

                token_count = 0
                async for line in response.content:
                    if line:
                        line = line.decode('utf-8').strip()
                        if line.startswith('data: '):
                            data_str = line[6:]  # Remove 'data: ' prefix
                            if data_str == '[DONE]':
                                break

                            try:
                                data = json.loads(data_str)
                                if 'choices' in data and len(data['choices']) > 0:
                                    delta = data['choices'][0].get('text', '')
                                    if delta:
                                        current_time = time.perf_counter()
                                        if ttft is None:
                                            ttft = current_time - start_time
                                            if log_request:
                                                print(f"请求 #{request_id} 首token时间: {ttft * 1000:.2f}ms")
                                        else:
                                            itl_times.append(current_time - last_token_time)

                                        generated_text += delta
                                        token_count += 1
                                        last_token_time = current_time
                            except json.JSONDecodeError as e:
                                if log_request:
                                    print(f"请求 #{request_id} JSON解析错误: {e}, 数据: {data_str[:100]}")
                                continue

            end_time = time.perf_counter()
            total_latency = end_time - start_time

            # 计算输出token数量
            output_tokens = len(self.tokenizer.encode(generated_text)) if generated_text else 0

            if log_request:
                print(f"请求 #{request_id} 完成: 生成{output_tokens}个token, 总延迟{total_latency * 1000:.2f}ms")

            return RequestResult(
                success=True,
                prompt_len=request.prompt_len,
                output_tokens=output_tokens,
                ttft=ttft or 0,
                latency=total_latency,
                generated_text=generated_text,
                itl=itl_times,
                request_id=request_id
            )

        except asyncio.TimeoutError:
            error_msg = "请求超时"
            if log_request:
                print(f"请求 #{request_id} 失败: {error_msg}")
        except Exception as e:
            error_msg = f"异常: {str(e)}"
            if log_request:
                print(f"请求 #{request_id} 失败: {error_msg}")

        return RequestResult(
            success=False,
            prompt_len=request.prompt_len,
            output_tokens=0,
            ttft=0,
            latency=0,
            generated_text="",
            error=error_msg,
            request_id=request_id
        )

    async def test_multiple_services(self, hosts_ports: List[tuple], all_requests: List[TestRequest],
                                     requests_per_service: int = 20):
        """测试多个服务，每个服务使用不同的请求子集"""
        all_results = []

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            request_start_idx = 0

            for service_idx, (host, port) in enumerate(hosts_ports):
                print(f"\n{'=' * 60}")
                print(f"Testing service {host}:{port} (Service {service_idx + 1}/3)")
                print(f"{'=' * 60}")

                # 为每个服务分配不同的请求子集
                start_idx = service_idx * requests_per_service
                end_idx = start_idx + requests_per_service
                service_requests = all_requests[start_idx:end_idx]

                print(f"分配请求 #{start_idx + 1} 到 #{end_idx} 给服务 {host}:{port}")

                # 并发发送请求，但添加详细日志
                tasks = []
                for i, req in enumerate(service_requests):
                    request_id = start_idx + i + 1
                    task = self.send_request(session, host, port, req,
                                             request_id=request_id, log_request=True)
                    tasks.append(task)

                results = await asyncio.gather(*tasks)
                all_results.extend(results)

                # 打印当前服务的详细统计
                successful = sum(1 for r in results if r.success)
                failed = len(results) - successful
                print(f"\nService {host}:{port} 结果总结:")
                print(f"  成功: {successful}/{len(results)}")
                print(f"  失败: {failed}/{len(results)}")

                if failed > 0:
                    print("  失败原因统计:")
                    error_counts = {}
                    for r in results:
                        if not r.success:
                            error = r.error or "未知错误"
                            error_counts[error] = error_counts.get(error, 0) + 1
                    for error, count in error_counts.items():
                        print(f"    {error}: {count}次")

        return all_results

    async def benchmark_target_service(self, host: str, port: int, requests: List[TestRequest],
                                       max_concurrency: int = 30):
        """对目标服务进行基准测试，支持并发控制"""
        print(f"\n{'=' * 80}")
        print(f"Starting benchmark for target service {host}:{port}...")
        print(f"Total requests: {len(requests)}")
        print(f"Max concurrency: {max_concurrency}")
        print(f"{'=' * 80}")

        # 创建信号量控制并发数
        semaphore = asyncio.Semaphore(max_concurrency)

        async def limited_request(session, req, req_id):
            async with semaphore:
                return await self.send_request(session, host, port, req,
                                               request_id=req_id, log_request=True)

        start_time = time.perf_counter()

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            # 创建所有任务
            tasks = []
            for i, req in enumerate(requests):
                task = limited_request(session, req, i + 1)
                tasks.append(task)

            print(f"开始发送请求，最大并发数: {max_concurrency}...")
            results = await asyncio.gather(*tasks)

        end_time = time.perf_counter()
        total_duration = end_time - start_time

        # 详细分析失败原因
        successful_results = [r for r in results if r.success]
        failed_results = [r for r in results if not r.success]

        print(f"\n{'=' * 60}")
        print("请求结果详细分析:")
        print(f"{'=' * 60}")
        print(f"总请求数: {len(results)}")
        print(f"成功请求: {len(successful_results)}")
        print(f"失败请求: {len(failed_results)}")
        print(f"成功率: {len(successful_results) / len(results) * 100:.2f}%")

        if failed_results:
            print(f"\n失败请求详情:")
            error_counts = {}
            for r in failed_results:
                error = r.error or "未知错误"
                error_counts[error] = error_counts.get(error, 0) + 1
                print(f"  请求 #{r.request_id}: {error}")

            print(f"\n失败原因统计:")
            for error, count in error_counts.items():
                print(f"  {error}: {count}次")

        # 计算性能指标
        metrics = self.calculate_metrics(requests, results, total_duration)

        return metrics, results

    def calculate_metrics(self, requests: List[TestRequest], results: List[RequestResult], duration: float) -> Dict[
        str, Any]:
        """计算性能指标，参考vllm的实现"""
        successful_results = [r for r in results if r.success]

        if not successful_results:
            return {"error": "No successful requests"}

        # 基础统计
        completed = len(successful_results)
        total_input_tokens = sum(r.prompt_len for r in successful_results)
        total_output_tokens = sum(r.output_tokens for r in successful_results)

        # 吞吐量计算
        request_throughput = completed / duration
        output_throughput = total_output_tokens / duration
        total_token_throughput = (total_input_tokens + total_output_tokens) / duration

        # 延迟统计
        ttfts = [r.ttft for r in successful_results if r.ttft > 0]
        latencies = [r.latency for r in successful_results]

        # ITL统计
        all_itls = []
        for r in successful_results:
            if r.itl:
                all_itls.extend(r.itl)

        metrics = {
            "duration": duration,
            "completed": completed,
            "total_requests": len(results),
            "success_rate": completed / len(results),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "request_throughput": request_throughput,
            "output_throughput": output_throughput,
            "total_token_throughput": total_token_throughput,
        }

        # TTFT统计
        if ttfts:
            metrics.update({
                "mean_ttft_ms": np.mean(ttfts) * 1000,
                "median_ttft_ms": np.median(ttfts) * 1000,
                "std_ttft_ms": np.std(ttfts) * 1000,
                "p95_ttft_ms": np.percentile(ttfts, 95) * 1000,
                "p99_ttft_ms": np.percentile(ttfts, 99) * 1000,
            })

        # 端到端延迟统计
        if latencies:
            metrics.update({
                "mean_e2el_ms": np.mean(latencies) * 1000,
                "median_e2el_ms": np.median(latencies) * 1000,
                "std_e2el_ms": np.std(latencies) * 1000,
                "p95_e2el_ms": np.percentile(latencies, 95) * 1000,
                "p99_e2el_ms": np.percentile(latencies, 99) * 1000,
            })

        # ITL统计
        if all_itls:
            metrics.update({
                "mean_itl_ms": np.mean(all_itls) * 1000,
                "median_itl_ms": np.median(all_itls) * 1000,
                "std_itl_ms": np.std(all_itls) * 1000,
                "p95_itl_ms": np.percentile(all_itls, 95) * 1000,
                "p99_itl_ms": np.percentile(all_itls, 99) * 1000,
            })

        return metrics

    def print_metrics(self, metrics: Dict[str, Any]):
        """打印性能指标，参考vllm的格式"""
        print("\n" + "=" * 50)
        print(" Serving Benchmark Result ".center(50, "="))
        print("=" * 50)

        print(f"{'Successful requests:':<40} {metrics.get('completed', 0):<10}")
        print(f"{'Total requests:':<40} {metrics.get('total_requests', 0):<10}")
        print(f"{'Success rate:':<40} {metrics.get('success_rate', 0):<10.2%}")
        print(f"{'Benchmark duration (s):':<40} {metrics.get('duration', 0):<10.2f}")
        print(f"{'Total input tokens:':<40} {metrics.get('total_input_tokens', 0):<10}")
        print(f"{'Total generated tokens:':<40} {metrics.get('total_output_tokens', 0):<10}")
        print(f"{'Request throughput (req/s):':<40} {metrics.get('request_throughput', 0):<10.2f}")
        print(f"{'Output token throughput (tok/s):':<40} {metrics.get('output_throughput', 0):<10.2f}")
        print(f"{'Total Token throughput (tok/s):':<40} {metrics.get('total_token_throughput', 0):<10.2f}")

        if 'mean_ttft_ms' in metrics:
            print("-" * 50)
            print(" Time to First Token ".center(50, "-"))
            print("-" * 50)
            print(f"{'Mean TTFT (ms):':<40} {metrics['mean_ttft_ms']:<10.2f}")
            print(f"{'Median TTFT (ms):':<40} {metrics['median_ttft_ms']:<10.2f}")
            print(f"{'P95 TTFT (ms):':<40} {metrics['p95_ttft_ms']:<10.2f}")
            print(f"{'P99 TTFT (ms):':<40} {metrics['p99_ttft_ms']:<10.2f}")

        if 'mean_e2el_ms' in metrics:
            print("-" * 50)
            print(" End-to-end Latency ".center(50, "-"))
            print("-" * 50)
            print(f"{'Mean E2EL (ms):':<40} {metrics['mean_e2el_ms']:<10.2f}")
            print(f"{'Median E2EL (ms):':<40} {metrics['median_e2el_ms']:<10.2f}")
            print(f"{'P95 E2EL (ms):':<40} {metrics['p95_e2el_ms']:<10.2f}")
            print(f"{'P99 E2EL (ms):':<40} {metrics['p99_e2el_ms']:<10.2f}")

        if 'mean_itl_ms' in metrics:
            print("-" * 50)
            print(" Inter-token Latency ".center(50, "-"))
            print("-" * 50)
            print(f"{'Mean ITL (ms):':<40} {metrics['mean_itl_ms']:<10.2f}")
            print(f"{'Median ITL (ms):':<40} {metrics['median_itl_ms']:<10.2f}")
            print(f"{'P95 ITL (ms):':<40} {metrics['p95_itl_ms']:<10.2f}")
            print(f"{'P99 ITL (ms):':<40} {metrics['p99_itl_ms']:<10.2f}")

        print("=" * 50)


async def main():
    parser = argparse.ArgumentParser(description="Custom benchmark for inference services")
    parser.add_argument("--model-name", type=str, default="qwen3", help="Model name")
    parser.add_argument("--max-tokens", type=int, default=512, help="Maximum output tokens")
    parser.add_argument("--requests-per-service", type=int, default=20, help="Requests per service")
    parser.add_argument("--target-host", type=str, default="70.182.43.96", help="Target service host")
    parser.add_argument("--target-port", type=int, default=8080, help="Target service port")
    parser.add_argument("--max-concurrency", type=int, default=30, help="Maximum concurrent requests to target service")
    parser.add_argument("--save-result", action="store_true", help="Save result to file")
    parser.add_argument("--result-file", type=str, default="benchmark_result.json", help="Result file name")

    args = parser.parse_args()

    # 初始化benchmark
    benchmark = CustomBenchmark(args.model_name, args.max_tokens)

    # 第一阶段：生成所有不同的请求
    total_requests = args.requests_per_service * 3  # 60个请求
    service_hosts_ports = [
        ("70.182.43.96", 8100),
        ("70.182.43.96", 8200),
        ("70.182.43.96", 8300),
    ]

    print("=" * 80)
    print("Phase 1: 生成测试请求并分配到各个服务...")
    print("=" * 80)
    print(f"总共生成 {total_requests} 个完全不同的请求")
    print(f"每个服务分配 {args.requests_per_service} 个请求")

    # 生成所有请求
    all_requests = benchmark.generate_all_test_prompts(total_requests)

    # 测试三个服务实例（每个服务使用不同的请求子集）
    initial_results = await benchmark.test_multiple_services(
        service_hosts_ports,
        all_requests,
        args.requests_per_service
    )

    print(f"\n{'=' * 80}")
    print(f"Phase 1 完成 - 向3个服务发送了 {total_requests} 个不同的请求")
    print(f"{'=' * 80}")

    # 第二阶段：将所有请求随机打乱后发送到目标服务
    print("\nPhase 2: 随机打乱请求顺序并向目标服务发送...")

    # 创建一个包含原始索引信息的请求列表副本，然后随机打乱
    randomized_requests = all_requests.copy()
    random.shuffle(randomized_requests)

    print(f"已将 {len(randomized_requests)} 个请求随机打乱顺序")

    metrics, target_results = await benchmark.benchmark_target_service(
        args.target_host,
        args.target_port,
        randomized_requests,
        args.max_concurrency
    )

    # 打印结果
    benchmark.print_metrics(metrics)

    # 保存结果
    if args.save_result:
        result_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "config": {
                "model_name": args.model_name,
                "max_tokens": args.max_tokens,
                "requests_per_service": args.requests_per_service,
                "max_concurrency": args.max_concurrency,
                "target_service": f"{args.target_host}:{args.target_port}",
                "initial_services": [f"{host}:{port}" for host, port in service_hosts_ports],
                "requests_randomized": True
            },
            "metrics": metrics,
            "total_requests": len(all_requests),
            "successful_requests": metrics.get("completed", 0),
            "detailed_results": [
                {
                    "request_id": r.request_id,
                    "success": r.success,
                    "prompt_len": r.prompt_len,
                    "output_tokens": r.output_tokens,
                    "ttft_ms": r.ttft * 1000 if r.ttft else 0,
                    "latency_ms": r.latency * 1000 if r.latency else 0,
                    "error": r.error
                }
                for r in target_results
            ]
        }

        with open(args.result_file, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)

        print(f"\nResults saved to {args.result_file}")


if __name__ == "__main__":
    asyncio.run(main())
