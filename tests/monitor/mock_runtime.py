from fastapi import FastAPI
import random
import time

app = FastAPI()

# 模拟指标
start_time = time.time()
event_stats = {
    "block_stored_count": 0,
    "block_removed_count": 0,
    "active_blocks_count": 0,
    "processed_tokens_total": 0,
    # 新增字典类型指标
    "processed_tokens_count": {},
    "blocks_count": {}
}

request_stats = {
    "pending_requests": 0,
    "active_requests": 0,
    "completed_requests": 0,
    "failed_requests": 0,
    "avg_ttft": 0.0,
    "avg_latency": 0.0,
    "requests_per_minute": 0
}

# 模拟实例列表
INSTANCES = ["vLLM worker 1", "vLLM worker 2", "vLLM worker 3"]
# 初始化字典指标
for instance in INSTANCES:
    event_stats["processed_tokens_count"][instance] = 0  # counter 初始值为0
    event_stats["blocks_count"][instance] = random.randint(5, 25)  # gauge 初始值随机

@app.get("/api/v1/metrics")
async def get_metrics():
    # 模拟指标变化
    event_stats["block_stored_count"] += random.randint(0, 5)
    event_stats["block_removed_count"] += random.randint(0, 5)
    event_stats["active_blocks_count"] = random.randint(0, 100)
    event_stats["processed_tokens_total"] += random.randint(10, 100)
    # processed_tokens_count: counter 类型 - 累加增长
    for instance in INSTANCES:
        event_stats["processed_tokens_count"][instance] += random.randint(5, 20)

    # blocks_count: gauge 类型 - 每次重新生成当前值
    event_stats["blocks_count"] = {
        instance: random.randint(5, 25) for instance in INSTANCES
    }

    request_stats["pending_requests"] = random.randint(0, 10)
    request_stats["active_requests"] = random.randint(0, 5)
    request_stats["completed_requests"] += random.randint(0, 3)
    request_stats["failed_requests"] += random.randint(0, 2)
    request_stats["avg_ttft"] = random.uniform(0.1, 0.5)
    request_stats["avg_latency"] = random.uniform(0.5, 2.0)
    request_stats["requests_per_minute"] = random.randint(0, 5)

    return {
        "kv_events_stats": {
            **event_stats,
            "uptime": time.time() - start_time
        },
        "requests_stats": request_stats
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8009)
