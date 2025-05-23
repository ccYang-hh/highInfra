"""
PluginManager 测试脚本

使用 RouterPlugin 测试 PluginManager 的各项功能
"""
import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

# 导入系统模块
from tmatrix.common.logging import init_logger
from tmatrix.runtime.plugins.routers.vllm_router import RouterPlugin, RouterStrategy
from tmatrix.runtime.utils import PluginEvent, PluginEventType, EventBus
from tmatrix.runtime.config import PluginInfo, PluginRegistryConfig

# 导入 PluginManager（假设放在 tmatrix.runtime.core.manager 中）
from tmatrix.runtime.plugins import PluginManager, PluginState

# 设置日志
logger = init_logger("tests/plugins")


async def test_plugin_manager():
    """测试插件管理器功能"""
    logger.info("开始测试 PluginManager...")

    # 1. 创建事件总线
    event_bus = EventBus()
    event_bus.set_event_loop(asyncio.get_running_loop())

    # 事件历史记录
    event_history = []

    # 事件处理函数
    def event_handler(event):
        logger.info(f"收到事件: {event.event_type} - 插件: {event.plugin_name}")
        event_history.append(event)

    # 异步事件处理函数
    async def async_event_handler(event):
        logger.info(f"异步处理事件: {event.event_type} - 插件: {event.plugin_name}")
        event_history.append(event)

    # 订阅事件
    event_bus.subscribe("*", event_handler)
    await event_bus.subscribe_async("*", async_event_handler)

    # 2. 创建插件注册表
    registry = PluginRegistryConfig(
        plugins={
            "router": PluginInfo(
                enabled=True,
                module="tmatrix.runtime.plugins.routers.vllm_router",
                priority=100
            )
        },
        auto_discovery=False
    )

    # 3. 创建插件管理器
    manager = PluginManager(registry, event_bus)
    manager.set_event_loop(asyncio.get_running_loop())

    # 4. 初始化插件管理器
    logger.info("初始化插件管理器...")
    await manager.initialize()

    # 等待事件传播
    await asyncio.sleep(0.1)

    # 5. 验证插件已加载和初始化
    assert "router" in manager._plugins, "插件未成功加载"
    assert manager._plugin_states["router"] == PluginState.INITIALIZED, "插件状态不是已初始化"

    # 6. 启动插件
    logger.info("启动插件...")
    # await manager.start_plugins() # 启动所有插件
    success = await manager.start_plugin("router")
    assert success, "启动插件失败"
    assert manager._plugin_states["router"] == PluginState.RUNNING, "插件状态不是运行中"

    # 等待事件传播
    await asyncio.sleep(0.1)

    # 7. 获取插件资源
    stages = manager.get_pipeline_stages("router")
    hooks = manager.get_stage_hooks("router")
    pipeline_hooks = manager.get_pipeline_hooks("router")
    router = manager.get_api_router("router")

    logger.info(f"插件资源: {len(stages)} 阶段, {len(hooks)} 钩子, {len(pipeline_hooks)} 管道钩子")
    assert len(stages) == 2, "插件阶段数量不正确"
    assert len(hooks) == 1, "插件钩子数量不正确"

    # 8. 测试配置更新
    logger.info("测试配置更新...")
    update_data = {
        "default_strategy": RouterStrategy.Session
    }

    # 发布配置更新事件
    event_bus.publish(
        PluginEventType.CONFIG_UPDATED,
        PluginEvent(
            plugin_name="router",
            event_type=PluginEventType.CONFIG_UPDATED,
            data={"config": update_data}
        )
    )

    # 等待配置更新处理
    await asyncio.sleep(0.2)

    # 验证配置更新
    plugin = manager.get_plugin("router")
    assert plugin.config.default_strategy == RouterStrategy.Session, "配置更新未生效"

    # 9. 停止插件
    logger.info("停止插件...")
    success = await manager.stop_plugin("router")
    assert success, "停止插件失败"
    assert manager._plugin_states["router"] == PluginState.INITIALIZED, "插件状态不是已初始化"

    # 10. 卸载插件
    logger.info("卸载插件...")
    success = await manager.unload_plugin("router")
    assert success, "卸载插件失败"
    assert "router" not in manager._plugins, "插件未成功卸载"

    # 11. 重新加载插件
    logger.info("重新加载插件...")
    success = await manager.reload_plugin("router")
    assert success, "重新加载插件失败"
    assert "router" in manager._plugins, "插件未成功重新加载"

    # 12. 关闭插件管理器
    logger.info("关闭插件管理器...")
    await manager.shutdown()

    # 13. 检查事件历史
    event_types = [e.event_type for e in event_history]
    logger.info(f"接收到的事件类型: {event_types}")

    assert PluginEventType.LOADED in event_types, "未收到加载事件"
    assert PluginEventType.INITIALIZED in event_types, "未收到初始化事件"
    assert PluginEventType.STARTED in event_types, "未收到启动事件"
    assert PluginEventType.STOPPED in event_types, "未收到停止事件"
    assert PluginEventType.UNLOADED in event_types, "未收到卸载事件"

    logger.info("PluginManager 测试完成，所有测试通过!")


async def test_concurrent_plugin_operations():
    """测试并发插件操作"""
    logger.info("开始测试并发插件操作...")

    # 创建插件管理器
    registry = PluginRegistryConfig(
        plugins={
            "router": PluginInfo(
                enabled=True,
                module="tmatrix.runtime.plugins.routers.vllm_router",
                priority=100
            )
        }
    )
    manager = PluginManager(registry)
    manager.set_event_loop(asyncio.get_running_loop())

    # 初始化管理器
    await manager.initialize()

    # 启动插件
    await manager.start_plugin("router")

    # 模拟并发操作
    async def concurrent_test():
        tasks = []
        # 多次获取资源
        for _ in range(10):
            for stage in manager.get_pipeline_stages("router"):
                tasks.append(asyncio.create_task(stage))
            for hook in manager.get_stage_hooks("router"):
                tasks.append(asyncio.create_task(hook))

        # 等待所有任务完成
        results = await asyncio.gather(*tasks)
        return results

    results = await concurrent_test()
    logger.info(f"并发操作返回 {len(results)} 个结果")

    # 清理
    await manager.shutdown()
    logger.info("并发操作测试完成!")


async def test_error_handling():
    """测试错误处理"""
    logger.info("开始测试错误处理...")

    # 使用不存在的模块创建插件注册
    registry = PluginRegistryConfig(
        plugins={
            "nonexistent": PluginInfo(
                enabled=True,
                module="tmatrix.nonexistent.module",
                priority=100
            ),
            "router": PluginInfo(
                enabled=True,
                module="tmatrix.runtime.plugins.routers.vllm_router",
                priority=100
            )
        }
    )
    manager = PluginManager(registry)

    # 捕获错误事件
    error_events = []

    def error_handler(event):
        if event.event_type == PluginEventType.ERROR:
            error_events.append(event)

    # 设置事件处理
    event_bus = EventBus()
    event_bus.subscribe(PluginEventType.ERROR, error_handler)
    manager.event_bus = event_bus
    manager.set_event_loop(asyncio.get_running_loop())

    # 初始化，应该处理nonexistent插件的错误
    await manager.initialize()

    # 等待事件传播
    await asyncio.sleep(0.1)

    # 检查错误处理
    assert len(error_events) > 0, "未捕获到错误事件"
    assert "router" in manager._plugins, "有效插件未加载"

    # 清理
    await manager.shutdown()
    logger.info("错误处理测试完成!")


async def main():
    """主测试函数"""
    try:
        # 运行基本功能测试
        await test_plugin_manager()

        # 运行并发操作测试
        # await test_concurrent_plugin_operations()

        # 运行错误处理测试
        await test_error_handling()

        logger.info("所有测试通过!")
        return 0
    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    # 在新的事件循环中运行测试
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
