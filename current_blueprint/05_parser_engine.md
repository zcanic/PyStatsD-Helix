# 施工蓝图: parser
# 目标文件: src/pystatsd_helix/parser.py

## 1. 核心目标
将 StatsD 文本协议解析为内部 `Metric` 对象，同时保证分配最小化、对恶意 payload 具备防御能力，并支持扩展（Datadog tag, Influx style）。

## 2. 架构指令
- **零正则表达式**：使用手写状态机/`bytes.find`/`split`；regex 在高吞吐下成本过高。
- **可插拔 tag 解析**：通过策略对象支持不同 tag 语法，默认 Graphite 风格。
- **严格输入上限**：单条 metric 长度 >512 字节视为异常，直接丢弃。
- **返回结构**：`parse(data: bytes) -> Iterable[Metric]` 和错误统计 tuple；不得将错误抛给 transport。
- **Skeptic 对照**：旧设计中 parser 直接构造 dict; 新方案使用 dataclass/TypedDict 并复用对象池减少 GC。

## 3. 关键依赖
- `dataclasses`, `typing`, `collections`（可选 deque）
- 内部：`metrics_types` 中的 `Metric`, `CounterMetric`, `TimerMetric`, `GaugeMetric`, `SetMetric`

## 4. API 与类设计
```python
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .metrics_types import Metric, MetricParseError

class TagParser(Protocol):
    def parse(self, tag_segment: bytes) -> tuple[str, str] | None: ...

@dataclass(slots=True)
class ParseResult:
    metrics: list[Metric]
    errors: int

class StatsDParser:
    def __init__(self, tag_parser: TagParser | None = None, max_metric_len: int = 512) -> None: ...
    def parse(self, payload: bytes) -> ParseResult: ...
```

## 5. 详细实现逻辑
1. **payload 按行拆分**：使用 `payload.split(b"\n")`（返回新 bytes，但可接受）；遍历每行时 `line.strip()`。
2. **line => metric**
   - 找到第一个 `:` 分隔 name 与 sample/value 部分；若缺失或 name 为空 -> 记 error。
   - 在剩余部分查找 `|`; 第一个 `|` 前是 value，后面 segments 依次为 type, optional modifiers（sample rate `@`, tags `#`）。
   - 支持类型：
     - `c`: Counter
     - `ms`/`h`: Timer (HdrHistogram)
     - `g`: Gauge
     - `s`: Set
   - 扩展：`|#tag1:value,tag2:value` => 交给 `tag_parser`。
3. **类型特定逻辑**
   - Counter: value 必须是 float；默认 sample rate 1.0；若 `@0.1` 则 `value /= 0.1`。
   - Timer: value -> float (ms)；后续 aggregator 会记录 histogram。
   - Gauge: 支持 `+/-` 增量语法；`+10` 表示 delta。
   - Set: value 当作字符串，存入 set。
4. **对象重用**
   - parser 可维护一个 `list[Metric]` 缓冲区，每次 `ParseResult` 返回前 `metrics_buffer, self._metrics = self._metrics, []`，减少重复分配。
5. **错误策略**
   - 对于任何转换失败（ValueError），增加 `errors`，继续下一行。
   - 解析结果 `errors` 供 transport/aggregator 记录。
6. **可扩展性**
   - 提供 `register_tag_parser(kind: str, parser: TagParser)`；`StatsDParser` 通过配置选择 `graphite` 或 `datadog` 解析策略。

## 7. Legacy 对照（02_protocol_gateway）
- **硬性限制:** 继承旧文档中的约束：metric 名 <=255 字符、tag 总数 <=20、payload <= 8KiB。若违反，在 parser 层返回错误并让 transport 计数 `parse_errors_total{reason="limit"}`。
- **采样归一:** 旧文稿要求在 parser 直接执行 counter sample normalization；本蓝图已在步骤 2 中明确 `value /= rate`，并把原始 `sample_rate` 保留给 aggregator 用于计数。
- **正交职责:** 与旧文稿不同，我们不在 parser 中实现 ingress 队列或 back-pressure；如仍需观察 queue 占用，应该在 transport/worker 层实现。此处保留 `ParseResult.errors` 以便上游记录。

## 6. 性能与错误处理
- **性能**：解析流程必须为 O(n)；避免在每条 metric 上创建多个 Python 对象。对 sample rate/tags 解析使用简单循环而非 split 多次。
- **错误处理**：在 `max_metric_len` 超过时立即丢弃以防止 DoS；对象池需要 try/finally 保证异常时 buffer 被清空，以免内存泄露。
- **对比旧方案**：老 blueprint 允许 parser 自行 flush；本版本强调 parser 只负责转换，避免功能扩散。