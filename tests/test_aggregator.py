import asyncio
import time
import pytest
from unittest.mock import MagicMock
from pystatsd_helix.aggregator import Aggregator, AggregatedBatch
from pystatsd_helix.config import ServerConfig
from pystatsd_helix.metrics_types import Metric, MetricType, CounterMetric, TimerMetric

@pytest.fixture
def config():
    return ServerConfig(
        num_workers=1,
        timer_histogram_config=(1, 3600000, 3),
        max_series=1000
    )

@pytest.fixture
def aggregator(config):
    return Aggregator(worker_id=1, config=config)

@pytest.mark.asyncio
async def test_aggregator_double_buffering(aggregator):
    """Test that double buffering works correctly."""
    
    # 1. Receive some metrics
    m1 = CounterMetric(name="test.counter", type=MetricType.COUNTER, value=10.0)
    aggregator.receive(m1)
    
    # Verify active bucket has data
    stats = aggregator.stats()
    assert stats.metrics_received == 1
    assert stats.series_count == 1
    
    # 2. Rotate buffer
    aggregator.rotate_buffer()
    
    # Verify active bucket is empty
    stats = aggregator.stats()
    assert stats.metrics_received == 0
    assert stats.series_count == 0
    
    # 3. Process buffer (should contain the metric)
    now = time.monotonic_ns()
    batch = await aggregator.process_buffer(now, time.time())
    
    assert batch.worker_id == 1
    assert "test.counter" in batch.counters
    assert batch.counters["test.counter"].value == 10.0
    
    # 4. Process again (should be empty or handle None gracefully if logic allows, 
    # but our implementation sets flush_bucket to None)
    # The current implementation returns an empty batch if flush_bucket is None
    batch2 = await aggregator.process_buffer(now, time.time())
    assert len(batch2.counters) == 0

@pytest.mark.asyncio
async def test_aggregator_cardinality_limit(aggregator):
    """Test that cardinality limit works."""
    # Set a small limit for testing
    aggregator.max_series = 5
    
    # Fill up
    for i in range(5):
        m = CounterMetric(name=f"test.c.{i}", type=MetricType.COUNTER, value=1.0)
        aggregator.receive(m)
        
    stats = aggregator.stats()
    assert stats.series_count == 5
    assert stats.metrics_dropped == 0
    
    # Add one more (should trigger eviction)
    m_new = CounterMetric(name="test.c.new", type=MetricType.COUNTER, value=1.0)
    aggregator.receive(m_new)
    
    stats = aggregator.stats()
    assert stats.series_count == 5 # Should stay at max
    assert stats.evictions == 1
    
    # Verify "test.c.0" (the first one) is gone (LRU)
    # We can't easily check internal state, but we can check if it's in the batch
    aggregator.rotate_buffer()
    batch = await aggregator.process_buffer(time.monotonic_ns(), time.time())
    
    assert "test.c.new" in batch.counters
    assert "test.c.0" not in batch.counters

@pytest.mark.asyncio
async def test_aggregator_timer_histogram(aggregator):
    """Test HdrHistogram integration."""
    # Record some timer values
    values = [10, 50, 90, 100]
    for v in values:
        m = TimerMetric(name="test.timer", type=MetricType.TIMER, value=float(v))
        aggregator.receive(m)
        
    aggregator.rotate_buffer()
    batch = await aggregator.process_buffer(time.monotonic_ns(), time.time())
    
    t = batch.timers["test.timer"]
    assert t.count == 4
    assert t.min == 10.0
    assert t.max == 100.0
    assert t.mean == 62.5
    # P50 should be 50 or close to it
    assert 50 <= t.percentiles["p50"] <= 90
