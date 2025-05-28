import asyncio
import aiohttp
import json
import time
import uuid
import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import argparse
from transformers import AutoTokenizer


@dataclass
class TurnResult:
    turn_number: int
    success: bool
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    ttft: float  # Time to first token
    latency: float  # Total latency
    generated_text: str
    error: str = None
    itl: List[float] = None  # Inter-token latencies


@dataclass
class ConversationResult:
    chat_id: str
    conversation_index: int
    total_turns: int
    successful_turns: int
    total_duration: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    turns: List[TurnResult]
    conversation_success: bool


class MultiTurnChatBenchmark:
    def __init__(self, model_name: str = "qwen3", max_tokens: int = 512):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.tokenizer = AutoTokenizer.from_pretrained("/home/hf_data/Qwen1.5-0.5B", trust_remote_code=True)

        # ========================================
        # 在这里设置您的测试prompt
        # 每一行代表一个完整的多轮对话，包含3轮prompt
        # 如果并发数是N，请提供N行prompt
        # ========================================
        self.conversation_prompts = [
            # 对话1的3轮prompt
            [
                "请介绍一下人工智能的发展历程",
                "那么深度学习相比传统机器学习有什么优势？",
                "未来AI技术的发展趋势是什么？"
            ],
        ]
        # ========================================

    def get_conversation_messages(self, conversation_index: int, turn_number: int,
                                  conversation_history: List[Dict]) -> List[Dict]:
        """构建对话消息列表，符合OpenAI API规范"""
        messages = conversation_history.copy()

        # 检查对话索引和轮次是否有效
        if (conversation_index < len(self.conversation_prompts) and
                turn_number <= len(self.conversation_prompts[conversation_index])):

            prompt = self.conversation_prompts[conversation_index][turn_number - 1]
            if prompt.strip():  # 只有当prompt不为空时才添加
                messages.append({
                    "role": "user",
                    "content": prompt
                })

        return messages

    async def send_chat_request(self, session: aiohttp.ClientSession, host: str, port: int,
                                messages: List[Dict], chat_id: str, conversation_index: int,
                                turn_number: int) -> TurnResult:
        """发送单轮对话请求"""
        url = f"http://{host}:{port}/v1/chat/completions"

        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": True,
            "temperature": 0.0,
            "chat_id": chat_id  # 添加chat_id到请求体
        }

        headers = {
            "Content-Type": "application/json"
        }

        # 计算输入token数
        input_text = ""
        for msg in messages:
            input_text += msg.get("content", "")
        input_tokens = len(self.tokenizer.encode(input_text))

        print(f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: 发起请求，输入tokens: {input_tokens}")

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
                    print(f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: 请求失败 - {error_msg}")
                    return TurnResult(
                        turn_number=turn_number,
                        success=False,
                        prompt_tokens=input_tokens,
                        completion_tokens=0,
                        total_tokens=input_tokens,
                        ttft=0,
                        latency=0,
                        generated_text="",
                        error=error_msg
                    )

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
                                    delta = data['choices'][0].get('delta', {})
                                    content = delta.get('content', '')
                                    if content:
                                        current_time = time.perf_counter()
                                        if ttft is None:
                                            ttft = current_time - start_time
                                            print(
                                                f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: 首token时间 {ttft * 1000:.2f}ms")
                                        else:
                                            itl_times.append(current_time - last_token_time)

                                        generated_text += content
                                        last_token_time = current_time
                            except json.JSONDecodeError as e:
                                print(
                                    f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: JSON解析错误 - {e}")
                                continue

            end_time = time.perf_counter()
            total_latency = end_time - start_time

            # 计算输出token数量
            completion_tokens = len(self.tokenizer.encode(generated_text)) if generated_text else 0
            total_tokens = input_tokens + completion_tokens

            print(
                f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: 完成，生成{completion_tokens}个token，延迟{total_latency * 1000:.2f}ms")

            return TurnResult(
                turn_number=turn_number,
                success=True,
                prompt_tokens=input_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                ttft=ttft or 0,
                latency=total_latency,
                generated_text=generated_text,
                itl=itl_times
            )

        except asyncio.TimeoutError:
            error_msg = "请求超时"
            print(f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: {error_msg}")
        except Exception as e:
            error_msg = f"异常: {str(e)}"
            print(f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn_number}: {error_msg}")

        return TurnResult(
            turn_number=turn_number,
            success=False,
            prompt_tokens=input_tokens,
            completion_tokens=0,
            total_tokens=input_tokens,
            ttft=0,
            latency=0,
            generated_text="",
            error=error_msg
        )

    async def run_single_conversation(self, session: aiohttp.ClientSession, host: str, port: int,
                                      conversation_index: int) -> ConversationResult:
        """运行单个多轮对话"""
        chat_id = str(uuid.uuid4())
        conversation_history = []
        turns = []

        print(f"\n{'=' * 60}")
        print(f"开始对话 #{conversation_index + 1} (ID: {chat_id[:8]})")
        print(f"{'=' * 60}")

        conversation_start = time.perf_counter()

        # 进行三轮对话
        for turn in range(1, 4):  # 1, 2, 3
            # 构建消息列表
            messages = self.get_conversation_messages(conversation_index, turn, conversation_history)

            if not messages:  # 如果没有消息，跳过这轮
                print(f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn}: 跳过（无prompt）")
                continue

            # 发送请求
            turn_result = await self.send_chat_request(session, host, port, messages,
                                                       chat_id, conversation_index, turn)
            turns.append(turn_result)

            if turn_result.success:
                # 将用户消息和助手回复添加到历史记录
                if (conversation_index < len(self.conversation_prompts) and
                        turn <= len(self.conversation_prompts[conversation_index]) and
                        self.conversation_prompts[conversation_index][turn - 1].strip()):
                    conversation_history.append({
                        "role": "user",
                        "content": self.conversation_prompts[conversation_index][turn - 1]
                    })
                conversation_history.append({
                    "role": "assistant",
                    "content": turn_result.generated_text
                })
            else:
                print(f"对话#{conversation_index + 1} {chat_id[:8]} - Turn {turn}: 失败，停止对话")
                break

        conversation_end = time.perf_counter()
        total_duration = conversation_end - conversation_start

        # 统计总体结果
        successful_turns = sum(1 for t in turns if t.success)
        total_prompt_tokens = sum(t.prompt_tokens for t in turns)
        total_completion_tokens = sum(t.completion_tokens for t in turns)
        total_tokens = sum(t.total_tokens for t in turns)
        conversation_success = successful_turns == len(turns) and len(turns) == 3

        result = ConversationResult(
            chat_id=chat_id,
            conversation_index=conversation_index,
            total_turns=len(turns),
            successful_turns=successful_turns,
            total_duration=total_duration,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            turns=turns,
            conversation_success=conversation_success
        )

        print(f"\n对话 #{conversation_index + 1} 完成:")
        print(f"  总轮数: {result.total_turns}")
        print(f"  成功轮数: {result.successful_turns}")
        print(f"  总耗时: {result.total_duration:.2f}s")
        print(f"  总tokens: {result.total_tokens}")
        print(f"  对话成功: {'是' if result.conversation_success else '否'}")

        return result

    async def run_multiple_conversations(self, host: str, port: int, num_conversations: int = 10):
        """并发运行多个对话"""
        print(f"{'=' * 80}")
        print(f"开始并发 {num_conversations} 个多轮对话测试")
        print(f"目标服务: {host}:{port}")
        print(f"{'=' * 80}")

        # 检查prompt配置
        available_conversations = len(self.conversation_prompts)
        if num_conversations > available_conversations:
            print(f"错误：请求 {num_conversations} 个并发对话，但只配置了 {available_conversations} 个对话的prompt")
            print(f"请在代码中为对话 #{available_conversations + 1} 到 #{num_conversations} 添加prompt配置")
            return []

        # 检查每个对话是否有完整的3轮prompt
        for i in range(num_conversations):
            empty_turns = []
            for turn in range(3):
                if turn >= len(self.conversation_prompts[i]) or not self.conversation_prompts[i][turn].strip():
                    empty_turns.append(turn + 1)
            if empty_turns:
                print(f"警告：对话 #{i + 1} 的第 {empty_turns} 轮prompt为空")

        start_time = time.perf_counter()

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            # 创建并发任务
            tasks = [
                self.run_single_conversation(session, host, port, i)
                for i in range(num_conversations)
            ]

            # 并发执行所有对话
            results = await asyncio.gather(*tasks, return_exceptions=True)

        end_time = time.perf_counter()
        total_test_duration = end_time - start_time

        # 过滤出成功的结果
        successful_results = [r for r in results if isinstance(r, ConversationResult)]
        failed_results = [r for r in results if not isinstance(r, ConversationResult)]

        # 计算总体统计
        self.print_overall_statistics(successful_results, failed_results, total_test_duration)

        return successful_results

    def print_overall_statistics(self, results: List[ConversationResult],
                                 failed_results: List, total_duration: float):
        """打印总体统计信息"""
        print(f"\n{'=' * 80}")
        print(" 多轮对话测试总体结果 ".center(80, "="))
        print(f"{'=' * 80}")

        total_conversations = len(results) + len(failed_results)
        successful_conversations = sum(1 for r in results if r.conversation_success)

        print(f"{'总对话数:':<30} {total_conversations}")
        print(f"{'成功对话数:':<30} {successful_conversations}")
        print(f"{'失败对话数:':<30} {total_conversations - len(results)}")
        print(f"{'不完整对话数:':<30} {len(results) - successful_conversations}")
        print(f"{'对话成功率:':<30} {successful_conversations / total_conversations * 100:.2f}%")
        print(f"{'总测试时间:':<30} {total_duration:.2f}s")

        if results:
            # 统计所有轮次的性能
            all_turns = []
            for conv in results:
                all_turns.extend([t for t in conv.turns if t.success])

            if all_turns:
                print(f"\n{'-' * 50}")
                print(" 性能统计 (所有成功轮次) ".center(50, "-"))
                print(f"{'-' * 50}")

                # 基础统计
                total_prompt_tokens = sum(t.prompt_tokens for t in all_turns)
                total_completion_tokens = sum(t.completion_tokens for t in all_turns)
                total_tokens = sum(t.total_tokens for t in all_turns)

                print(f"{'总轮次数:':<30} {len(all_turns)}")
                print(f"{'总输入tokens:':<30} {total_prompt_tokens}")
                print(f"{'总输出tokens:':<30} {total_completion_tokens}")
                print(f"{'总tokens:':<30} {total_tokens}")

                # 吞吐量统计
                total_requests = len(all_turns)
                request_throughput = total_requests / total_duration
                token_throughput = total_tokens / total_duration
                completion_throughput = total_completion_tokens / total_duration

                print(f"{'请求吞吐量 (req/s):':<30} {request_throughput:.2f}")
                print(f"{'总token吞吐量 (tok/s):':<30} {token_throughput:.2f}")
                print(f"{'输出token吞吐量 (tok/s):':<30} {completion_throughput:.2f}")

                # 延迟统计
                ttfts = [t.ttft for t in all_turns if t.ttft > 0]
                latencies = [t.latency for t in all_turns]

                if ttfts:
                    print(f"\n{'-' * 30}")
                    print(" TTFT统计 (ms) ".center(30, "-"))
                    print(f"{'-' * 30}")
                    print(f"{'平均TTFT:':<20} {np.mean(ttfts) * 1000:.2f}")
                    print(f"{'中位数TTFT:':<20} {np.median(ttfts) * 1000:.2f}")
                    print(f"{'P95 TTFT:':<20} {np.percentile(ttfts, 95) * 1000:.2f}")
                    print(f"{'P99 TTFT:':<20} {np.percentile(ttfts, 99) * 1000:.2f}")

                if latencies:
                    print(f"\n{'-' * 30}")
                    print(" 端到端延迟统计 (ms) ".center(30, "-"))
                    print(f"{'-' * 30}")
                    print(f"{'平均延迟:':<20} {np.mean(latencies) * 1000:.2f}")
                    print(f"{'中位数延迟:':<20} {np.median(latencies) * 1000:.2f}")
                    print(f"{'P95延迟:':<20} {np.percentile(latencies, 95) * 1000:.2f}")
                    print(f"{'P99延迟:':<20} {np.percentile(latencies, 99) * 1000:.2f}")

                # ITL统计
                all_itls = []
                for t in all_turns:
                    if t.itl:
                        all_itls.extend(t.itl)

                if all_itls:
                    print(f"\n{'-' * 30}")
                    print(" ITL统计 (ms) ".center(30, "-"))
                    print(f"{'-' * 30}")
                    print(f"{'平均ITL:':<20} {np.mean(all_itls) * 1000:.2f}")
                    print(f"{'中位数ITL:':<20} {np.median(all_itls) * 1000:.2f}")
                    print(f"{'P95 ITL:':<20} {np.percentile(all_itls, 95) * 1000:.2f}")
                    print(f"{'P99 ITL:':<20} {np.percentile(all_itls, 99) * 1000:.2f}")

            # 按轮次统计
            print(f"\n{'-' * 50}")
            print(" 分轮次统计 ".center(50, "-"))
            print(f"{'-' * 50}")
            for turn_num in [1, 2, 3]:
                turn_results = []
                for conv in results:
                    turn_data = [t for t in conv.turns if t.turn_number == turn_num and t.success]
                    turn_results.extend(turn_data)

                if turn_results:
                    avg_latency = np.mean([t.latency for t in turn_results]) * 1000
                    avg_tokens = np.mean([t.completion_tokens for t in turn_results])
                    success_rate = len(turn_results) / len(results) * 100
                    print(
                        f"第{turn_num}轮: 成功率{success_rate:.1f}%, 平均延迟{avg_latency:.1f}ms, 平均输出{avg_tokens:.1f}tokens")

        print(f"{'=' * 80}")


async def main():
    parser = argparse.ArgumentParser(description="Multi-turn chat benchmark")
    parser.add_argument("--model-name", type=str, default="qwen3", help="Model name")
    parser.add_argument("--max-tokens", type=int, default=512, help="Maximum output tokens")
    parser.add_argument("--host", type=str, default="172.17.111.150", help="Target service host")
    parser.add_argument("--port", type=int, default=8080, help="Target service port")
    parser.add_argument("--num-conversations", type=int, default=10, help="Number of concurrent conversations")
    parser.add_argument("--save-result", action="store_true", help="Save result to file")
    parser.add_argument("--result-file", type=str, default="multi_turn_benchmark_result.json", help="Result file name")

    args = parser.parse_args()

    # 初始化benchmark
    benchmark = MultiTurnChatBenchmark(args.model_name, args.max_tokens)

    # 运行测试
    results = await benchmark.run_multiple_conversations(
        args.host,
        args.port,
        args.num_conversations
    )

    # 保存结果
    if args.save_result and results:
        result_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "config": {
                "model_name": args.model_name,
                "max_tokens": args.max_tokens,
                "target_service": f"{args.host}:{args.port}",
                "num_conversations": args.num_conversations,
                "turns_per_conversation": 3
            },
            "summary": {
                "total_conversations": len(results),
                "successful_conversations": sum(1 for r in results if r.conversation_success),
                "total_turns": sum(len(r.turns) for r in results),
                "successful_turns": sum(r.successful_turns for r in results)
            },
            "conversations": [
                {
                    "conversation_index": r.conversation_index + 1,
                    "chat_id": r.chat_id,
                    "successful_turns": r.successful_turns,
                    "total_duration": r.total_duration,
                    "total_tokens": r.total_tokens,
                    "conversation_success": r.conversation_success,
                    "turns": [
                        {
                            "turn_number": t.turn_number,
                            "success": t.success,
                            "prompt_tokens": t.prompt_tokens,
                            "completion_tokens": t.completion_tokens,
                            "ttft_ms": t.ttft * 1000 if t.ttft else 0,
                            "latency_ms": t.latency * 1000,
                            "error": t.error
                        }
                        for t in r.turns
                    ]
                }
                for r in results
            ]
        }

        with open(args.result_file, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)

        print(f"\n结果已保存到 {args.result_file}")


if __name__ == "__main__":
    asyncio.run(main())
