# 施工蓝图: observability & resilience
# 目标文件: src/pystatsd_helix/obs/*.py, docs/observability/*

## 1. 核心目标
- 为 ingest → flush 全链路提供统一的 Metrics/Logs/Traces 三件套，并暴露健康探针与 chaos 钩子。
- 将 resilience 策略（back-pressure、circuit breaker、降级开关）编码化，与 Shared-Nothing 架构保持一致。
- 为 SRE/平台团队提供运行、告警与混沌演练的标准化工具链。

## 2. 架构指令
1. **Telemetry 即代码的一部分**：任何新模块都必须通过 `obs.metrics` 提供的 helper 暴露关键指标；未注册的指标在 code review 阶段视为缺陷。
2. **独立事件循环**：Observability 输出（Prometheus/OTLP）运行在 master 进程中，worker 仅以 Logger/Graphite 指标与 flush 后端交互，避免跨进程 exporter。
3. **降级开关集中**：`resilience.py` 暴露 `BackpressurePolicy`, `CircuitBreaker`, `DegradationProfile`，供 gateway/flush 调用；禁止散落在业务代码中的 if/else。
4. **可审计的 Chaos**：提供 `pystatsdctl chaos <scenario>`，每个场景映射到 `chaos/scenarios/*.py`，执行前后都要记录事件。

## 3. 关键依赖
- `prometheus_client` (pull metrics)
- `opentelemetry-sdk` + OTLP exporter
- `structlog`
- 内部：`config`（obs section）、`main`（HTTP server）、`worker`（heartbeat metrics）、`backends`（circuit state）

## 4. API 与模块切分
```text
pystatsd_helix/obs/
  metrics.py      -> helper for defining counters/gauges/histograms
  logging.py      -> structlog config w/ redaction + sampling
  tracing.py      -> OTel provider factory + span helpers
  health.py       -> FastAPI/Starlette endpoints (/health/*, /metrics)
  resilience.py   -> CircuitBreaker, BackpressurePolicy dataclasses
```

### 4.1 metrics.py
- `MetricRegistry` 注册器封装 `prometheus_client`. 每个组件调用 `registry.counter("pystatsd_gateway_packets_total", labels=("protocol",))` 获取懒加载指标。
- 提供 `emit_statsd_loopback()` 将内部指标回写到 LoggerBackend 方便 tail。

### 4.2 logging.py
- 使用 `structlog` + `orjson`，字段遵循 `{ts, level, component, event, worker_id, trace_id, context}`。
- INFO 默认 10% 采样（configurable），WARN/ERROR 全量。
- 提供 `SensitiveFilter`，自动替换 `password|token|secret` 字段为 `***`。

### 4.3 tracing.py
- 创建 `TracerProvider`，默认 Sample 1%，在 `config.observability.trace_sample_percent` 可调。
- Spans：`gateway.receive`, `parser.parse`, `aggregator.flush`, `flush.dispatch`, `backend.flush`。

### 4.4 resilience.py
- `CircuitBreaker`: window=20, failure_threshold=0.5, open_timeout=30s，状态指标 `pystatsd_circuit_state{backend}`。
- `BackpressurePolicy`: 读取 gateway queue/util metrics，决定是否采样丢弃；向 Logger 发 WARN。
- `DegradationProfile`: 允许在“紧急模式”下降低 flush 频率/禁用 tags，由 `pystatsdctl degrade enable` 触发。

## 5. 健康/探针
- `GET /health/live` -> 仅检查 master 线程。
- `GET /health/ready` -> worker 心跳新鲜、flush queue <70%、必需 backend breaker 关闭。
- `GET /health/backends` -> Graphite/Logger breaker 状态 + 最近错误。
- `GET /metrics` -> Prometheus/OpenMetrics。若部署中已有 sidecar，可通过 unix socket 暴露。

## 6. Chaos & Resilience 场景
1. Worker Kill (`chaos.worker_kill`): 验证 master 重启、alert 触发。
2. Backend Blackhole: iptables drop -> breaker open -> degrade hook。
3. Histogram Backend Crash: monkeypatch `hdrhistogram` ImportError，验证 fallback。
4. Tag Storm: Replay 200k unique tags -> eviction 指标 + WARN。
5. Config Flap: 连续 reload -> 观测 `config_reload_latency_seconds`。

## 7. Legacy 对照 (06_observability_and_resilience.md)
- 旧文稿中的指标目录、日志规范、Tracing 拓扑、健康探针与 chaos 场景均被吸收并重新编号；新增要求：所有 resilience 工具集中在 `resilience.py`，避免散布。
- 原文提到的“Shared control socket” 监控方案已被 Worker 自采指标 + master HTTP exporter 替换。
- Alert 阈值列表在 `docs/observability/alerts.md` 中保留，并链接到 module 08 的运行手册。

## 8. 验证
- Unit：metrics helper、circuit breaker 状态机、log redaction。
- Integration：启动完整 daemon，抓取 `/metrics`、`/health/ready`，确保标签/返回码正确。
- Chaos：`pytest -m chaos` 覆盖 5 个场景，必须在 nightly job 中绿灯。
- SLO：Prometheus dashboard `dashboards/obs.json` 显示 ingest/drop/flush/circuit 状态，SRE 以此做签署。
