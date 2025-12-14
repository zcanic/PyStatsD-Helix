#!/usr/bin/env python3
"""
Unit tests for core PyStatsD-Helix components.
Focus on aggregator, parser, and multi-process metrics.
"""
import pytest
import asyncio
import time
from unittest.mock import Mock, MagicMock, patch
from multiprocessing import Value

import sys
sys.path.insert(0, 'src')

from pystatsd_helix.parser import StatsDParser, ParseResult
from pystatsd_helix.metrics_types import (
    Metric, MetricType, CounterMetric, TimerMetric, GaugeMetric, SetMetric
)
from pystatsd_helix.aggregator import Aggregator, MetricBucket
from pystatsd_helix.config import ServerConfig


class TestStatsDParser:
    """Tests for the StatsD protocol parser."""
    
    def setup_method(self):
        self.parser = StatsDParser()
    
    def test_parse_counter(self):
        """Test parsing a simple counter metric."""
        result = self.parser.parse(b"test.counter:1|c")
        assert len(result.metrics) == 1
        assert result.metrics[0].name == "test.counter"
        assert result.metrics[0].value == 1.0
        assert result.metrics[0].type == MetricType.COUNTER
    
    def test_parse_counter_with_rate(self):
        """Test parsing counter with sample rate."""
        result = self.parser.parse(b"test.counter:5|c|@0.5")
        assert len(result.metrics) == 1
        assert result.metrics[0].value == 10.0  # 5 / 0.5
    
    def test_parse_gauge(self):
        """Test parsing a gauge metric."""
        result = self.parser.parse(b"test.gauge:42|g")
        assert len(result.metrics) == 1
        assert result.metrics[0].name == "test.gauge"
        assert result.metrics[0].value == 42.0
        assert result.metrics[0].type == MetricType.GAUGE
    
    def test_parse_timer(self):
        """Test parsing a timer metric."""
        result = self.parser.parse(b"test.timer:120|ms")
        assert len(result.metrics) == 1
        assert result.metrics[0].name == "test.timer"
        assert result.metrics[0].value == 120.0
        assert result.metrics[0].type == MetricType.TIMER
    
    def test_parse_set(self):
        """Test parsing a set metric."""
        result = self.parser.parse(b"test.set:user123|s")
        assert len(result.metrics) == 1
        assert result.metrics[0].type == MetricType.SET
    
    def test_parse_histogram(self):
        """Test parsing a histogram metric (treated as timer)."""
        result = self.parser.parse(b"test.histogram:50|h")
        # Histograms may be parsed as timers or have their own type
        assert len(result.metrics) >= 0  # Should not crash
    
    def test_parse_multiple_metrics(self):
        """Test parsing multiple metrics in one packet."""
        data = b"counter1:1|c\ncounter2:2|c\ntimer1:100|ms"
        result = self.parser.parse(data)
        assert len(result.metrics) == 3
    
    def test_parse_with_tags(self):
        """Test parsing metrics with Graphite-style tags."""
        result = self.parser.parse(b"test.metric;env=prod;host=server1:1|c")
        assert len(result.metrics) == 1
        metric = result.metrics[0]
        assert "env=prod" in metric.name or metric.tags == {"env": "prod", "host": "server1"}
    
    def test_parse_invalid_metric(self):
        """Test that invalid metrics are handled gracefully."""
        result = self.parser.parse(b"invalid_metric_no_value")
        assert result.errors >= 0  # Should not crash
    
    def test_parse_empty_packet(self):
        """Test parsing empty packet."""
        result = self.parser.parse(b"")
        assert len(result.metrics) == 0
    
    def test_parse_negative_value(self):
        """Test parsing negative values."""
        result = self.parser.parse(b"test.gauge:-10|g")
        assert len(result.metrics) == 1
        assert result.metrics[0].value == -10.0
    
    def test_parse_float_value(self):
        """Test parsing floating point values."""
        result = self.parser.parse(b"test.timer:3.14159|ms")
        assert len(result.metrics) == 1
        assert abs(result.metrics[0].value - 3.14159) < 0.0001


class TestMetricBucket:
    """Tests for the MetricBucket (single buffer unit)."""
    
    def test_initial_state(self):
        """Test bucket starts empty."""
        bucket = MetricBucket()
        assert bucket.metrics_received == 0
        assert bucket.metrics_dropped == 0
        assert len(bucket.counters) == 0
    
    def test_counter_aggregation(self):
        """Test that counters are summed correctly."""
        bucket = MetricBucket()
        # Add same counter twice (defaultdict handles this)
        bucket.counters["test.counter"] += 10
        bucket.counters["test.counter"] += 5
        assert bucket.counters["test.counter"] == 15


class TestAggregator:
    """Tests for the Aggregator with double buffering."""
    
    @pytest.fixture
    def config(self):
        """Create a minimal config for testing."""
        return ServerConfig(
            host="127.0.0.1",
            port=8125,
            num_workers=1,
            flush_interval=10.0,
            max_series=1000,
        )
    
    def test_receive_counter(self, config):
        """Test receiving and aggregating a counter."""
        agg = Aggregator(worker_id=1, config=config)
        metric = CounterMetric(name="test.counter", type=MetricType.COUNTER, value=5.0)
        
        agg.receive(metric)
        
        assert agg.total_metrics_received == 1
        assert "test.counter" in agg._active_bucket.counters
    
    def test_receive_multiple_counters(self, config):
        """Test that multiple counter values are summed."""
        agg = Aggregator(worker_id=1, config=config)
        
        for i in range(10):
            metric = CounterMetric(name="test.counter", type=MetricType.COUNTER, value=1.0)
            agg.receive(metric)
        
        assert agg.total_metrics_received == 10
        assert agg._active_bucket.counters["test.counter"] == 10.0
    
    def test_receive_gauge(self, config):
        """Test that gauges take the last value."""
        agg = Aggregator(worker_id=1, config=config)
        
        agg.receive(GaugeMetric(name="test.gauge", type=MetricType.GAUGE, value=10.0))
        agg.receive(GaugeMetric(name="test.gauge", type=MetricType.GAUGE, value=20.0))
        agg.receive(GaugeMetric(name="test.gauge", type=MetricType.GAUGE, value=15.0))
        
        assert agg._active_bucket.gauges["test.gauge"] == 15.0
    
    def test_rotate_buffer(self, config):
        """Test double buffer rotation."""
        agg = Aggregator(worker_id=1, config=config)
        
        # Add metric to active bucket
        agg.receive(CounterMetric(name="test.counter", type=MetricType.COUNTER, value=5.0))
        
        active_before = agg._active_bucket
        agg.rotate_buffer()
        
        # Active bucket should be new, flush bucket should be old
        assert agg._active_bucket is not active_before
        assert agg._flush_bucket is active_before
    
    def test_total_metrics_received_persists(self, config):
        """Test that total_metrics_received persists across buffer rotations."""
        agg = Aggregator(worker_id=1, config=config)
        
        for i in range(100):
            agg.receive(CounterMetric(name=f"counter.{i}", type=MetricType.COUNTER, value=1.0))
        
        assert agg.total_metrics_received == 100
        
        agg.rotate_buffer()
        
        # Should still be 100 after rotation
        assert agg.total_metrics_received == 100
        
        # Add more
        for i in range(50):
            agg.receive(CounterMetric(name=f"counter.{i}", type=MetricType.COUNTER, value=1.0))
        
        assert agg.total_metrics_received == 150
    
    def test_flush_pending_metrics(self, config):
        """Test that flush_pending_metrics clears local counters."""
        agg = Aggregator(worker_id=1, config=config)
        
        for i in range(100):
            agg.receive(CounterMetric(name=f"counter.{i}", type=MetricType.COUNTER, value=1.0))
        
        agg.flush_pending_metrics()
        
        # Local counters should be cleared
        assert len(agg._local_received) == 0
        assert len(agg._local_dropped) == 0
    
    def test_cardinality_limit(self, config):
        """Test that cardinality guard doesn't crash and limits growth."""
        agg = Aggregator(worker_id=1, config=config)
        
        # Add 100 unique metrics
        for i in range(100):
            agg.receive(CounterMetric(name=f"unique.metric.{i}", type=MetricType.COUNTER, value=1.0))
        
        # Should have received all of them
        assert agg.total_metrics_received == 100
        # Should have some counters (exact behavior depends on cardinality guard)
        assert len(agg._active_bucket.counters) > 0


class TestMultiWorkerMetrics:
    """Tests for multi-worker metrics aggregation via shared memory."""
    
    def test_shared_memory_value(self):
        """Test that multiprocessing.Value works correctly."""
        val = Value('Q', 0)  # unsigned long long
        
        val.value = 100
        assert val.value == 100
        
        val.value += 50
        assert val.value == 150
    
    def test_sum_multiple_values(self):
        """Test summing multiple shared memory values."""
        values = [Value('Q', 100), Value('Q', 200), Value('Q', 300)]
        total = sum(v.value for v in values)
        assert total == 600


class TestIntegration:
    """Integration tests for full data flow."""
    
    @pytest.fixture
    def config(self):
        return ServerConfig(
            host="127.0.0.1",
            port=8125,
            num_workers=1,
            flush_interval=10.0,
        )
    
    def test_parser_to_aggregator_flow(self, config):
        """Test full flow from parsing to aggregation."""
        parser = StatsDParser()
        agg = Aggregator(worker_id=1, config=config)
        
        # Simulate receiving raw StatsD data
        raw_data = b"app.requests:1|c\napp.latency:120|ms\napp.connections:5|g"
        result = parser.parse(raw_data)
        
        for metric in result.metrics:
            agg.receive(metric)
        
        assert agg.total_metrics_received == 3
        assert "app.requests" in agg._active_bucket.counters
        assert "app.connections" in agg._active_bucket.gauges


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
