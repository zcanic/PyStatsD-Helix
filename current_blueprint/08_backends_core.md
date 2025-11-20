# 施工蓝图: backends core
# 目标文件: src/pystatsd_helix/backends/__init__.py, src/pystatsd_helix/backends/base.py, src/pystatsd_helix/backends/loader.py

## 1. 核心目标
- 定义所有后端插件共享的契约（接口、生命周期、配置约束、观测信号）。
- 提供可发现/可热插拔的加载机制（entry points + 显式 allowlist），避免运行期 import 魔法。
- 在 Shared-Nothing 架构下，让每个 worker 独立构建后端实例，禁用跨进程连接复用。

## 2. Skeptic 原则
1. **约束优先于灵活**：后端必须在 `setup -> flush* -> shutdown` 的固定状态机中运行，禁止自定义线程池或全局变量；否则易泄露 socket。
2. **配置强校验**：Loader 只接受 `ServerConfig.backend_configs` 输出的 dataclass；动态参数需显式 schema，拒绝“dict 直塞”。
3. **Fail Fast**：发现未知 backend 名称、缺失 entry point、版本不匹配时立即抛 `BackendLoadError`，不要用 WARNING 静默跳过。
4. **完全隔离**：后端模块内不得引用 transport/parser/aggregator；通信只通过 `AggregatedBatch`。
5. **可测试性**：base 层提供 `InMemoryBackend` 方便单元测试；所有 backends 都应在加载时声明自身 capability（是否支持 tags、是否需要 network）。

## 3. 关键依赖
- `importlib.metadata.entry_points`
- `typing.Protocol`, `abc.ABC`
- 内部：`ServerConfig`, `AggregatedBatch`, `FlushResult`, `BackendConfig`（pydantic）

## 4. API 设计
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Literal, ClassVar

class Backend(Protocol):
    name: ClassVar[str]
    supports_tags: ClassVar[bool]
    required: ClassVar[bool]

    async def setup(self, config: BackendConfig, *, loop: asyncio.AbstractEventLoop) -> None: ...
    async def flush(self, batch: AggregatedBatch) -> FlushResult: ...
    async def shutdown(self) -> None: ...
    def describe(self) -> BackendDescriptor: ...  # static metadata

@dataclass(slots=True)
class BackendDescriptor:
    name: str
    version: str
    supports_tags: bool
    max_batch_size: int
    qos: Literal["critical", "best_effort"]

@dataclass(slots=True)
class FlushResult:
    backend: str
    batch_id: str
    success: bool
    retryable: bool
    latency_ms: float
    error: str | None = None

class BackendLoadError(RuntimeError): ...

class BackendLoader:
    def __init__(self, config: ServerConfig) -> None: ...
    def resolve(self) -> list[Backend]: ...
```

## 5. 实现要点
1. **Entry Point 注册**
   - `pyproject.toml` 中定义 `pystatsd.backends` group。
   - Loader 调用 `entry_points(group="pystatsd.backends")` 并过滤 `config.active_backends`。
   - 对每个 entry point，加载后调用 `backend.describe()` 做版本兼容性检查（例如 `descriptor.version.startswith(APP_VERSION_MAJOR)`）。
2. **配置绑定**
   - `ServerConfig.backend_configs` 为 `BackendConfigs` dataclass；Loader 将 `config.backend_configs.<name>` 传给对应 backend。
   - 缺失配置 -> `BackendLoadError`。
   - 允许 backend 定义 `@classmethod validate_config(cls, config)` 以执行额外检查。
3. **实例化策略**
   - 每个 worker 调用 `BackendLoader(config).resolve()` 得到 backend 类，再逐个 `await backend.setup()`。
   - Loader 必须 `deepcopy` config，防止 backend 修改共享对象。
4. **生命周期**
   - Worker 启动：`setup`；flush 阶段：`await backend.flush(batch)`；shutdown：`await backend.shutdown()`。
   - 若 backend 抛异常，由 FlushDispatcher 捕获 -> 应用 retry/circuit breaker 策略。
5. **内置 `InMemoryBackend`**
   - 仅用于测试：把 batch append 到 `self._buffer`，并暴露 `drain()` 供断言。
6. **安全/隔离**
   - 禁止 Loader 自动导入未在 allowlist 的 entry point；`config.allowed_backend_packages` 决定可用范围。
   - 后端对 secrets 的访问 via config（env var references），Loader 不做字符串拼接。

## 6. 观测与诊断
- Loader 记录 `backend_loaded_total{backend,version}`、`backend_load_failure_total{reason}`。
- Base 层提供 `FlushResult` 结构，FlushDispatcher 统一把 latency/error 指标写入 `flush_failures_total` 等。
- `BackendDescriptor` 供 CLI `pystatsdctl backends list` 调用，列出已加载插件、caps。

## 7. 性能与错误策略
- Loader 仅在 worker 启动时运行，因此可以使用 `entry_points`（较慢）——无须缓存跨进程对象。
- `flush` 调用需异步并发执行；Base 层建议 backend 标明 `max_concurrency`，Dispatcher 依此控制。
- 错误分类：
  - **Load 阶段** -> Fatal，worker 启动失败。
  - **Flush** -> 重试/熔断。
  - **Shutdown** -> 仅记录 warning（避免影响整体退出）。

## 8. 遗留对照
- 老 `04_flush_backend_system.md` 关于“全局 Dispatcher”已被否决；保留其中的插件契约条款并已纳入此蓝图。
- 任何想重新引入集中队列/共享 backend 的提案，必须提交 ADR 并证明不会破坏 Shared-Nothing。
