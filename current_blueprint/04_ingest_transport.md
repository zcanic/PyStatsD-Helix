# 施工蓝图: transport
# 目标文件: src/pystatsd_helix/transport.py

## 1. 核心目标
实现 UDP Datagram 层，负责将原始字节从内核缓冲区搬运到 Parser。强调零拷贝、最少分配、严格的异常隔离，确保 worker 在遭遇恶意流量时也能继续运行。

## 2. 架构指令
- **纯 bytes**：`StatsDProtocol` 只接收 `bytes`，禁止 `decode()` 后再传递给 parser；parser 自己处理 ASCII 转换。
- **背压策略明确**：无法阻止 UDP flood，因此 transport 的责任是尽快丢弃并统计，而不是试图排队。
- **异常隔离**：任何 parsing/aggregation 错误都不得向上传播到事件循环；在 `datagram_received` 中捕获并记录。
- **Instrumentation by default**：提供 `packets_total`, `packets_dropped`, `parse_errors` 等指标，写入 aggregator。
- **Skeptic 注**：旧方案在协议类里悄悄创建 asyncio 任务，导致 flush 与 packet 收取耦合。本版本禁止 transport 直接操作 loop，保持单一职责。

## 3. 关键依赖
- `asyncio.DatagramProtocol`
- 内部：`parser.StatsDParser`, `aggregator.Aggregator`

## 4. API 与类设计
```python
import asyncio
import logging
from typing import Final

class StatsDProtocol(asyncio.DatagramProtocol):
    MAX_PACKET_SIZE: Final[int] = 65535

    def __init__(self, parser: StatsDParser, aggregator: Aggregator) -> None: ...

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None: ...
    def error_received(self, exc: Exception) -> None: ...
    def connection_lost(self, exc: Exception | None) -> None: ...
```

## 5. 详细实现逻辑
1. **初始化**
   - 保存 parser/aggregator 引用；计算 `self.worker_labels = {"worker_id": ..., "host": ...}` 供 metrics 使用。
2. **`datagram_received`**
   - 快速长度检查，超出 `MAX_PACKET_SIZE` 直接丢弃并计数。
   - 将 data 传给 `parser.parse(data)`；parser 返回 `(metric | None, errors)`。
   - 对每条 metric 调用 `aggregator.enqueue(metric)`；若 aggregator 抛出 `QueueFullError`，记录并递增 drop 指标（不要重试）。
   - 尽量避免 Python 层循环：parser 应返回迭代器；但 transport 不得做字符串 split。
3. **`error_received`**
   - 常见错误：`ConnectionRefusedError` (UDP ICMP)；只需记录 DEBUG。
4. **`connection_lost`**
   - Worker 关闭时调用；记录 INFO 并释放 aggregator。
5. **指标记录**
   - Transport 负责在 aggregator 中注册两个 counter：`transport.packets_total`, `transport.packets_dropped`。
   - 可使用 `aggregator.increment(metric_name, value=1, sample_rate=1.0)` 这类 API，以免生成新的 metric 对象。

## 6. 性能与错误处理
- **性能**：`datagram_received` 必须在 <5µs 内完成常规路径；避免 logging I/O，改用抽样（仅每 10k 包记录一次）。
- **内存**：禁止把数据复制到 list 中；parser 若需要分割，必须在 bytes 上操作（`memoryview` + `find`）。
- **错误处理**：transport 绝不能抛异常导致 loop 停止；任何不可恢复错误（例如 aggregator 抛非预期异常）应调用 `self._fatal("...", exc)`，内部触发 worker shutdown。

## 7. Legacy 对照（02_protocol_gateway）
- **队列与丢包策略:** 沿用旧文档的 80% 阈值预警思路，但实现方式放在 aggregator/worker 层：`StatsDProtocol` 暴露 `ingress_queue_utilization()` 回调供 worker 记录，当超过阈值时在 transport 内部采样 drop 并打 `reason="queue_full"` 的计数器。
- **TCP/P1 功能:** 旧蓝图描述的 TCP 可选链路在此保留占位：`StatsDProtocol` 应将 `connection_made`/`data_received` 钩子抽象出来，方便未来扩展；默认实现仍为 UDP-only。
- **Tag/限长守护:** 原文的“<=20 tags / <=8KiB packet”要求已经体现在 parser 蓝图；此处补充一句：transport 在 `len(data) > config.gateway.max_packet_bytes` 时直接 drop 并计数。
- **调试接口:** 若启用 `gateway.debug=true`，transport 可保留最近 10 条解析失败的 payload（通过 `collections.deque`），供 `/diag/gateway` 读取，与旧文稿保持一致。