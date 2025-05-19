#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
简单测试脚本，用于验证 EtcdServiceDiscovery 的基本功能
确保先安装 etcd3 依赖: pip install etcd3
确保本地运行了 etcd 服务，默认地址是 localhost:2379
"""

import time
import uuid
from tmatrix.runtime.service_discovery import EtcdServiceDiscovery
from tmatrix.runtime.service_discovery import Endpoint, EndpointType, TransportType

# 创建服务发现实例
discovery = EtcdServiceDiscovery(
    etcd_host="localhost",  # 修改为你的 etcd 地址
    etcd_port=2379,  # 修改为你的 etcd 端口
    prefix="tmatrix.test.endpoints",
    cache_ttl=10.0
)

if __name__ == "__main__":
    try:
        print("====== 测试 EtcdServiceDiscovery ======")

        # 测试1: 注册端点
        print("\n[1] 测试注册端点...")
        endpoint1 = Endpoint(
            endpoint_id=f"test-endpoint-{uuid.uuid4()}",
            endpoint_type=[EndpointType.CHAT_COMPLETION],
            address="http://localhost:8000",
            model_name="gpt-3.5-turbo",
            transport_type=TransportType.HTTP,
            priority=10,
            metadata={"max_tokens": 4096},
        )

        # 注册无TTL的端点
        discovery.register_endpoint(endpoint1)
        print(f"注册端点成功: {endpoint1.endpoint_id}")

        # 测试2: 注册带TTL的端点
        print("\n[2] 测试注册带TTL的端点...")
        endpoint2 = Endpoint(
            endpoint_id=f"test-endpoint-ttl-{uuid.uuid4()}",
            endpoint_type=[EndpointType.CHAT_COMPLETION],
            address="http://localhost:8001",
            model_name="gpt-4",
            transport_type=TransportType.HTTP,
            priority=20,
            metadata={"max_tokens": 8192}
        )

        # 注册带30秒TTL的端点
        discovery.register_endpoint(endpoint2, ttl=30)
        print(f"注册带TTL端点成功: {endpoint2.endpoint_id}")

        # 测试3: 获取所有端点
        print("\n[3] 测试获取所有端点...")
        endpoints = discovery.get_endpoints()
        print(f"获取到 {len(endpoints)} 个端点:")
        for i, ep in enumerate(endpoints):
            print(f"  [{i + 1}] ID: {ep.endpoint_id}, 模型: {ep.model_name}, 地址: {ep.address}")

        # 测试4: 测试监听功能
        print("\n[4] 测试监听功能...")
        print("注册新端点以触发监听事件...")
        endpoint3 = Endpoint(
            endpoint_id=f"test-endpoint-watch-{uuid.uuid4()}",
            endpoint_type=[EndpointType.CHAT_COMPLETION],
            address="http://localhost:8002",
            model_name="claude-3",
            transport_type=TransportType.HTTP,
            priority=15,
            metadata={"version": "1.0"}
        )

        discovery.register_endpoint(endpoint3)
        print(f"已注册新端点: {endpoint3.endpoint_id}，等待监听更新...")

        # 等待一秒让监听事件生效
        time.sleep(2)

        # 重新获取端点检查是否被监听到
        endpoints = discovery.get_endpoints()
        print(f"现在获取到 {len(endpoints)} 个端点")

        # 测试5: 移除端点
        print("\n[5] 测试移除端点...")
        print(f"移除端点: {endpoint1.endpoint_id}")
        discovery.remove_endpoint(endpoint1.endpoint_id)

        # 等待一秒让监听事件生效
        time.sleep(2)

        # 重新获取端点检查是否被移除
        endpoints = discovery.get_endpoints()
        endpoint_ids = [ep.endpoint_id for ep in endpoints]
        if endpoint1.endpoint_id not in endpoint_ids:
            print(f"端点 {endpoint1.endpoint_id} 已成功移除")
        else:
            print(f"端点 {endpoint1.endpoint_id} 移除失败")

        print("\n[6] 测试健康状态...")
        health_status = discovery.health()
        print(f"服务发现健康状态: {'正常' if health_status else '异常'}")

    except Exception as e:
        print(f"测试过程中发生错误: {e}")

    finally:
        # 清理注册的测试端点
        print("\n====== 清理测试资源 ======")
        try:
            for endpoint_id in [endpoint1.endpoint_id, endpoint2.endpoint_id, endpoint3.endpoint_id]:
                print(f"移除端点: {endpoint_id}")
                discovery.remove_endpoint(endpoint_id)
        except:
            pass

        # 关闭服务发现
        print("关闭服务发现...")
        discovery.close()
        print("测试完成")
