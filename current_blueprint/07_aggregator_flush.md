# 施工蓝图: aggregator & flush pipeline
# 目标文件: src/pystatsd_helix/aggregator.py, src/pystatsd_helix/flush.py

> **Skeptic 提示：** 老版 `03_metric_aggregation_core.md` 与 `04_flush_backend_system.md` 混杂了共享内存、集中队列等假设。本蓝图重写聚合+flush 流程，确保每个 worker 自洽、无共享状态，flush 只负责把 *本 worker* 的 `AggregatedBatch` 扇出到本地 backends。

## 1. 核心目标
- 在单个 worker 内将 `StatsDParser` 产出的 `Metric` 对象聚合成时间窗口快照，满足 P99 精度与内存预算。
- 提供线程安全（进程内事件循环语义）且可预测的 flush 机制：`Aggregator.flush()` -> `FlushDispatcher.dispatch(batch)` -> backends。
- 暴露必要的运行时指标/诊断钩子，便于主进程观测而不需要共享可变状态。

## 2. 架构指令
1. **状态隔离**：`Aggregator` 仅在 worker 协程上下文运行，不得把引用返回给其他进程。若 flush 需要异步操作，使用 `asyncio.Queue` 在同一事件循环内传递。
2. **双缓冲**：`receive()` 只向当前活跃缓冲写入；`flush()` 只在 `asyncio.Lock` 保护下交换引用，整个操作 <1ms，避免阻塞 datagram 读。
3. **HdrHistogram 首选**：timer/histogram 计算必须优先使用 `hdrhistogram_py`；若导入失败，记录 `CRITICAL` 并拒绝启动该 worker（不要悄悄降级）。
4. **Cardinality Guard**：每个 worker 可处理的 `(metric_name,tags)` 组合数量受 `config.aggregation.max_series` 限制；超过时立刻驱逐 LRU 并记录 `aggregation_evictions_total`。
5. **Flush QoS**：flush 线程（协程）不能阻塞 aggregator：即使 backend 阻塞也只能影响当前 worker 的 flush queue，上游必须快速丢弃/降级。

## 3. 关键依赖
- 外部库：`hdrhistogram`, 可选 `tdigest`（仅在 `config.aggregation.allow_tdigest_fallback=True` 时启用）。
- 内部模块：`metrics_types`, `backends.loader`, `backends.base`, `transport`（调用方），`config`（读取阈值）。

## 4. API 设计
```python
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .metrics_types import CounterMetric, GaugeMetric, TimerMetric, SetMetric
from .config import ServerConfig

@dataclass(slots=True)
class AggregatedBatch:
    worker_id: int
    window_start_ns: int
    window_end_ns: int
    counters: dict[str, CounterSnapshot]
    gauges: dict[str, GaugeSnapshot]
    timers: dict[str, TimerSnapshot]
    sets: dict[str, SetSnapshot]
    annotations: dict[str, float]

class Aggregator(Protocol):
    def receive(self, metric: Metric) -> None: ...
    def flush(self, *, now_ns: int) -> AggregatedBatch: ...
    def stats(self) -> AggregatorStats: ...

class FlushDispatcher:
    def __init__(self, config: ServerConfig, backends: Sequence[Backend]) -> None: ...
    async def submit(self, batch: AggregatedBatch) -> None: ...
    async def drain(self) -> None: ...
```

## 5. 实现细节
### 5.1 Aggregator 初始化
- 根据 `ServerConfig.aggregation.shard_count` 创建 `ShardedStore`：`[{"counters": dict(), "gauges": dict(), ...} for _ in range(shards)]`。
- 为 timers 初始化 `HdrHistogram` 实例池：每个 shard 维护一个 histogram 并在 flush 后调用 `.reset()`，避免重新分配。
- 维护 `window_start_ns`（`time.monotonic_ns()`）以便 flush 构造窗口元数据。

### 5.2 receive()
- 通过 `hash(metric.name) ^ hash(metric.tags_id)` 选择 shard；`shard.lock` 是轻量 `asyncio.Lock`（在 uvloop 下代价小）。
- 根据类型调用特定 handler：
  - Counter: `value += metric.value / metric.sample_rate`
  - Gauge: `state = GaugeState(value, is_delta)`；`is_delta=True` 时在 flush 时按最后值输出。
  - Timer: `histogram.record_value(value_ms)`；并更新辅助统计（count, sum, sum_sq）。
  - Set: `set.add(metric.value)` 但限制大小；超过 `max_set_size` -> `set_overflow_total++`。
- 所有 handler 禁止阻塞；异常记录后丢弃该 metric。

### 5.3 flush()
1. `now = time.monotonic_ns()`；若 `now - window_start_ns < min_flush_ns`，直接返回空 batch（防止过度 flush）。
2. 获取全局 `flush_lock`，交换 `active_shards` 与 `flush_shards`（深拷贝指针，而非逐条移动）。
3. 遍历 `flush_shards` 构建 snapshot：
   - CounterSnapshot：`value`, `count`, `sample_rate`
   - GaugeSnapshot：`last_value`, `last_seen_ns`
   - TimerSnapshot：调用 histogram API 生成 percentiles map、count、min、max、avg、stddev。
4. Sets：输出元素数+（可选）top K 示例；若 `config.aggregation.set_mode="approx"`，在 flush 阶段把 set 转为 HyperLogLog sketch。
5. 构造 `AggregatedBatch` 并重置 `flush_shards`（清 dict、调用 histogram.reset()）。
6. 更新 `window_start_ns = now`。

### 5.4 FlushDispatcher.submit()
- 维护 `asyncio.Queue(maxsize=config.flush.queue_max)`；超过容量时丢弃最老 batch，并记录 `flush_queue_drops_total`（宁可丢弃也不阻塞 aggregator）。
- 创建后台任务 `self._pump_task = asyncio.create_task(self._pump())`：
  ```python
  async def _pump(self):
      while True:
          batch = await self._queue.get()
          await self._fan_out(batch)
  ```
- `_fan_out` 并发调用每个 backend 的 `flush(batch)`；可使用 `asyncio.gather(..., return_exceptions=True)` 并为关键 backend 启用重试策略。失败策略与旧 `04_flush_backend_system` 一致，但限定在本 worker 内部，不通知 master。

### 5.5 Backpressure & Circuit Breaker
- 每个 backend runner维护 `BreakerState`（closed/half-open/open）；使用滑动窗口统计失败率。
- 当 breaker open：`flush(batch)` 直接返回降级状态并记录 `flush_backend_blocked_total`。
- 对于 `required` backend，若 breaker open > `config.flush.required_backend_max_downtime`, 触发 worker shutdown（让 master 拉起新进程）。

### 5.6 Shutdown/drain
- Worker 退出前调用 `await dispatcher.drain()`: flush queue emptied, backends `shutdown()`。
- Aggregator 提供 `def drain()`：执行一次 `flush(now_ns=time.monotonic_ns())` 并将 batch 交给 dispatcher，确保最后一批指标不丢失。

## 6. 性能、资源与错误处理
- **CPU**：`receive` 热路径无 allocations（复用 histogram、使用 `__slots__` Metric）；`flush` 操作 O(number_of_series)。
- **内存**： `config.aggregation.max_series` 需与 HDR histogram 内存叠加考虑；为 100k series 时内存约 1.5 GiB。蓝图要求实现 `AggregationMemoryTracker`，每次 flush 统计 bytes 并输出 `aggregation_memory_bytes`。
- **错误隔离**：parser/transport 发生异常不会波及 flush；flush/backends 异常不会反向影响 aggregator（通过 queue decoupling）。
- **差异点**：旧版建议“全局 flush dispatcher”；本蓝图否决该方案，理由：集中队列重建单点瓶颈、打破 shared-nothing。

## 7. 可观察性
- Metrics：
  - `aggregation_metrics_total{type}`
  - `aggregation_evictions_total{reason}`
  - `aggregation_flush_duration_ms`
  - `flush_queue_size`
  - `flush_failures_total{backend}` / `flush_circuit_state{backend}`
- Logs：cardinality 超限、必需 backend 持续失败、HdrHistogram 初始化失败。
- Debug API：在 worker 本地暴露 `/diag/aggregator`（可选），返回 top-N series、当前 queue 深度。

## 8. 测试计划
- 单元：各 metric handler、double-buffer 交换、Histogram 精度对照 known dataset、breaker 状态机。
- 集成：模拟 UDP 流 -> parser -> aggregator -> mock backend，验证 flush 语义和 drop 策略。
- 负载：使用 `bench_aggregator_throughput.py`，目标≥10 万 metrics/s/worker，Drop 率 <1%。
- Chaos：
  - Backend 连续失败 2 分钟 -> breaker 应 open；
  - Cardinality 爆炸（> max_series） -> 只 evict 最老 series + 报警。

## 9. 移植/遗留清理指引
- `03_metric_aggregation_core.md`：保留的内容（HdrHistogram 配置、eviction 指标、benchmark 目标）已纳入此蓝图；其它如“跨 worker back-pressure”需删除。
- `04_flush_backend_system.md`：保留 plugin contract、Logger/Graphite 细节，但限定在 worker 范围；集中 dispatcher 描述可标注为“历史方案”。
- 迁移完成后，请在遗留文件顶部添加链接指向本蓝图并声明“此文档已废弃”，直至完全删除。
