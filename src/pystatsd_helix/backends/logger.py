from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import random
from typing import Any, TextIO, Optional, TYPE_CHECKING
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .base import Backend, FlushResult, BackendDescriptor

if TYPE_CHECKING:
    from ..config import LoggerConfig
    from ..aggregator import AggregatedBatch

# Try to use orjson for performance, fallback to json
try:
    import orjson
    def json_dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)
except ImportError:
    def json_dumps(obj: Any) -> bytes:
        return json.dumps(obj).encode("utf-8")


class LoggerBackend(Backend):
    name = "logger"
    supports_tags = True
    required = False
    max_batch_size = 1

    def __init__(self):
        self.config: Optional[LoggerConfig] = None
        self._writer: Optional[Any] = None # asyncio.StreamWriter or file handler wrapper
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._file_handler: Optional[RotatingFileHandler] = None

    def describe(self) -> BackendDescriptor:
        return BackendDescriptor(
            name=self.name,
            version="0.1.0",
            supports_tags=True,
            max_batch_size=1,
            qos="best_effort"
        )

    async def setup(self, config: LoggerConfig, *, loop: asyncio.AbstractEventLoop) -> None:
        self.config = config
        self._loop = loop
        
        if config.destination in ("stdout", "stderr"):
            pipe = sys.stdout if config.destination == "stdout" else sys.stderr
            # Create async writer for stdout/stderr
            # Note: connect_write_pipe is not supported on all event loops (e.g. Windows SelectorEventLoop)
            # uvloop supports it usually.
            # For cross-platform safety in MVP, especially on Windows default loop, 
            # we might need run_in_executor for blocking I/O if connect_write_pipe fails.
            try:
                transport, protocol = await loop.connect_write_pipe(
                    asyncio.streams.FlowControlMixin, pipe
                )
                self._writer = asyncio.StreamWriter(transport, protocol, None, loop)
            except Exception:
                # Fallback for Windows/SelectorLoop
                self._writer = None # Will use print/write in executor
                
        elif config.destination == "file":
            if not config.file_path:
                raise ValueError("file_path is required when destination is 'file'")
            
            self._file_handler = RotatingFileHandler(
                config.file_path,
                maxBytes=config.max_bytes,
                backupCount=config.backup_count
            )
            # No async writer for file, we use executor

    async def flush(self, batch: AggregatedBatch) -> FlushResult:
        start_time = time.monotonic()
        
        # Sampling
        if self.config and self.config.sample_percent < 100.0:
            if random.random() * 100.0 > self.config.sample_percent:
                return FlushResult(
                    backend=self.name,
                    batch_id=str(batch.window_end_ns),
                    success=True,
                    retryable=False,
                    latency_ms=0.0,
                    error="skipped"
                )

        # Prepare Payload
        # Convert dataclasses to dicts for JSON serialization
        payload = {
            "ts": batch.window_end_ns / 1e9,  # Convert ns to seconds
            "worker_id": batch.worker_id,
            "window_len_ms": (batch.window_end_ns - batch.window_start_ns) / 1e6,
            "counters": {k: {"val": v.value, "cnt": v.count} for k, v in batch.counters.items()},
            "gauges": {k: {"val": v.value} for k, v in batch.gauges.items()},
            "timers": {k: {"cnt": v.count, "p95": v.percentiles.get("95", 0)} for k, v in batch.timers.items()},
            "sets": {k: {"cnt": v.count} for k, v in batch.sets.items()},
        }

        try:
            data = json_dumps(payload) + b"\n"
            
            if self._file_handler:
                # Blocking file I/O in executor
                await self._loop.run_in_executor(None, self._emit_file, data)
            elif self._writer:
                # Async stdout/stderr
                self._writer.write(data)
                await self._writer.drain()
            else:
                # Fallback sync stdout/stderr in executor
                dest = sys.stdout if self.config.destination == "stdout" else sys.stderr
                await self._loop.run_in_executor(None, self._emit_sync, dest, data)

            latency = (time.monotonic() - start_time) * 1000
            return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=True,
                retryable=False,
                latency_ms=latency
            )
            
        except Exception as e:
            latency = (time.monotonic() - start_time) * 1000
            return FlushResult(
                backend=self.name,
                batch_id=str(batch.window_end_ns),
                success=False,
                retryable=False,
                latency_ms=latency,
                error=str(e)
            )

    def _emit_file(self, data: bytes):
        if self._file_handler:
            # RotatingFileHandler expects str usually, but we can write bytes if opened in wb?
            # Standard handler opens in 'a'. We need to decode if we want to use standard handler.
            # Or just write raw.
            record = logging.LogRecord("logger_backend", logging.INFO, "", 0, "", (), None)
            record.msg = data.decode("utf-8") # Decode for text handler
            self._file_handler.emit(record)

    def _emit_sync(self, dest: TextIO, data: bytes):
        dest.buffer.write(data)
        dest.flush()

    async def shutdown(self) -> None:
        if self._writer:
            if self.config and self.config.destination in ("stdout", "stderr"):
                # Just flush, never close stdout/stderr
                try:
                    await self._writer.drain()
                except Exception:
                    pass
            else:
                # For other destinations (if we supported TCP etc), close.
                # Note: File destination uses _file_handler, not _writer.
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except Exception:
                    pass
        
        if self._file_handler:
            self._file_handler.close()
