# 施工蓝图: metrics_types
# 目标文件: src/pystatsd_helix/metrics_types.py

## 1. 核心目标
定义统一的 Metric 数据模型，供 parser -> aggregator -> backend 全链路共享。要求结构紧凑（prefer `__slots__`/`dataclasses`）、类型清晰、易于序列化，并提供 Histogram/T-Digest 友好数据结构。

## 2. 架构指令
- **数据即合约**：所有模块必须只通过 `Metric` 及其子结构通信，禁止传递裸 dict。
- **不可变性**：Metric 对象在解析后不得被修改；需要增量的字段（例如 Gauge delta 标记）应独立字段描述。
- **类型覆盖**：至少支持 Counter, Timer, Gauge, Set, Histogram Bucket (flush 时生成)，Taggable interface。
- **Skeptic 注**：旧设计使用 namedtuple 导致类型难以扩展；此次改用 `@dataclass(slots=True, frozen=True)`。

## 3. 关键依赖
- `dataclasses`, `enum`, `typing`

## 4. API 与类设计
```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

class MetricType(str, Enum):
    COUNTER = "counter"
    TIMER = "timer"
    GAUGE = "gauge"
    SET = "set"

@dataclass(slots=True, frozen=True)
class Metric:
    name: str
    type: MetricType
    value: float | int | str
    sample_rate: float
    tags: Mapping[str, str]

@dataclass(slots=True, frozen=True)
class CounterMetric(Metric):
    value: float

@dataclass(slots=True, frozen=True)
class TimerMetric(Metric):
    value: float  # milliseconds

@dataclass(slots=True, frozen=True)
class GaugeMetric(Metric):
    value: float
    is_delta: bool

@dataclass(slots=True, frozen=True)
class SetMetric(Metric):
    value: str
```
- 可以提供 `MetricFactory`，在 parser 内构建具体类型，便于未来扩展（如 Distribution）。

## 5. 详细实现逻辑
1. **内存优化**
   - 使用 `__slots__` + `frozen=True`，避免遗忘字段及额外 dict。
   - tags 使用 `types.MappingProxyType` 包装不可变映射；若无 tag，使用 shared EMPTY_TAGS。
2. **工厂函数**
   - `def make_counter(name: str, value: float, sample_rate: float, tags: Mapping[str, str]) -> CounterMetric`
   - 方便 parser/aggregator 统一创建。
3. **Histogram 支持**
   - aggregator 内部不会直接存 TimerMetric 列表，而是转换成 `HdrHistogram`；但 flush 输出可定义 `TimerSnapshot` dataclass（p95, count, mean 等），放在 metrics_types 中，供 backend 使用。

## 6. 性能与错误处理
- Metric 对象数量巨大，务必减少 GC 压力；通过 slots/frozen + 共享 tags 减少内存。
- 任何新类型都必须更新此文件并同步 parser/aggregator；README 强调“加类型先写蓝图，再写代码”。