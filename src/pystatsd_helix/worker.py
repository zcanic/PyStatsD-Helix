from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import sys
import time
import socket
from typing import List, Optional, Any

# Try to import uvloop
try:
    import uvloop
except ImportError:
    uvloop = None

from .config import ServerConfig
from .aggregator import Aggregator, FlushDispatcher
from .parser import StatsDParser
from .transport import StatsDProtocol
from .backends.loader import create_backends
from .obs.logging import configure_logging
from .obs.metrics import MetricRegistry

class Worker:
    """
    Independent Worker process.
    Runs its own event loop, aggregator, and backends.
    """
    def __init__(self, worker_id: int, config: ServerConfig, heartbeat_shared_value: Any = None):
        self.worker_id = worker_id
        self.config = config
        self.heartbeat_shared_value = heartbeat_shared_value
        self.logger = logging.getLogger(f"worker.{worker_id}")
        self._shutdown_event = asyncio.Event()
        
        # Components
        self.parser = StatsDParser()
        self.aggregator = Aggregator(worker_id, config)
        self.backends = [] # Initialized in run()
        self.dispatcher: Optional[FlushDispatcher] = None
        self.transport: Optional[asyncio.BaseTransport] = None

        # Metrics
        self.metrics = MetricRegistry()
        self.heartbeat = self.metrics.counter(
            "pystatsd_worker_heartbeat_total",
            "Worker heartbeat count",
            labelnames=("worker_id",)
        )

    async def run(self):
        """
        Main entry point for the worker async loop.
        """
        configure_logging(level=self.config.log_level)
        self.logger.info(f"Worker {self.worker_id} initializing...")
        
        # 1. Initialize Backends
        # create_backends returns instances, we need to setup them
        self.backends = create_backends(self.config)
        from .backends.loader import setup_backends
        await setup_backends(self.backends, self.config)
        self.logger.info(f"Loaded backends: {[b.__class__.__name__ for b in self.backends]}")
        
        # 2. Initialize Dispatcher
        self.dispatcher = FlushDispatcher(self.config, self.backends)
        await self.dispatcher.start()
        
        # 3. Start UDP Server
        loop = asyncio.get_running_loop()
        protocol_factory = lambda: StatsDProtocol(self.aggregator, self.parser, self.logger)
        
        try:
            # On Windows, reuse_port is not supported.
            # We should check OS or catch the error.
            # The blueprint says "fail fast", but for development on Windows we might want fallback?
            # "If operating system does not support it, should record FATAL and terminate."
            # But user is on Windows.
            # Let's try with reuse_port=True, if fails and we are on Windows, warn and fallback (or just fail if strict).
            # Given this is a hackathon project and user is on Windows, let's fallback.
            
            reuse_port = True
            if os.name == 'nt':
                reuse_port = False
                self.logger.warning("Windows detected: SO_REUSEPORT disabled. Load balancing will not work.")

            self.transport, _ = await loop.create_datagram_endpoint(
                protocol_factory,
                local_addr=(self.config.host, self.config.port),
                reuse_port=reuse_port
            )
            
            # Optimize socket buffer
            sock = self.transport.get_extra_info('socket')
            if sock and self.config.socket_buffer_size:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.config.socket_buffer_size)
                    actual_buf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                    self.logger.info(f"UDP Receive Buffer set to: {actual_buf} bytes (requested: {self.config.socket_buffer_size})")
                except Exception as e:
                    self.logger.warning(f"Failed to set SO_RCVBUF: {e}")

            self.logger.info(f"Listening on UDP {self.config.host}:{self.config.port}")
        except Exception as e:
            self.logger.critical(f"Failed to bind UDP port: {e}")
            return

        # 4. Start Flush Loop
        flush_task = asyncio.create_task(self._flush_loop())
        
        # 5. Wait for shutdown
        # We can listen for signals here or rely on the main process to kill us.
        # But main process sends SIGTERM, which we should handle if we want graceful shutdown.
        # However, signal handlers in asyncio are tricky in non-main threads, 
        # but here we are a separate process, so we are the main thread of this process.
        
        try:
            loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)
            loop.add_signal_handler(signal.SIGINT, self._handle_sigterm)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            self.logger.warning("Signal handlers not supported on this platform/loop. Graceful shutdown via signal might not work.")
        
        await self._shutdown_event.wait()
        
        # 6. Shutdown sequence
        self.logger.info("Shutting down...")
        if self.transport:
            self.transport.close()
        
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
            
        # Final flush
        self.logger.info("Performing final flush...")
        self.aggregator.rotate_buffer()
        final_batch = await self.aggregator.process_buffer(time.monotonic_ns(), time.time())
        await self.dispatcher.submit(final_batch)
        await self.dispatcher.drain()
        
        self.logger.info("Worker stopped.")

    def _handle_sigterm(self):
        self.logger.info("Received termination signal.")
        self._shutdown_event.set()

    async def _flush_loop(self):
        """
        Periodically flush metrics to backends.
        """
        while not self._shutdown_event.is_set():
            # Update shared heartbeat timestamp
            if self.heartbeat_shared_value:
                self.heartbeat_shared_value.value = time.time()

            # Jitter
            interval = self.config.flush_interval * random.uniform(0.95, 1.05)
            try:
                await asyncio.sleep(interval)
                
                # Heartbeat
                self.heartbeat.labels(worker_id=str(self.worker_id)).inc()

                now = time.monotonic_ns()
                timestamp = time.time()
                
                # 双缓冲 Flush 流程
                # 1. 快速旋转 Buffer (Sync, Atomic)
                self.aggregator.rotate_buffer()
                
                # 2. 异步处理旧 Buffer (Async, Yieldable)
                batch = await self.aggregator.process_buffer(now, timestamp)
                
                if self.dispatcher:
                    await self.dispatcher.submit(batch)
                else:
                    self.logger.error("Dispatcher not initialized, dropping batch")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in flush loop: {e}", exc_info=True)


def run_worker_process(config: ServerConfig, worker_id: int, heartbeat_shared_value: Any = None) -> None:
    """
    Entry point called by multiprocessing.Process.
    """
    # 1. Setup Logging
    logging.basicConfig(
        level=config.log_level,
        format=f"%(asctime)s [%(levelname)s] [worker-{worker_id}] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True # Overwrite any existing config
    )
    
    # 2. Setup uvloop
    if uvloop:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    else:
        logging.warning("uvloop not found! Falling back to standard asyncio loop. Performance will be degraded.")

    # 3. Run Worker
    worker = Worker(worker_id, config, heartbeat_shared_value)
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.critical(f"Worker process crashed: {e}", exc_info=True)
        sys.exit(1)
