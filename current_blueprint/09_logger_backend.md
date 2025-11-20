# 施工蓝图: LoggerBackend
# 目标文件: src/pystatsd_helix/backends/logger.py

## 1. 核心目标
- 提供零依赖、可本地调试的指标输出方式，便于开发/验收/备份 flush。
- 支持 NDJSON 与 human-friendly 两种格式，保证在高吞吐下仍可采样/限速。
- 作为 `required=False` 的 best-effort backend：失败不会阻止 worker，但必须输出诊断信息。

## 2. Skeptic 指令
1. **不可用日志即 bug**：LoggerBackend 唯一职责是可靠写日志；若因配置错误无法运行，必须在 worker 启动时失败。
2. **采样控制**：默认仅记录 10% flush（可配置），防止在 100k pkt/s 场景下刷爆磁盘。
3. **结构化输出**：所有记录使用 JSON 并包含 batch 元数据，禁止随意拼接字符串。
4. **线程/协程安全**：backend 在单 worker loop 内运行，不得创建额外线程写文件。

## 3. 配置 Schema（嵌入 `BackendConfigs.logger`）
```python
class LoggerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    level: Literal["DEBUG","INFO","WARNING","ERROR"] = "INFO"
    mode: Literal["ndjson","pretty"] = "ndjson"
    sample_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    destination: Literal["stdout","stderr","file"] = "stdout"
    file_path: Path | None = None
    max_bytes: int = Field(default=10 * 1024 * 1024)
    backup_count: int = 5
```
- 当 `destination="file"` 时启用 `RotatingFileHandler`；否则使用 `asyncio` 友好的 stdout/stderr writer。

## 4. API 结构
```python
class LoggerBackend(Backend):
    name = "logger"
    supports_tags = True
    required = False
    max_batch_size = 1  # 按 batch 级别写出

    async def setup(self, config: LoggerConfig, *, loop) -> None: ...
    async def flush(self, batch: AggregatedBatch) -> FlushResult: ...
    async def shutdown(self) -> None: ...
```

## 5. 实现要点
1. **初始化**
   - 验证 `destination`：
     - stdout/stderr -> `self._writer = asyncio.StreamWriter(sys.stdout.buffer, ...)`。
     - file -> 使用 `logging.handlers.RotatingFileHandler`，再通过 `loop.run_in_executor` 写入（避免阻塞）。
   - 预构建 JSON encoder（`orjson` 优先，否则 `json`）。
2. **flush**
   - 采样逻辑：`if random.random() > sample_percent/100: return FlushResult(success=True, skipped=True)`。
   - 序列化：
     ```json
     {
       "ts": 1730000000.123,
       "worker_id": 3,
       "batch_id": "...",
       "counters": {...},
       "gauges": {...},
       "timers": {...},
       "flush_duration_ms": 12.3
     }
     ```
   - 写入完成后 `await writer.drain()`；若 file 模式 -> `await loop.run_in_executor(None, handler.emit, record)`。
3. **限流 & backpressure**
   - 记录 `emit_latency_ms`; 若 > `config.max_emit_latency_ms`（默认 50ms），打印 WARN 并建议关闭 LoggerBackend。
4. **错误处理**
   - 写入异常 -> 返回 `FlushResult(success=False, retryable=False)`，让 Dispatcher 打印日志但不重试。

## 6. Observability
- 自身指标：`logger_backend_flush_total{status}`、`logger_backend_bytes_total`、`logger_backend_sample_percent_effective`。
- 日志格式自举：LoggerBackend 记录 `logger_backend` 事件，包含 config 摘要（但避免泄露路径）。

## 7. 测试
- 单元：采样逻辑、JSON 内容、file rotation、错误路径。
- 集成：启动 worker -> LoggerBackend -> `pytest` 捕获 stdout，校验 flush 输出。
- 压力：模拟 1k flush/min，确认 writer 不阻塞 flush queue。

## 8. 遗留对照
- 老 `04_flush_backend_system` 中“Logger backend only for debugging”观点被推翻：此实现需 production-ready，可用于审计。任何想弱化其可靠性者需额外评审。
