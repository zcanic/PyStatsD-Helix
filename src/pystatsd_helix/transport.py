from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple

from .aggregator import Aggregator
from .parser import StatsDParser
from .obs.metrics import MetricRegistry

class StatsDProtocol(asyncio.DatagramProtocol):
    """
    Asyncio DatagramProtocol for receiving StatsD packets.
    This is the hot path.
    """
    def __init__(self, aggregator: Aggregator, parser: StatsDParser, logger: logging.Logger):
        self.aggregator = aggregator
        self.parser = parser
        self.logger = logger
        self.transport: Optional[asyncio.DatagramTransport] = None
        
        # Metrics
        self.metrics = MetricRegistry()
        self.packets_total = self.metrics.counter(
            "pystatsd_gateway_packets_total", 
            "Total number of packets received", 
            labelnames=("protocol",)
        )
        self.bytes_total = self.metrics.counter(
            "pystatsd_gateway_bytes_total", 
            "Total number of bytes received", 
            labelnames=("protocol",)
        )
        self.errors_total = self.metrics.counter(
            "pystatsd_gateway_errors_total", 
            "Total number of packet processing errors", 
            labelnames=("type",)
        )

    def connection_made(self, transport: asyncio.BaseTransport):
        self.transport = transport # type: ignore

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        """
        Process received datagram.
        """
        self.packets_total.labels(protocol="udp").inc()
        self.bytes_total.labels(protocol="udp").inc(len(data))

        # 1. Parse
        # Note: parse returns a result with metrics and error count
        # It does NOT raise exceptions for parsing errors.
        result = self.parser.parse(data)
        
        # 2. Process Metrics
        for metric in result.metrics:
            self.aggregator.receive(metric)
            
        # 3. Log errors if any (rate limited ideally, but simple for MVP)
        if result.errors > 0:
            self.errors_total.labels(type="parse_error").inc(result.errors)
            # In a real high-perf system, we'd increment an internal counter
            # rather than logging every time to avoid log spam.
            self.logger.debug(f"Ignored {result.errors} malformed metrics from {addr}")

    def error_received(self, exc: Exception):
        self.logger.warning(f"UDP transport error: {exc}")

    def connection_lost(self, exc: Optional[Exception]):
        if exc:
            self.logger.error(f"UDP connection lost: {exc}")
        else:
            self.logger.info("UDP connection closed.")
