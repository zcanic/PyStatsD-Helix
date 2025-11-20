from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, List, Tuple, Optional, Mapping

from .metrics_types import (
    Metric,
    CounterMetric,
    TimerMetric,
    GaugeMetric,
    SetMetric,
    MetricType,
)

# Pre-allocate empty mapping for metrics without tags
EMPTY_TAGS: Mapping[str, str] = MappingProxyType({})


class TagParser(Protocol):
    def parse(self, tag_segment: bytes) -> Mapping[str, str]: ...


class GraphiteTagParser:
    """
    Parses tags in Graphite format: tag1=value1;tag2=value2
    Note: The blueprint mentions |#tag:v,tag2:v2 as a generic format,
    but Graphite usually uses semi-colons in the path or a specific tag format.
    Here we implement the standard StatsD tag extension format: |#k:v,k2:v2
    """
    def parse(self, tag_segment: bytes) -> Mapping[str, str]:
        # tag_segment is bytes like b"tag1:val1,tag2:val2"
        # We assume the leading '# ' is stripped by the caller or handled before.
        tags = {}
        if not tag_segment:
            return EMPTY_TAGS
            
        # Split by comma
        parts = tag_segment.split(b",")
        for part in parts:
            if not part:
                continue
            if b":" in part:
                k, v = part.split(b":", 1)
                # Decode keys/values to strings as per Metric definition
                # We use 'replace' to avoid crashing on bad utf-8, though strict might be better for security
                tags[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
            else:
                # Tag without value
                tags[part.decode("utf-8", errors="replace")] = ""
        return tags


@dataclass(slots=True)
class ParseResult:
    metrics: List[Metric]
    errors: int


class StatsDParser:
    """
    High-performance, bytes-only StatsD parser.
    """
    def __init__(self, tag_parser: Optional[TagParser] = None, max_metric_len: int = 512) -> None:
        self.tag_parser = tag_parser or GraphiteTagParser()
        self.max_metric_len = max_metric_len
        # Reusable buffer for metrics to reduce allocation pressure if needed
        # For now, we return a new list per parse call as per blueprint return signature
        
    def parse(self, payload: bytes) -> ParseResult:
        metrics: List[Metric] = []
        errors = 0
        
        # Split payload by newline
        # Note: split returns a list of bytes.
        lines = payload.split(b"\n")
        
        for line in lines:
            # Strip whitespace
            line = line.strip()
            if not line:
                continue
                
            if len(line) > self.max_metric_len:
                errors += 1
                continue
                
            try:
                metric = self._parse_line(line)
                if metric:
                    metrics.append(metric)
                else:
                    errors += 1
            except (ValueError, IndexError, Exception):
                # Catch-all for parsing errors to ensure robustness
                errors += 1
                
        return ParseResult(metrics=metrics, errors=errors)

    def _parse_line(self, line: bytes) -> Optional[Metric]:
        # Format: <name>:<value>|<type>[|@sample_rate][|#tags]
        
        # 1. Extract Name
        # Find the first colon
        colon_idx = line.find(b":")
        if colon_idx == -1:
            return None
            
        name_bytes = line[:colon_idx]
        if not name_bytes:
            return None
            
        name = name_bytes.decode("utf-8", errors="replace")
        
        # 2. Extract Tags (if any)
        # Tags start with |#
        tags = EMPTY_TAGS
        rest = line[colon_idx+1:]
        
        tag_marker_idx = rest.find(b"|#")
        if tag_marker_idx != -1:
            tag_part = rest[tag_marker_idx+2:]
            tags = self.tag_parser.parse(tag_part)
            # Remove tags from rest for further processing
            rest = rest[:tag_marker_idx]
            
        # 3. Extract Value, Type, Sample Rate
        # Remaining format: <value>|<type>[|@sample_rate]
        parts = rest.split(b"|")
        if len(parts) < 2:
            return None
            
        value_bytes = parts[0]
        type_bytes = parts[1]
        
        sample_rate = 1.0
        if len(parts) > 2:
            sample_part = parts[2]
            if sample_part.startswith(b"@"):
                try:
                    sample_rate = float(sample_part[1:])
                except ValueError:
                    return None # Invalid sample rate
        
        # 4. Create Metric based on Type
        # c: Counter, ms/h: Timer, g: Gauge, s: Set
        
        if type_bytes == b"c":
            try:
                val = float(value_bytes)
                # Normalize value if sample rate < 1.0
                if sample_rate > 0 and sample_rate < 1.0:
                    val /= sample_rate
                return CounterMetric(name=name, type=MetricType.COUNTER, value=val, sample_rate=sample_rate, tags=tags)
            except ValueError:
                return None

        elif type_bytes == b"ms" or type_bytes == b"h":
            try:
                val = float(value_bytes)
                return TimerMetric(name=name, type=MetricType.TIMER, value=val, sample_rate=sample_rate, tags=tags)
            except ValueError:
                return None

        elif type_bytes == b"g":
            try:
                # Check for delta (+/-)
                is_delta = False
                if value_bytes.startswith(b"+") or value_bytes.startswith(b"-"):
                    is_delta = True
                
                val = float(value_bytes)
                return GaugeMetric(name=name, type=MetricType.GAUGE, value=val, is_delta=is_delta, sample_rate=sample_rate, tags=tags)
            except ValueError:
                return None

        elif type_bytes == b"s":
            val_str = value_bytes.decode("utf-8", errors="replace")
            return SetMetric(name=name, type=MetricType.SET, value=val_str, sample_rate=sample_rate, tags=tags)

        else:
            # Unknown type
            return None
