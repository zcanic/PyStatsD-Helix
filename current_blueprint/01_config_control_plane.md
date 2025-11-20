# 施工蓝图: config
# 目标文件: src/pystatsd_helix/config.py

## 1. 核心目标
建立单一事实来源（SSOT）的配置层，提供强类型、可验证、不可变的运行参数，并确保 worker 进程之间通过序列化/反序列化获得深拷贝的配置对象，进而杜绝“共享可变状态”破坏 Shared-Nothing 的前提。

## 2. 架构指令
- **集中加载，禁止懒惰改写**：配置只允许在主进程中加载一次，通过 `multiprocessing.Process` 参数传递，worker 内部禁止对 `ServerConfig` 做原地修改（如需局部 cache，必须 copy）。
- **多源合并有顺序**：CLI > 环境变量 > TOML/YAML 文件 > 内建默认；任何冲突必须在 `load_config_from_file` 前解决，以免 worker 看到不一致的值。
- **Schema 即文档**：使用 `pydantic.BaseModel` (v2) + 字段描述，生成 JSON Schema 供 UI/自动化引用，避免“隐藏配置”。
- **防御性校验**：对端口、worker 数、flush 间隔、Histogram 范围等进行上/下限检查；一旦发现非法值直接抛出 `ConfigError`，拒绝启动。

## 3. 关键依赖
- **外部库**：`pydantic>=2`, `pyyaml`, `tomllib` (stdlib), `typing_extensions` (用于 Literal/TypedDict 向后兼容)。
- **内部模块**：无（config 位于依赖图根部），但要为 `backends`、`worker`、`main` 等提供数据类。

## 4. API 与类设计
```python
from __future__ import annotations
import os
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, ValidationError

class LoggerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    pretty_print: bool = False
    sample_percent: float = Field(default=100.0, ge=0.0, le=100.0)

class GraphiteConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    host: str = Field(default="127.0.0.1", description="Graphite plaintext endpoint")
    port: int = Field(default=2003, ge=1, le=65535)
    prefix: str = "statsd"
    tag_format: Literal["graphite", "datadog"] = "graphite"
    timeout: PositiveFloat = 5.0
    connect_retry_max: PositiveInt = 3

class BackendConfigs(BaseModel):
    model_config = ConfigDict(frozen=True)
    logger: LoggerConfig = Field(default_factory=LoggerConfig)
    graphite: GraphiteConfig | None = None

class ServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    host: str = "0.0.0.0"
    port: int = Field(default=8125, ge=1, le=65535)
    num_workers: int = Field(default=0, ge=0)
    flush_interval: PositiveFloat = 10.0
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    active_backends: Sequence[str] = Field(default_factory=lambda: ["logger"], min_length=1)
    backend_configs: BackendConfigs = Field(default_factory=BackendConfigs)
    timer_histogram_config: tuple[int, int, int] = (1, 3600000, 3)

    def get_num_workers(self) -> int: ...

class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded/validated."""

def load_config(path: str | Path | None, env: os._Environ[str] | None = None) -> ServerConfig: ...
```

## 5. 详细实现逻辑
1. **入口函数 `load_config`**
   - 解析 CLI 传入路径；若为空则默认 `/etc/pystatsd/config.toml` 或当前目录 `pystatsd.toml`（依优先级尝试）。
   - 读取文件内容：`.toml` 使用 `tomllib.loads`，`.yaml/.yml` 使用 `yaml.safe_load`；其它扩展名立即抛出 `ConfigError`。
   - 合并顺序：
     1. `defaults`（通过 `ServerConfig()` 生成 dict）。
     2. 文件配置。
     3. 环境变量（`PYSTATSD__RUNTIME__HOST` 风格，解析为嵌套 dict）。
     4. CLI overrides（由 `main` 传入 `cli_overrides`）。
   - 最终使用 `ServerConfig.model_validate(merged_dict)`；捕获 `ValidationError` 并转译为人类可读信息。
2. **`get_num_workers`**
   - `0` 表示自动：`cpu_count = os.cpu_count() or 1`；对 >64 核心机器给出 WARN，建议留给系统/监控的 CPU。
   - 如果显式配置超过 CPU 数，记录 WARN 但允许继续。
3. **不可变保证**
   - `model_config.frozen=True` 使实例 hashable/不可变；若 worker 需要 mutable copy（例如 aggregator wants dict of timer config），必须 `config.model_copy(deep=True)`.
4. **后端激活验证**
   - 在 `load_config` 末尾，检查 `active_backends` 中的名称是否在 `BackendConfigs` 属性里；若 Logger 关闭但 Graphite 也未配置，报错。
5. **Histogram 参数 sanity check**
   - `min_val >=1`, `max_val` 至少大于 `min*10`, `sigfigs` ∈ [1,5]；若不符合，抛 `ConfigError`，避免运行时 HdrHistogram 报错。

## 6. 性能与错误处理
- **启动性能**：配置解析只发生一次；I/O 量级为单个文件，可忽略。关键是保证 worker 不会因 lazy 校验在热路径崩溃。
- **错误处理策略**：任何 `ValidationError` 或文件读写异常都转成 `ConfigError` 并带出清晰上下文（字段路径、坏值）。`main` 捕获后打印并退出 `sys.exit(1)`，杜绝“半启动”的危险状态。
- **可观察性**：配置加载成功后记录 SHA256/版本号，方便排查配置漂移；禁止把敏感字段（比如 future backends 的 API key）打印到日志。
- **Skeptic 注**：与原有蓝图相比，此方案强化了不可变化与多源合并顺序，避免“某 worker 看到旧配置”导致 flush interval 不一致的问题；若未来确实需要热更新，必须通过控制平面另起 RFC，而不是在 config 层偷偷支持。

## 7. 控制平面与热更新接口
- **CLI (`pystatsdctl`)：** 在旧 `05_config_control_plane.md` 中列出的 `start/stop/status/reload/drain` 命令必须由本模块提供。实现上，`build_cli()` 负责注册命令，具体执行逻辑委托给 `Supervisor`/`ControlServer`。所有 CLI 操作在执行前调用 `load_config` 并打印 diff（敏感字段脱敏）。
- **REST / Unix Socket：** 默认关闭，如开启需由配置显式 `control.api_enabled=True`。REST 仅暴露 `GET/PUT /config`、`POST /actions/<drain|shutdown>`，并在 schema 中标记哪些字段允许热更新（`restart_required=False`）。Unix socket 用于 master→worker 控制消息（与 runtime 蓝图一致）。
- **热更新许可清单：** 仅 gateway、flush interval、log level、backend enable/disable 可以热更；worker 数、端口、histogram 精度等字段必须要求重启。`ServerConfig` 需提供 `mutable_fields()` 列表供控制平面校验。
- **审计与 RBAC：** 每次配置变更都写入 `config-history/<timestamp>.json`，记录 actor、源（CLI/REST）、diff。暴露 `controlplane_config_version` 指标，并在 CLI `config history` 命令中展示最近 N 次记录。API/CLI token 需要 `viewer/operator/admin` 三种角色，映射到允许的 endpoints。

## 8. Legacy 映射
- 已吸收旧文档关于“多源合并顺序、热更新工作流、CLI/REST/Unix 控制面”的全部内容；但共享控制队列与自动热更策略被拒绝（Shared-Nothing 需要显式重启）。
- `05_config_control_plane.md` 将被替换为废弃提示，指向本蓝图与 `pystatsdctl` 文档。
- 若未来新增控制面功能，必须同步更新 `ServerConfig` schema 以及本文 7、8 节，确保新的控制入口依旧受严格验证与审计。