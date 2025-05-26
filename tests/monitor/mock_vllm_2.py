from fastapi import FastAPI, Response
import random
import time

app = FastAPI()

# 全局状态维护counter值
counter_state = {
    'prompt_tokens_total': 1000,
    'generation_tokens_total': 500,
    'start_time': time.time()
}

# 为每个histogram metric维护全局状态
histogram_state = {}

histogram_configs = {
    'time_to_first_token_seconds': [0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0,
                                    7.5, 10.0, 20.0, 40.0, 80.0, 160.0, 640.0, 2560.0],
    'time_per_output_token_seconds': [0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 2.5, 5.0,
                                      7.5, 10.0, 20.0, 40.0, 80.0],
    'e2e_request_latency_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0,
                                    60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
    'request_queue_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0,
                                   120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
    'request_inference_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0,
                                       60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
    'request_prefill_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0,
                                     60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0],
    'request_decode_time_seconds': [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0,
                                    60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 7680.0]
}

def generate_histogram_data(metric_name, buckets):
    # 初始化全局状态
    if metric_name not in histogram_state:
        histogram_state[metric_name] = {
            'total_count': 0,
            'bucket_counts': [0 for _ in buckets],
            'sum_value': 0.0
        }
    state = histogram_state[metric_name]

    # 新增一批请求，并用"对数正态分布"生成合理的耗时
    new_requests = random.randint(1, 10)
    for _ in range(new_requests):
        latency = abs(random.lognormvariate(0, 1))  # 正常数据
        state['sum_value'] += latency
        state['total_count'] += 1
        # 累增所有大于等于该latency的bucket
        for i, bucket in enumerate(buckets):
            if latency <= bucket:
                state['bucket_counts'][i] += 1

    samples = []
    for i, bucket in enumerate(buckets):
        samples.append(
            f'vllm:{metric_name}_bucket{{engine="0",le="{bucket}",model_name="qwen"}} {state["bucket_counts"][i]}.0'
        )
    samples.append(
        f'vllm:{metric_name}_bucket{{engine="0",le="+Inf",model_name="qwen"}} {state["total_count"]}.0'
    )
    samples.append(
        f'vllm:{metric_name}_count{{engine="0",model_name="qwen"}} {state["total_count"]}.0'
    )
    samples.append(
        f'vllm:{metric_name}_sum{{engine="0",model_name="qwen"}} {round(state["sum_value"], 6)}'
    )
    return samples, state['total_count'], state['sum_value']

@app.get("/metrics")
async def metrics_handler():
    """模拟vLLM的metrics端点（优化、正确递增）"""
    counter_state['prompt_tokens_total'] += random.randint(10, 100)
    counter_state['generation_tokens_total'] += random.randint(5, 50)
    num_running = random.randint(0, 10)
    num_waiting = random.randint(0, 5)
    gpu_cache_usage = round(random.uniform(0.005, 0.9), 6)

    # 生成所有直方图数据
    histogram_data = {}
    for metric_name, buckets in histogram_configs.items():
        histogram_data[metric_name] = generate_histogram_data(metric_name, buckets)

    metrics_text = f'''# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{{engine="0",model_name="qwen"}} {num_running}.0
# HELP vllm:num_requests_waiting Number of requests waiting to be processed.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{{engine="0",model_name="qwen"}} {num_waiting}.0
# HELP vllm:gpu_cache_usage_perc GPU KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{{engine="0",model_name="qwen"}} {gpu_cache_usage}
# HELP vllm:prompt_tokens_total Number of prefill tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total{{engine="0",model_name="qwen"}} {counter_state['prompt_tokens_total']}.0
# HELP vllm:generation_tokens_total Number of generation tokens processed.
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{{engine="0",model_name="qwen"}} {counter_state['generation_tokens_total']}.0
# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
{chr(10).join(histogram_data['time_to_first_token_seconds'][0])}
# HELP vllm:time_per_output_token_seconds Histogram of time per output token in seconds.
# TYPE vllm:time_per_output_token_seconds histogram
{chr(10).join(histogram_data['time_per_output_token_seconds'][0])}
# HELP vllm:e2e_request_latency_seconds Histogram of e2e request latency in seconds.
# TYPE vllm:e2e_request_latency_seconds histogram
{chr(10).join(histogram_data['e2e_request_latency_seconds'][0])}
# HELP vllm:request_queue_time_seconds Histogram of time spent in WAITING phase for request.
# TYPE vllm:request_queue_time_seconds histogram
{chr(10).join(histogram_data['request_queue_time_seconds'][0])}
# HELP vllm:request_inference_time_seconds Histogram of time spent in RUNNING phase for request.
# TYPE vllm:request_inference_time_seconds histogram
{chr(10).join(histogram_data['request_inference_time_seconds'][0])}
# HELP vllm:request_prefill_time_seconds Histogram of time spent in PREFILL phase for request.
# TYPE vllm:request_prefill_time_seconds histogram
{chr(10).join(histogram_data['request_prefill_time_seconds'][0])}
# HELP vllm:request_decode_time_seconds Histogram of time spent in DECODE phase for request.
# TYPE vllm:request_decode_time_seconds histogram
{chr(10).join(histogram_data['request_decode_time_seconds'][0])}
'''

    return Response(content=metrics_text, media_type='text/plain')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)