import time
from typing import Optional
from aiohttp import web

from tmatrix.common.logging import init_logger
from .core.base import CollectedEndpoint

logger = init_logger("monitor/web_server")


class MonitorWebServer:
    """
    监控服务的Web服务器
    负责处理所有 HTTP 请求和路由管理
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 8080):
        self.host = host
        self.port = port

        # HTTP 组件
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # 状态
        self._running = False

        # 依赖注入 - 外部服务引用
        self._monitor_service = None

    def set_monitor_service(self, monitor_service):
        """注入监控服务依赖"""
        self._monitor_service = monitor_service

    def _setup_routes(self):
        """设置路由"""
        self._app.router.add_get('/api/v1/monitor/health', self._health_handler)
        self._app.router.add_get('/api/v1/monitor/status', self._status_handler)
        self._app.router.add_get('/api/v1/monitor/stats', self._stats_handler)
        self._app.router.add_post('/api/v1/monitor/add_endpoint', self._add_endpoint_handler)
        self._app.router.add_post('/api/v1/monitor/del_endpoint', self._del_endpoint_handler)

    async def start(self):
        """启动 Web 服务器"""
        if self._running:
            logger.warning("Web server is already running")
            return

        logger.info(f"Starting web server on {self.host}:{self.port}")

        try:
            # 创建应用
            self._app = web.Application()

            # 设置路由
            self._setup_routes()

            # 启动服务
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()

            self._site = web.TCPSite(self._runner, self.host, self.port)
            await self._site.start()

            self._running = True
            logger.info(f"Web server started at http://{self.host}:{self.port}")

        except Exception as e:
            logger.error(f"Failed to start web server: {e}")
            await self.stop()
            raise

    async def stop(self):
        """停止 Web 服务器"""
        if not self._running:
            return

        logger.info("Stopping web server...")

        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._app = None
        self._running = False

        logger.info("Web server stopped")

    @property
    def is_running(self) -> bool:
        """检查服务器是否在运行"""
        return self._running

    # ==================== 基础接口处理器 ====================

    async def _health_handler(self, request) -> web.Response:
        """健康检查处理器"""
        if not self._monitor_service:
            return web.json_response({
                'status': 'error',
                'message': 'Monitor service not available'
            }, status=503)

        return web.json_response({
            'status': 'healthy' if self._monitor_service._running else 'stopped',
            'timestamp': time.time(),
            'collectors': len(self._monitor_service.collectors),
            'exporters': len(self._monitor_service.exporters),
            'web_server': 'running'
        })

    async def _status_handler(self, request) -> web.Response:
        """状态信息处理器"""
        if not self._monitor_service:
            return web.json_response({'error': 'Monitor service not available'}, status=503)

        collector_status = [c.get_stats() for c in self._monitor_service.collectors]
        exporter_status = [e.get_stats() for e in self._monitor_service.exporters]

        return web.json_response({
            'running': self._monitor_service._running,
            'collectors': collector_status,
            'exporters': exporter_status,
            'registry': self._monitor_service.registry.get_stats(),
            'web_server': {
                'running': self._running,
                'host': self.host,
                'port': self.port
            }
        })

    async def _stats_handler(self, request) -> web.Response:
        """统计信息处理器"""
        if not self._monitor_service:
            return web.json_response({'error': 'Monitor service not available'}, status=503)

        # 解析POST请求体
        try:
            request_data = await request.json()
            requested_metrics = request_data.get('metrics', [])
        except Exception as e:
            return web.json_response({'error': f'Invalid JSON: {str(e)}'}, status=400)

        if not requested_metrics:
            return web.json_response({'error': 'metrics field is required'}, status=400)

        # 获取所有指标数据
        all_metrics = await self._monitor_service.registry.get_all_metrics()

        # 构建指标名称到指标对象列表的映射
        metric_map = {}
        stats = {}

        for collector_name, metric_list in all_metrics.items():
            stats[collector_name] = {
                'metric_count': len(metric_list),
                'last_update': max((m.timestamp for m in metric_list), default=0)
            }

            for metric in metric_list:
                # 序列化指标数据
                metric_data = {
                    'name': metric.name,
                    'value': metric.value,
                    'metric_type': metric.metric_type.value if hasattr(metric.metric_type, 'value') else str(
                        metric.metric_type),
                    'labels': metric.labels,
                    'timestamp': metric.timestamp,
                    'description': metric.description,
                    'unit': metric.unit
                }

                # 同名指标放到列表中
                if metric.name not in metric_map:
                    metric_map[metric.name] = []
                metric_map[metric.name].append(metric_data)

        # 根据请求筛选指标
        metrics_data = {}

        # 特判：如果包含 "all"，返回所有指标
        if "all" in requested_metrics:
            metrics_data = metric_map
        else:
            # 只返回请求的指标
            for metric_name in requested_metrics:
                if metric_name in metric_map:
                    metrics_data[metric_name] = metric_map[metric_name]

        response_data = {
            'stats': stats,
            'metrics': metrics_data
        }

        return web.json_response(response_data)

    async def _add_endpoint_handler(self, request) -> web.Response:
        """添加端点处理器"""
        if not self._monitor_service:
            return web.json_response({'error': 'Monitor service not available'}, status=503)

        try:
            # 解析请求体
            data = await request.json()

            # 验证必需字段
            if 'address' not in data or 'instance_name' not in data:
                return web.json_response({
                    'error': 'Missing required fields: address, instance_name'
                }, status=400)

            # 创建 Endpoint 对象
            endpoint = CollectedEndpoint(
                address=data['address'],
                instance_name=data['instance_name']
            )

            # 调用 service 方法
            await self._monitor_service.add_endpoint(endpoint)

            return web.json_response({
                'success': True,
                'message': f'Endpoint {endpoint.instance_name} added successfully',
                'endpoint': {
                    'address': endpoint.address,
                    'instance_name': endpoint.instance_name
                }
            })

        except ValueError as e:
            return web.json_response({'error': f'Invalid JSON: {str(e)}'}, status=400)
        except Exception as e:
            logger.error(f"Error adding endpoint: {e}")
            return web.json_response({'error': f'Failed to add endpoint: {str(e)}'}, status=500)

    async def _del_endpoint_handler(self, request) -> web.Response:
        """删除端点处理器"""
        if not self._monitor_service:
            return web.json_response({'error': 'Monitor service not available'}, status=503)

        try:
            # 解析请求体
            data = await request.json()

            # 验证必需字段
            if 'address' not in data or 'instance_name' not in data:
                return web.json_response({
                    'error': 'Missing required fields: address, instance_name'
                }, status=400)

            # 创建 Endpoint 对象用于匹配
            endpoint = CollectedEndpoint(
                address=data['address'],
                instance_name=data['instance_name']
            )

            # 调用 service 方法
            success = await self._monitor_service.del_endpoint(endpoint)

            if success:
                return web.json_response({
                    'success': True,
                    'message': f'Endpoint {endpoint.instance_name} deleted successfully'
                })
            else:
                return web.json_response({
                    'success': False,
                    'message': f'Endpoint {endpoint.instance_name} not found'
                }, status=404)

        except ValueError as e:
            return web.json_response({'error': f'Invalid JSON: {str(e)}'}, status=400)
        except Exception as e:
            logger.error(f"Error deleting endpoint: {e}")
            return web.json_response({'error': f'Failed to delete endpoint: {str(e)}'}, status=500)
