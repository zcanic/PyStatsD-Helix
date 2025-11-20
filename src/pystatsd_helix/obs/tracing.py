"""
Tracing module for PyStatsD-Helix.
Wraps OpenTelemetry to provide tracing capabilities.
"""
from __future__ import annotations

import logging
from typing import Optional, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None # type: ignore

class Tracer:
    """
    Wrapper around OTel tracer.
    """
    def __init__(self, name: str):
        self.name = name
        self._tracer = trace.get_tracer(name) if OTEL_AVAILABLE else None

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs):
        if OTEL_AVAILABLE and self._tracer:
            with self._tracer.start_as_current_span(name, **kwargs) as span:
                yield span
        else:
            yield DummySpan()

class DummySpan:
    def set_attribute(self, key: str, value: Any): pass
    def add_event(self, name: str, attributes: Optional[dict] = None): pass
    def set_status(self, status: Any): pass
    def record_exception(self, exception: Exception): pass
    def is_recording(self) -> bool: return False

def get_tracer(name: str) -> Tracer:
    return Tracer(name)

def configure_tracing(service_name: str = "pystatsd-helix"):
    """
    Configure OpenTelemetry tracing.
    """
    if not OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, tracing disabled.")
        return

    provider = TracerProvider()
    # For now, just log to console or no-op if not configured
    # In production, this would be OTLP exporter
    # provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
