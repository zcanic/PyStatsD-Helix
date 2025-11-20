"""
Simple HTTP server for Observability (Metrics & Health).
"""
from __future__ import annotations

import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

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
        if HealthCheck.is_ready():
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_error(503, "Service Unavailable")

    def _handle_metrics(self):
        if not PROMETHEUS_AVAILABLE:
            self.send_error(501, "Prometheus client not installed")
            return

        try:
            if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
                registry = CollectorRegistry()
                multiprocess.MultiProcessCollector(registry)
            else:
                registry = REGISTRY

            data = generate_latest(registry)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            logger.error(f"Error generating metrics: {e}")
            self.send_error(500, "Internal Server Error")

    def log_message(self, format, *args):
        # Suppress default logging to stdout to avoid clutter
        pass

class ObsServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        try:
            self.server = HTTPServer((self.host, self.port), ObsRequestHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            logger.info(f"Observability server listening on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start observability server: {e}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
