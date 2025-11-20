from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from typing import Optional, List, Any
from pathlib import Path

from .base import Backend, FlushResult, BackendDescriptor
from ..obs.metrics import MetricRegistry
from ..obs.resilience import CircuitBreaker, CircuitBreakerConfig

# Type checking imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..config import GraphiteConfig
    from ..aggregator import AggregatedBatch

class GraphiteBackend(Backend):
    name = "graphite"
    supports_tags = False # Plaintext protocol doesn't support tags natively
    required = True
    
    # Compile regex for sanitization once
    _SANITIZE_REGEX = re.compile(r"[^a-zA-Z0-9_\-.]")
    
    def __init__(self):
        self.config: Optional[GraphiteConfig] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.logger = logging.getLogger("pystatsd.backends.graphite")
        self._connected_at = 0.0
        
        # Circuit Breaker
        self.circuit_breaker = CircuitBreaker("graphite", CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30.0))

        # Metrics
        self.metrics = MetricRegistry()
        self.sent_total = self.metrics.counter(
            "pystatsd_backend_sent_total",
            "Total metrics sent to backend",
            labelnames=("backend", "status")
        )
        self.errors_total = self.metrics.counter(
            "pystatsd_backend_errors_total",
            "Total errors in backend",
            labelnames=("backend", "type")
        )

    def describe(self) -> BackendDescriptor:
        return BackendDescriptor(
            name=self.name,
            version="0.1.0",
            supports_tags=False,
            max_batch_size=1000,
            qos="critical"
        )

    async def setup(self, config: GraphiteConfig, *, loop: asyncio.AbstractEventLoop) -> None:
        self.config = config
        self._loop = loop
        # We don't connect immediately on setup, we connect on first flush or ensure_connection
        # But let's try to connect once to fail fast if config is bad?
        # Blueprint says "fail fast" for config, but connection issues might be transient.
        # Let's just validate config here.
        if config.enable_tls and config.ca_file and not Path(config.ca_file).exists():
             raise FileNotFoundError(f"CA file not found: {config.ca_file}")

    async def _ensure_connection(self) -> bool:
        if self._writer and not self._writer.is_closing():
            return True
            
        try:
            ssl_ctx = None
            if self.config.enable_tls:
                ssl_ctx = ssl.create_default_context(cafile=str(self.config.ca_file) if self.config.ca_file else None)
            
            self.logger.debug(f"Connecting to Graphite at {self.config.host}:{self.config.port}...")
            
            future = asyncio.open_connection(
                host=self.config.host, 
                port=self.config.port, 
                ssl=ssl_ctx
            )
            
            self._reader, self._writer = await asyncio.wait_for(future, timeout=self.config.connect_timeout)
            self._connected_at = time.time()
            self.logger.info(f"Connected to Graphite at {self.config.host}:{self.config.port}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Graphite: {e}")
            return False

    def _sanitize(self, s: str) -> str:
        """Replace invalid characters with _"""
        return self._SANITIZE_REGEX.sub("_", s)

    def _format_metric(self, path: str, value: float, timestamp: int) -> str:
        return f"{path} {value} {timestamp}\n"

    async def flush(self, batch: AggregatedBatch) -> FlushResult:
        start_time = time.monotonic()
        
        # 0. Check Circuit Breaker
        if not self.circuit_breaker.allow_request():
            self.errors_total.labels(backend=self.name, type="circuit_open").inc()
            return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=False,
                retryable=True,
                latency_ms=0,
                error="Circuit breaker open"
            )

        # 1. Ensure connection
        if not await self._ensure_connection():
             self.circuit_breaker.record_failure()
             self.errors_total.labels(backend=self.name, type="connection").inc()
             return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=False,
                retryable=True,
                latency_ms=(time.monotonic() - start_time) * 1000,
                error="Connection failed"
            )
            
        # 2. Serialize
        # Timestamp in seconds (int)
        ts = int(batch.window_end_ns / 1e9)
        
        # Ensure config is not None (checked in setup/ensure_connection but type checker needs help)
        if not self.config:
             return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=False,
                retryable=False,
                latency_ms=0,
                error="Config not loaded"
            )

        prefix = self.config.prefix
        if prefix and not prefix.endswith("."):
            prefix += "."
            
        buffer = bytearray()
        lines_count = 0
        
        try:
            # Counters
            for key, snapshot in batch.counters.items():
                # Key format from aggregator is "name|tags" or just "name"
                # We need to handle tags if present or just strip them for plaintext
                # Blueprint says: metric.name;tag=value -> metric.name.tag_value (Graphite style)
                # But our aggregator key format is currently simple string.
                # Let's assume key is just name for MVP or parse it.
                # Aggregator._format_key_str does "name|tag1=v1,tag2=v2"
                
                if "|" in key:
                    name, tags_part = key.split("|", 1)
                    # TODO: Handle tags mapping to graphite format if needed
                    # For now, just use name
                    clean_name = self._sanitize(name)
                else:
                    clean_name = self._sanitize(key)
                
                # Output rate and count? Usually just count or rate.
                # StatsD standard: 
                # stats.counters.<name>.rate
                # stats.counters.<name>.count
                
                # We use prefix.counters.<name>
                base_path = f"{prefix}counters.{clean_name}"
                
                buffer.extend(self._format_metric(f"{base_path}.rate", snapshot.value, ts).encode("ascii"))
                buffer.extend(self._format_metric(f"{base_path}.count", snapshot.count, ts).encode("ascii"))
                lines_count += 2

            # Gauges
            for key, snapshot in batch.gauges.items():
                if "|" in key:
                    name, _ = key.split("|", 1)
                    clean_name = self._sanitize(name)
                else:
                    clean_name = self._sanitize(key)
                    
                path = f"{prefix}gauges.{clean_name}"
                buffer.extend(self._format_metric(path, snapshot.value, ts).encode("ascii"))
                lines_count += 1
                
            # Timers
            for key, snapshot in batch.timers.items():
                if "|" in key:
                    name, _ = key.split("|", 1)
                    clean_name = self._sanitize(name)
                else:
                    clean_name = self._sanitize(key)
                    
                base_path = f"{prefix}timers.{clean_name}"
                
                # Standard timer metrics
                metrics = [
                    ("count", snapshot.count),
                    ("lower", snapshot.min),
                    ("upper", snapshot.max),
                    ("mean", snapshot.mean),
                    ("std", snapshot.stddev)
                ]
                # Add percentiles
                for p_key, p_val in snapshot.percentiles.items():
                    # p90 -> upper_90
                    metrics.append((f"upper_{p_key}", p_val))
                    
                for suffix, val in metrics:
                    buffer.extend(self._format_metric(f"{base_path}.{suffix}", val, ts).encode("ascii"))
                    lines_count += 1
            
            # Sets
            for key, snapshot in batch.sets.items():
                if "|" in key:
                    name, _ = key.split("|", 1)
                    clean_name = self._sanitize(name)
                else:
                    clean_name = self._sanitize(key)
                
                path = f"{prefix}sets.{clean_name}.count"
                # SetSnapshot has 'cardinality'
                count = snapshot.cardinality
                buffer.extend(self._format_metric(path, count, ts).encode("ascii"))
                lines_count += 1

            # 3. Write to socket
            if buffer:
                # Ensure writer is available
                if not self._writer:
                     raise ConnectionError("Writer is None")

                self._writer.write(buffer)
                await asyncio.wait_for(self._writer.drain(), timeout=self.config.write_timeout)
                
                self.sent_total.labels(backend=self.name, status="success").inc(lines_count)
                self.circuit_breaker.record_success()
                
                return FlushResult(
                    backend=self.name,
                    batch_id=str(batch.window_end_ns),
                    success=True,
                    retryable=False,
                    latency_ms=(time.monotonic() - start_time) * 1000
                )
            
            # Empty batch
            self.circuit_breaker.record_success()
            return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=True,
                retryable=False,
                latency_ms=(time.monotonic() - start_time) * 1000
            )

        except Exception as e:
            self.logger.error(f"Graphite flush error: {e}")
            self.circuit_breaker.record_failure()
            self.errors_total.labels(backend=self.name, type="flush").inc()
            # Close connection on error to force reconnect
            if self._writer:
                self._writer.close()
            
            return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=False,
                retryable=True,
                latency_ms=(time.monotonic() - start_time) * 1000,
                error=str(e)
            )

    async def shutdown(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
