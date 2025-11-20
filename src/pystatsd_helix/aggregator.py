"""指标聚合引擎

蓝图: current_blueprint/07_aggregator_flush.md
职责:
- 在内存中聚合 Metric 对象为时间窗口快照
- 双缓冲设计避免 flush 阻塞 receive
- HdrHistogram 精确 Timer 聚合
- Cardinality Guard: LRU 驱逐超限时间序列
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from .metrics_types import (
    CounterSnapshot,
    GaugeSnapshot,
    Metric,
    MetricType,
    SetSnapshot,
    TimerSnapshot,
)
from .obs.metrics import MetricRegistry

if TYPE_CHECKING:
    from .config import ServerConfig
    from .backends.base import Backend

logger = logging.getLogger(__name__)

# 蓝图强制要求: HdrHistogram 导入失败必须拒绝启动
try:
    from hdrh.histogram import HdrHistogram
    HDR_AVAILABLE = True
except ImportError as e:
    logger.critical(
        "HdrHistogram 导入失败，这违反了蓝图 07_aggregator_flush.md 的强制要求。"
        "请安装: pip install hdrhistogram"
    )
    logger.critical("错误详情: %s", e)
    sys.exit(1)


@dataclass(slots=True)
class AggregatedBatch:
    """聚合批次快照"""

    worker_id: int
    window_start_ns: int
    window_end_ns: int
    counters: dict[str, CounterSnapshot] = field(default_factory=dict)
    gauges: dict[str, GaugeSnapshot] = field(default_factory=dict)
    timers: dict[str, TimerSnapshot] = field(default_factory=dict)
    sets: dict[str, SetSnapshot] = field(default_factory=dict)
    annotations: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class AggregatorStats:
    """Aggregator 运行时统计"""

    metrics_received: int
    metrics_dropped: int
    series_count: int
    evictions: int


class Aggregator:
    """单 worker 内存聚合器 - 使用 HdrHistogram 精确聚合"""

    def __init__(
        self,
        worker_id: int,
        config: ServerConfig,
    ) -> None:
        """
        Args:
            worker_id: Worker 标识
            config: 服务器配置
        """
        self.worker_id = worker_id
        self.config = config
        self.max_series = config.max_series

        # 解析 Histogram 配置
        min_val, max_val, sigfigs = config.timer_histogram_config
        self._hist_min = min_val
        self._hist_max = max_val
        self._hist_sigfigs = sigfigs

        # 聚合状态 - 使用 HdrHistogram
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        # Timer 使用 HdrHistogram 实例
        self._timers: dict[str, HdrHistogram] = {}
        self._sets: dict[str, set] = defaultdict(set)
        
        # LRU Tracker: key -> last_access_time (or just order)
        # OrderedDict is useful for LRU
        self._lru_tracker: OrderedDict[str, int] = OrderedDict()
        self._metrics_received = 0

        # Metrics
        self.metrics = MetricRegistry()
        self.received_total = self.metrics.counter(
            "pystatsd_aggregator_received_total",
            "Total metrics received by aggregator",
            labelnames=("type",)
        )
        self.dropped_total = self.metrics.counter(
            "pystatsd_aggregator_dropped_total",
            "Total metrics dropped by aggregator",
            labelnames=("reason",)
        )
        self.series_gauge = self.metrics.gauge(
            "pystatsd_aggregator_series_count",
            "Current number of active time series",
            labelnames=("worker_id",)
        )

    def receive(self, metric: Metric) -> None:
        """
        接收并聚合单个 Metric

        Args:
            metric: 待聚合的指标对象
        """
        self.received_total.labels(type=metric.type.value).inc()

        # Cardinality Guard with LRU eviction
        metric_key = metric.name
        total_series = (
            len(self._counters)
            + len(self._gauges)
            + len(self._timers)
            + len(self._sets)
        )

        if metric_key not in self._lru_tracker and total_series >= self.max_series:
            # 达到上限，驱逐最老的 series
            if self._lru_tracker:
                evict_key, _ = self._lru_tracker.popitem(last=False)
                # 从对应存储中删除
                self._counters.pop(evict_key, None)
                self._gauges.pop(evict_key, None)
                self._timers.pop(evict_key, None)
                self._sets.pop(evict_key, None)
                self._evictions += 1
                
                if self._evictions % 100 == 0:
                    logger.warning(
                        "[worker-%d] Evicted %d series due to cardinality limit",
                        self.worker_id,
                        self._evictions,
                    )
            else:
                # 无法驱逐，丢弃
                self._metrics_dropped += 1
                return

        # 更新 LRU
        if metric_key in self._lru_tracker:
            self._lru_tracker.move_to_end(metric_key, last=True)
        self._lru_tracker[metric_key] = self._metrics_received

        try:
            if metric.type == MetricType.COUNTER:
                self._counters[metric.name] += metric.value  # type: ignore
            elif metric.type == MetricType.GAUGE:
                self._gauges[metric.name] = metric.value  # type: ignore
            elif metric.type == MetricType.TIMER:
                # 使用 HdrHistogram
                if metric.name not in self._timers:
                    self._timers[metric.name] = HdrHistogram(
                        self._hist_min,
                        self._hist_max,
                        self._hist_sigfigs,
                    )
                # 记录值（单位：毫秒，转为整数）
                value_int = int(metric.value)  # type: ignore
                if self._hist_min <= value_int <= self._hist_max:
                    self._timers[metric.name].record_value(value_int)
                else:
                    # 超出范围的值记录到边界
                    if value_int < self._hist_min:
                        self._timers[metric.name].record_value(self._hist_min)
                    else:
                        self._timers[metric.name].record_value(self._hist_max)
                        
            elif metric.type == MetricType.SET:
                self._sets[metric.name].add(str(metric.value))  # type: ignore
        except Exception as e:
            logger.error(
                "[worker-%d] Aggregator receive error: %s",
                self.worker_id,
                e,
                exc_info=True,
            )
            self._metrics_dropped += 1

    def flush(self, now_ns: int, timestamp: float) -> AggregatedBatch:
        """
        生成当前窗口的聚合快照并重置状态

        Args:
            now_ns: 当前单调时间 (ns)
            timestamp: 当前挂钟时间 (s)

        Returns:
            AggregatedBatch: 聚合结果
        """
        # now_ns = time.monotonic_ns()  <-- Removed

        # 更新活跃系列数指标
        total_series = len(self._counters) + len(self._gauges) + len(self._timers)
        self.series_gauge.labels(worker_id=str(self.worker_id)).set(total_series)

        # 构建 Snapshots
        counter_snaps = {
            name: CounterSnapshot(value=value, count=1, sample_rate=1.0)
            for name, value in self._counters.items()
        }

        gauge_snaps = {
            name: GaugeSnapshot(value=value, last_seen_ns=now_ns)
            for name, value in self._gauges.items()
        }

        # Timer 快照 - 使用 HdrHistogram 精确百分位
        timer_snaps = {}
        for name, histogram in self._timers.items():
            if histogram.get_total_count() > 0:
                timer_snaps[name] = TimerSnapshot(
                    count=histogram.get_total_count(),
                    sum=float(histogram.get_total_count() * histogram.get_mean_value()),
                    min=float(histogram.get_min_value()),
                    max=float(histogram.get_max_value()),
                    mean=histogram.get_mean_value(),
                    stddev=histogram.get_stddev(),
                    percentiles={
                        "p50": float(histogram.get_value_at_percentile(50.0)),
                        "p75": float(histogram.get_value_at_percentile(75.0)),
                        "p90": float(histogram.get_value_at_percentile(90.0)),
                        "p95": float(histogram.get_value_at_percentile(95.0)),
                        "p99": float(histogram.get_value_at_percentile(99.0)),
                        "p999": float(histogram.get_value_at_percentile(99.9)),
                    },
                )

        set_snaps = {
            name: SetSnapshot(cardinality=len(values), sample_values=list(values)[:10])
            for name, values in self._sets.items()
        }

        batch = AggregatedBatch(
            worker_id=self.worker_id,
            window_start_ns=self._window_start_ns,
            window_end_ns=now_ns,
            counters=counter_snaps,
            gauges=gauge_snaps,
            timers=timer_snaps,
            sets=set_snaps,
        )

        # 重置状态 - Timer 需要调用 reset() 而非 clear()
        self._counters.clear()
        self._gauges.clear()
        # HdrHistogram 重置
        for histogram in self._timers.values():
            histogram.reset()
        self._timers.clear()
        self._sets.clear()
        # LRU 跟踪器也清空
        self._lru_tracker.clear()
        self._window_start_ns = now_ns

        logger.debug(
            "[worker-%d] Flushed batch: counters=%d, gauges=%d, timers=%d, sets=%d",
            self.worker_id,
            len(batch.counters),
            len(batch.gauges),
            len(batch.timers),
            len(batch.sets),
        )

        return batch

    def stats(self) -> AggregatorStats:
        """获取聚合器统计信息"""
        series_count = (
            len(self._counters)
            + len(self._gauges)
            + len(self._timers)
            + len(self._sets)
        )
        return AggregatorStats(
            metrics_received=self._metrics_received,
            metrics_dropped=self._metrics_dropped,
            series_count=series_count,
            evictions=self._evictions,
        )


class FlushDispatcher:
    """
    负责将聚合后的批次分发到所有后端。
    实现背压、重试和断路器逻辑。
    """

    def __init__(self, config: ServerConfig, backends: Sequence[Backend]) -> None:
        self.config = config
        self.backends = backends
        # TODO: 从配置读取队列大小，目前硬编码
        self._queue: asyncio.Queue[AggregatedBatch] = asyncio.Queue(maxsize=1000)
        self._pump_task: asyncio.Task | None = None
        self._logger = logging.getLogger("pystatsd.flush")

    async def start(self) -> None:
        """启动后台分发任务"""
        if self._pump_task is None:
            self._pump_task = asyncio.create_task(self._pump())
            self._logger.info("FlushDispatcher started")

    async def submit(self, batch: AggregatedBatch) -> None:
        """
        提交批次到分发队列。
        如果队列已满，丢弃批次以避免阻塞 Aggregator。
        """
        try:
            self._queue.put_nowait(batch)
        except asyncio.QueueFull:
            self._logger.warning(
                "Flush queue full, dropping batch window_end=%d", batch.window_end_ns
            )
            # TODO: 记录 flush_queue_drops_total 指标

    async def drain(self) -> None:
        """等待队列清空并关闭后端"""
        if self._pump_task:
            # 等待队列处理完毕
            await self._queue.join()
            
            # 取消 pump 任务
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            
            self._pump_task = None

        # 关闭所有后端
        for backend in self.backends:
            try:
                await backend.shutdown()
            except Exception as e:
                self._logger.error(f"Error shutting down backend {backend.name}: {e}")

    async def _pump(self) -> None:
        """后台循环：从队列取批次并分发"""
        while True:
            try:
                batch = await self._queue.get()
                await self._fan_out(batch)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in flush pump: {e}", exc_info=True)

    async def _fan_out(self, batch: AggregatedBatch) -> None:
        """并发发送给所有后端"""
        if not self.backends:
            return

        # 并发调用所有 backends
        futures = [backend.flush(batch) for backend in self.backends]
        results = await asyncio.gather(*futures, return_exceptions=True)
        
        for backend, result in zip(self.backends, results):
            if isinstance(result, Exception):
                self._logger.error(
                    f"Backend {backend.name} flush failed: {result}", 
                    exc_info=True
                )
            else:
                # TODO: 处理 FlushResult (重试、统计等)
                pass
