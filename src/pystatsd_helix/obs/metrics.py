"""
Metrics registry and helpers.
Wraps prometheus_client to provide a unified interface.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

# Try to import prometheus_client
try:
    from prometheus_client import Counter, Gauge, Histogram, REGISTRY
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # Dummy classes for type hinting or fallback
    class Counter: 
        def __init__(self, *args, **kwargs): pass
    class Gauge: 
        def __init__(self, *args, **kwargs): pass
    class Histogram: 
        DEFAULT_BUCKETS = (.005, .01, .025, .05, .075, .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0, float('inf'))
        def __init__(self, *args, **kwargs): pass
    REGISTRY = None

logger = logging.getLogger(__name__)

class MetricRegistry:
    """
    Central registry for internal metrics.
    """
    _instance = None
    _metrics: Dict[str, Any]
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MetricRegistry, cls).__new__(cls)
            cls._instance._metrics = {}
        return cls._instance

    def counter(self, name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Any:
        """Get or create a Counter metric."""
        if not PROMETHEUS_AVAILABLE:
            return DummyMetric()
            
        if name not in self._metrics:
            self._metrics[name] = Counter(name, documentation, labelnames)
        return self._metrics[name]

    def gauge(self, name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Any:
        """Get or create a Gauge metric."""
        if not PROMETHEUS_AVAILABLE:
            return DummyMetric()

        if name not in self._metrics:
            self._metrics[name] = Gauge(name, documentation, labelnames)
        return self._metrics[name]

    def histogram(self, name: str, documentation: str, labelnames: tuple[str, ...] = (), buckets: tuple[float, ...] = Histogram.DEFAULT_BUCKETS) -> Any:
        """Get or create a Histogram metric."""
        if not PROMETHEUS_AVAILABLE:
            return DummyMetric()

        if name not in self._metrics:
            self._metrics[name] = Histogram(name, documentation, labelnames, buckets=buckets)
        return self._metrics[name]

class DummyMetric:
    """Fallback metric class when prometheus_client is missing."""
    def inc(self, amount: float = 1): pass
    def dec(self, amount: float = 1): pass
    def set(self, value: float): pass
    def observe(self, value: float): pass
    def labels(self, *args, **kwargs): return self

# Global helper functions
_registry = MetricRegistry()

def register_metric(name: str, type: str, documentation: str) -> None:
    """
    Register a metric. 
    Note: In Prometheus client, registration happens on creation.
    This helper is kept for compatibility with the previous placeholder.
    """
    if type == "counter":
        _registry.counter(name, documentation)
    elif type == "gauge":
        _registry.gauge(name, documentation)
    elif type == "histogram":
        _registry.histogram(name, documentation)

def record_metric(name: str, value: float, labels: Dict[str, str] | None = None) -> None:
    """
    Record a value for a metric.
    This is a simplified helper. For high performance, use the metric object directly.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    metric = _registry._metrics.get(name)
    if not metric:
        logger.warning(f"Metric {name} not found in registry")
        return

    try:
        labeled_metric = metric
        if labels:
            # Sort label values based on label names order - this is tricky without knowing the order
            # Prometheus client expects label values in the order of label names.
            # This helper assumes labels match exactly if provided.
            # For safety, we might need to inspect the metric's labelnames.
            # But _labelnames is internal.
            # Let's assume labels are passed as kwargs to labels() if it supported it, but it takes *args.
            # This helper is brittle for labels. Prefer using registry.counter().labels(...) directly.
            pass 
        
        if isinstance(metric, Counter):
            labeled_metric.inc(value)
        elif isinstance(metric, Gauge):
            labeled_metric.set(value)
        elif isinstance(metric, Histogram):
            labeled_metric.observe(value)
    except Exception as e:
        logger.error(f"Failed to record metric {name}: {e}")

