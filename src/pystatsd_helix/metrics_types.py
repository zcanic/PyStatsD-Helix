from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Any, Sequence

class MetricType(str, Enum):
    COUNTER = "counter"
    TIMER = "timer"
    GAUGE = "gauge"
    SET = "set"

@dataclass(slots=True, frozen=True)
class Metric:
    """Base class for all metrics."""
    name: str
    type: MetricType
    tags: Mapping[str, str] = field(default_factory=dict)
    sample_rate: float = 1.0

@dataclass(slots=True, frozen=True)
class CounterMetric(Metric):
    """
    Counter metric.
    Value is the count to add.
    If sample_rate < 1.0, the value should be normalized (value / sample_rate)
    by the aggregator, or pre-normalized by the parser.
    Blueprint 05 says: value /= rate in parser.
    """
    value: float = 0.0

@dataclass(slots=True, frozen=True)
class TimerMetric(Metric):
    """
    Timer metric (also used for Histograms).
    Value is in milliseconds.
    """
    value: float = 0.0

@dataclass(slots=True, frozen=True)
class GaugeMetric(Metric):
    """
    Gauge metric.
    is_delta: True if the value is a change (+/-), False if it's an absolute set.
    """
    value: float = 0.0
    is_delta: bool = False

@dataclass(slots=True, frozen=True)
class SetMetric(Metric):
    """
    Set metric.
    Value is the unique string to count.
    """
    value: str = ""

class MetricParseError(Exception):
    """Raised when a metric cannot be parsed."""
    pass

# --- Aggregator Snapshots ---
# These are used to pass aggregated data to backends.

@dataclass(slots=True, frozen=True)
class CounterSnapshot:
    value: float
    count: int
    sample_rate: float

@dataclass(slots=True, frozen=True)
class GaugeSnapshot:
    value: float
    last_seen_ns: int

@dataclass(slots=True, frozen=True)
class TimerSnapshot:
    count: int
    min: float
    max: float
    mean: float
    stddev: float
    sum: float
    # Percentiles map: "90" -> value, "99" -> value, etc.
    percentiles: Mapping[str, float]

@dataclass(slots=True, frozen=True)
class SetSnapshot:
    cardinality: int
    sample_values: Sequence[str] = field(default_factory=list)
