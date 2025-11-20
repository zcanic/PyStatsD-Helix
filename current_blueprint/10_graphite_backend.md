# 施工蓝图: GraphiteBackend
# 目标文件: src/pystatsd_helix/backends/graphite.py

## 1. 核心目标
- 将 `AggregatedBatch` 转换为 Graphite plaintext 协议格式并可靠地推送到 TCP endpoint。
- 支持批量发送、自动重连、TLS、网络抖动下的退避策略。
- 作为默认 `required=True` backend，任何不可恢复错误都应触发 worker 退出（由 master 交给 SRE 决策）。

## 2. Skeptic 指令
1. **性能优先**：批量写入 1k 行或 64 KiB（取较小者）；禁用逐行发送。
2. **不可 silent fail**：任何 socket 写失败必须记 log + `FlushResult(success=False, retryable=True)`，让 Dispatcher 处理重试/熔断。
3. **时间戳一致**：所有指标使用 flush 结束时间（秒），不可混用 `time.time()`。
4. **Tag 处理**：Graphite 默认不支持 tags；使用 `.` + sanitized tag suffix (`metric.name;tag=value` to `metric.name.tag_value`). 仍需提供 `config.tag_format="graphite|datadog"` 以切换。

## 3. 配置 Schema（`BackendConfigs.graphite`）
```python
class GraphiteConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    host: str = "127.0.0.1"
    port: int = 2003
    prefix: str = "statsd"
    tag_format: Literal["graphite","datadog"] = "graphite"
    enable_tls: bool = False
    ca_file: Path | None = None
    connect_timeout: float = 5.0
    write_timeout: float = 5.0
    batch_size: int = 1000
    batch_bytes: int = 64 * 1024
    max_retries: int = 3
    retry_backoff: float = 1.0
```

## 4. API 结构
```python
class GraphiteBackend(Backend):
    name = "graphite"
    supports_tags = False  # plaintext
    required = True

    async def setup(self, config: GraphiteConfig, *, loop) -> None: ...
    async def flush(self, batch: AggregatedBatch) -> FlushResult: ...
    async def shutdown(self) -> None: ...
```

## 5. 实现步骤
1. **连接管理**
   - 使用 `asyncio.open_connection` 或 `loop.create_connection`（支持 TLS via `ssl.create_default_context`）。
   - 建立 `self._writer`, `self._reader`; 记录 `connected_at`。
   - 提供 `_ensure_connection()`，在 flush 前检查；断开则重试（指数退避 `retry_backoff * 2**attempt`，最高 `max_retries`）。
2. **线协议序列化**
   - Metric 名称：`{prefix}.{namespace}.{metric}`；先 `sanitize(name)`（替换空格/非法字符为 `_`）。
   - 每条记录 `f"{metric_path} {value} {timestamp}\n"`。
   - Counters/gauges -> 单值；Timers -> 输出 count/sum/p95/p99 等多条 metric，名称例如 `timers.<name>.count`。
   - Sets -> 输出 `sets.<name>.count` + 可选 cardinality。
3. **批量发送**
   - 将 lines 聚合到 `bytearray()`；达到 `batch_size` 或 `batch_bytes` 时调用 `_write(buffer)` 并清空。
   - `_write` = `self._writer.write(buffer)` + `await self._writer.drain()`；设置超时 `write_timeout`（`asyncio.wait_for`).
4. **错误路径**
   - `ConnectionResetError` / Timeout -> 标记 `retryable=True`，关闭连接，等待 Dispatcher 决定重试/熔断。
   - 连续失败 > `max_retries` -> 返回失败结果并让 Circuit Breaker 打开；若 backend `required` 且 downtime 超阈值 -> worker shutdown。
5. **TLS & 认证**
   - 当 `enable_tls=True`，要求提供 `ca_file` 或 `ssl.create_default_context()`；`hostname` 取自 `config.host`。
   - 支持可选 `auth_token`（通过 config）。若指定 -> 在每次连接后发送 `"auth <token>\n"`（Graphite plaintext 扩展）。

## 6. 观测指标
- `graphite_backend_connect_total{status}`
- `graphite_backend_flush_duration_ms`
- `graphite_backend_bytes_sent_total`
- `graphite_backend_retry_total`
- `graphite_backend_circuit_state`
- 记录 flush 过程中被丢弃的行数（e.g., 由于 sanitize 失败）。

## 7. 测试矩阵
- 单元：
  - `sanitize` 函数。
  - 批量切分逻辑 (size/bytes)。
  - TLS config 验证。
- 集成：使用 `asyncio.start_server` 模拟 Graphite，验证 reconnect、auth、batching。
- 负载：发送 1k batches/min，保证 CPU < 1 core；`asyncio` profiler 确认无阻塞。
- Chaos：模拟 socket reset；确保 backend 返回 `retryable=True` 并成功重连。

## 8. 遗留对照
- 旧文档推荐在 dispatcher 里集中管理 TCP 连接；现方案改为“每 worker 一条连接”，避免跨进程争抢。
- 过去未强制 TLS；现在 blueprint 要求 Graphite 若部署在不可信网络必须开启 TLS，否则 CI 直接失败（测试可跳过）。
