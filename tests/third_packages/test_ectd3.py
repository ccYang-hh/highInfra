"""
etcd 功能测试脚本 - 使用 etcd3 库测试 etcd 的核心功能
"""

import etcd3
import time
import sys


def print_separator(title):
    """打印分隔线"""
    print("\n" + "=" * 50)
    print(f"  {title}")
    print("=" * 50)


def main():
    # 连接到 etcd 服务器
    print_separator("连接 etcd")
    try:
        # 默认连接到本地2379端口
        client = etcd3.client(host='localhost', port=2379)
        print("✅ 成功连接到 etcd")
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        sys.exit(1)

    # 1. 基本的键值操作
    print_separator("基本键值操作")

    # 写入键值
    client.put('/test/key1', 'Hello etcd!')
    print("✅ 写入键值: /test/key1 = 'Hello etcd!'")

    # 读取键值
    value, metadata = client.get('/test/key1')
    print(f"✅ 读取键值: /test/key1 = '{value.decode('utf-8')}'")

    # 列出前缀为 /test/ 的所有键
    print("列出前缀为 /test/ 的所有键:")
    for item in client.get_prefix('/test/'):
        value, metadata = item
        print(f"  - {metadata.key.decode('utf-8')}: {value.decode('utf-8')}")

    # 更新键值
    client.put('/test/key1', 'Updated value')
    value, _ = client.get('/test/key1')
    print(f"✅ 更新键值: /test/key1 = '{value.decode('utf-8')}'")

    # 2. 测试租约 (Lease)
    print_separator("租约 (Lease) 测试")

    # 创建5秒租约
    lease = client.lease(ttl=5)
    print(f"✅ 创建了5秒租约 (ID: {lease.id})")

    # 使用租约写入键值
    client.put('/test/lease_key', 'This will expire', lease=lease)
    print("✅ 写入带租约的键: /test/lease_key (5秒后过期)")

    # 验证键存在
    value, _ = client.get('/test/lease_key')
    print(f"✅ 读取租约键值: /test/lease_key = '{value.decode('utf-8')}'")

    print("等待租约过期 (5秒)...")
    time.sleep(6)  # 等待超过租约时间

    # 验证键已过期
    value, _ = client.get('/test/lease_key')
    if value is None:
        print("✅ 验证租约过期: 键已自动删除")
    else:
        print(f"❌ 租约未正常过期: {value.decode('utf-8')}")

    # 3. 测试监听 (Watch)
    print_separator("监听 (Watch) 测试")

    # 启动一个监听，但我们只会监听一小段时间
    watch_count = 0
    events_queue = []

    # 创建监听函数
    def watch_callback(event):
        nonlocal watch_count
        watch_count += 1
        event_type = 'PUT' if event.type == 0 else 'DELETE'
        key = event.key.decode('utf-8')
        value = event.value.decode('utf-8') if event.value else None
        print(f"✅ 监听到事件 #{watch_count}: {event_type} {key} = {value}")
        events_queue.append((event_type, key, value))

    # 启动监听
    watch_id = client.add_watch_callback('/test/watch/', watch_callback)
    print("✅ 已启动键 /test/watch/ 的监听")

    # 稍等一下，确保监听启动
    time.sleep(5)

    # 执行一些操作，触发监听事件
    print("执行操作触发监听...")
    client.put('/test/watch/key1', 'Watch test 1')
    client.put('/test/watch/key2', 'Watch test 2')
    client.delete('/test/watch/key1')

    # 等待回调执行
    time.sleep(10)

    # 取消监听
    client.cancel_watch(watch_id)
    print(f"✅ 监听已取消，共捕获 {watch_count} 个事件")

    # 4. 测试事务 (Transaction)
    print_separator("事务 (Transaction) 测试")

    # 准备一个键用于事务测试
    client.put('/test/txn', 'Initial value')

    # 创建一个事务
    print("执行事务: 当 /test/txn 的值为 'Initial value' 时更新它")
    status, responses = client.transaction(
        compare=[
            client.transactions.value('/test/txn') == b'Initial value'
        ],
        success=[
            client.transactions.put('/test/txn', 'Updated in transaction')
        ],
        failure=[
            client.transactions.put('/test/txn', 'Transaction failed')
        ]
    )

    # 检查事务结果
    value, _ = client.get('/test/txn')
    print(f"✅ 事务结果: {'成功' if status else '失败'}")
    print(f"✅ 当前值: /test/txn = '{value.decode('utf-8')}'")

    # 5. 清理测试数据
    print_separator("清理测试数据")
    client.delete_prefix('/test/')
    print("✅ 已删除所有测试键")

    print("\n✅ 测试完成!")


if __name__ == "__main__":
    main()
