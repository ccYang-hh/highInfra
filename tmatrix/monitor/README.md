## Monitor模块V0架构

代码工程视图：
```text
monitor/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py              # 基础类和接口定义
│   ├── registry.py          # 指标注册中心
│   └── config.py            # 配置管理
├── collectors/
│   ├── __init__.py
│   ├── vllm_collector.py    # vLLM引擎指标收集器
│   └── runtime_collector.py # 业务运行时指标收集器
├── exporters/
│   ├── __init__.py
│   ├── prometheus.py        # Prometheus导出器
│   └── log.py               # 日志导出器
├── service.py               # 监控服务主类
├── server.py                # Web服务
├── standalone.py            # 独立运行入口（调试用）
└── integrated.py            # 集成到业务系统的入口
```