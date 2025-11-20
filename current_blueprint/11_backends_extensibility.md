# 施工蓝图: backend extensibility & tooling
# 目标文件: docs/backends/DEVELOPER.md, src/pystatsd_helix/backends/devkit.py, pyproject entry points

## 1. 核心目标
- 为第三方/内部团队提供一致的后端扩展流程：脚手架、接口、测试、打包与发布。
- 定义 `devkit` 辅助模块，封装常用序列化/重试工具，让插件专注于业务逻辑。
- 通过 CI/CLI 工具 (`pystatsdctl backends`) 提供验证、lint 和兼容性检查，避免“装上即炸”。

## 2. Skeptic 原则
1. **文档即契约**：开发者指南必须由自动生成工具验证，确保描述与实际 API 同步。
2. **兼容性闸门**：外部插件在注册 entry point 前必须通过 `pystatsdctl backends validate`；CI 若检测到未声明 capability/版本，则拒绝发布。
3. **观测先于上线**：devkit 强制每个 backend 记录 flush 指标，否则无法通过 lint。
4. **去中心化责任**：主 repo 不接受随意后端；必须通过扩展机制并由 owning 团队维护。

## 3. 关键组件
- **Developer Guide (`docs/backends/DEVELOPER.md`)**：详细说明如何创建项目、实现接口、写测试、注册 entry point。
- **Devkit (`src/pystatsd_helix/backends/devkit.py`)**：提供装饰器、序列化助手、重试工具、统计采集。
- **CLI (`pystatsdctl backends ...`)**：列出已安装插件、运行验证、生成样板。
- **Templates**：`tools/templates/backend` 目录含 cookiecutter 模板。

## 4. Devkit API 设想
```python
@dataclass(slots=True)
class FlushContext:
    batch: AggregatedBatch
    config: BackendConfig
    loop: asyncio.AbstractEventLoop

class BackendTelemetry:
    def __init__(self, name: str) -> None: ...
    def record_flush(self, success: bool, latency_ms: float, size: int) -> None: ...
    def counter(self, name: str, value: float) -> None: ...

class BaseBackend(Backend, ABC):
    telemetry: BackendTelemetry

    async def setup(self, config: BackendConfig, *, loop) -> None:
        self.telemetry = BackendTelemetry(self.name)
        await self.on_setup(config, loop)

    async def flush(self, batch):
        start = perf_counter()
        try:
            result = await self.on_flush(batch)
            self.telemetry.record_flush(True, elapsed, batch.metric_count)
            return result
        except Exception as exc:
            self.telemetry.record_flush(False, elapsed, batch.metric_count)
            raise

    @abstractmethod
    async def on_setup(...): ...
```
- Devkit 还提供：
  - `def sanitize_metric_name(name: str) -> str`
  - `async def tcp_writer(host, port, *, tls=False, timeout=...)`
  - `RetryPolicy` 数据类（max_attempts, backoff, jitter）。

## 5. CLI / 验证流程
1. `pystatsdctl backends list`：读取 entry points，展示 descriptor。
2. `pystatsdctl backends validate my_backend`：
   - 导入 backend
   - 运行 schema 校验
   - 对 `AggregatedBatch` fixture 执行一次 flush（写入 /dev/null）
   - 检查 telemetry output
3. `pystatsdctl backends scaffold logger-like`：基于模板生成骨架（setup.cfg, tests, README）。
4. `pystatsdctl backends smoke --config config.toml`：加载真实配置并运行 dry-run（不启动 worker），验证 secrets/网络权限。

## 6. 文档生成
- `docs/backends/DEVELOPER.md` 应通过 `mkdocs` 或 `mdx` 自动生成部分内容（例如 API 引用）。
- 章节结构：
  1. 快速入门
  2. 生命周期解释
  3. 配置 Schema 与验证
  4. Telemetry 要求
  5. 测试策略（unit + integration harness）
  6. 发布 checklist（versioning、entry point、签名）

## 7. 测试与 CI
- `tests/backends/test_devkit.py`: 覆盖 BaseBackend hooks、retry helper、telemetry。
- 端到端：运行 fake backend（写入内存），由 worker 驱动 flush，确保 CLI `validate` 可捕获错误。
- CI 任务 `backend-lint`: 运行 `pystatsdctl backends validate --all`，任何失败阻断合并。

## 8. 互动与支持
- 在 README 中公布支持政策：
  - Core 团队维护内置 backends；
  - 社区插件需自带 OnCall；
  - 版本兼容：只保证同 major 版本内 API 稳定。
- Devkit 提供 issue template + GH discussion 分类。

## 9. 遗留对照
- 旧 blueprint 未给出扩展流程，导致“复制 logger backend 改名”式实现。现文档要求遵守 devkit + CLI 检查；任何 PR 若未更新 `docs/backends/DEVELOPER.md` 将被拒绝。
- 插件加载器从“隐式 import”升级为“entry point + allowlist”，本蓝图补上配套作者指南。
