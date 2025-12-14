"""
Simple HTTP server for Observability (Metrics & Health).
"""
from __future__ import annotations

import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Callable, Dict, Any

from .health import HealthCheck

try:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry, multiprocess, REGISTRY
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

logger = logging.getLogger(__name__)

class ObsRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health/live":
            self._handle_live()
        elif self.path == "/health/ready":
            self._handle_ready()
        elif self.path == "/metrics":
            self._handle_metrics()
        else:
            self.send_error(404, "Not Found")

    def _handle_live(self):
        if HealthCheck.is_live():
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_error(503, "Service Unavailable")

    def _handle_ready(self):
        # Use the callback injected into the server if available
        is_ready = True
        if hasattr(self.server, 'readiness_check') and self.server.readiness_check:
            is_ready = self.server.readiness_check()
        
        # Also check local static health logic
        if is_ready and HealthCheck.is_ready():
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_error(503, "Service Unavailable")

    def _handle_metrics(self):
        try:
            output_lines = []
            
            # 1. Add custom metrics from callback (shared memory counters)
            if hasattr(self.server, 'metrics_callback') and self.server.metrics_callback:
                custom_metrics = self.server.metrics_callback()
                if custom_metrics:
                    for name, value in custom_metrics.items():
                        # Format as Prometheus text format
                        output_lines.append(f"# HELP {name} Custom metric from shared memory")
                        output_lines.append(f"# TYPE {name} gauge")
                        output_lines.append(f"{name} {value}")
            
            # 2. Add Prometheus metrics if available
            if PROMETHEUS_AVAILABLE:
                if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
                    registry = CollectorRegistry()
                    multiprocess.MultiProcessCollector(registry)
                else:
                    registry = REGISTRY
                prometheus_data = generate_latest(registry).decode('utf-8')
                output_lines.append(prometheus_data)
            
            response = "\n".join(output_lines).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            logger.error(f"Error generating metrics: {e}")
            self.send_error(500, "Internal Server Error")

    def log_message(self, format, *args):
        # Suppress default logging to stdout to avoid clutter
        pass

class ObsServer:
    def __init__(self, host: str, port: int, readiness_check: Optional[Callable[[], bool]] = None, 
                 metrics_callback: Optional[Callable[[], Dict[str, Any]]] = None):
        self.host = host
        self.port = port
        self.readiness_check = readiness_check
        self.metrics_callback = metrics_callback
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        try:
            self.server = HTTPServer((self.host, self.port), ObsRequestHandler)
            # Inject the callbacks into the server instance so the handler can access them
            self.server.readiness_check = self.readiness_check
            self.server.metrics_callback = self.metrics_callback
            
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            logger.info(f"Observability server listening on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start observability server: {e}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

