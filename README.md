# PyStatsD-Helix

**纯 Python、可线性扩展、具备标签支持** 的 StatsD 兼容服务器。

## 项目目标

- 单节点（4 worker）稳定处理 **≥ 40k UDP pkt/s**，Timer P99 误差 < 1%
- 可插拔后端架构（Logger、Graphite、扩展接口）
- 完善的可观测性、测试与部署手册
- 2026-Q1 完成 MVP 试运行，2026-Q2 发布 GA

## 技术栈

- **Python 3.12+**：利用最新性能优化与类型提示
- **uvloop**：高性能事件循环，替代标准 asyncio
- **Pydantic v2**：强类型配置模型与验证
- **HdrHistogram**：高精度 Timer 聚合（P99 误差 < 1%）
- **SO_REUSEPORT**：多 worker 负载均衡
- **Shared-Nothing 架构**：每个 worker 独立内存空间，无共享可变状态

## 快速开始

### 安装依赖

```powershell
# 创建虚拟环境
python -m venv venv
.\venv\Scripts\Activate.ps1

# 安装项目
pip install -e .

# 安装开发依赖
pip install -e ".[dev]"
```

### 运行服务器

```powershell
# 使用默认配置（4 workers, UDP 8125, Logger backend）
pystatsd

# 指定配置文件
pystatsd --config config.toml

# 指定 worker 数量
pystatsd --workers 8
```

### 配置文件示例

创建 `config.toml`：

```toml
[server]
host = "0.0.0.0"
port = 8125
num_workers = 4
flush_interval = 10.0
log_level = "INFO"
active_backends = ["logger", "graphite"]

[backend_configs.logger]
level = "INFO"
mode = "ndjson"
sample_percent = 100.0

[backend_configs.graphite]
host = "graphite.example.com"
port = 2003
prefix = "statsd"
tag_format = "graphite"
```

## 项目结构

```
src/pystatsd_helix/
├── __init__.py
├── main.py              # CLI 入口与 Supervisor
├── config.py            # 配置模型与加载
├── worker.py            # Worker 进程运行时
├── transport.py         # UDP/TCP 传输层
├── parser.py            # StatsD 协议解析器
├── metrics_types.py     # Metric 数据模型
├── aggregator.py        # 指标聚合引擎
├── flush.py             # Flush 调度器
├── observability.py     # 可观测性钩子
└── backends/
    ├── __init__.py
    ├── base.py          # Backend 接口定义
    ├── loader.py        # Backend 加载器
    ├── logger.py        # Logger Backend
    └── graphite.py      # Graphite Backend
```

## 开发指南

### 运行测试

```powershell
# 单元测试
pytest

# 带覆盖率
pytest --cov=src/pystatsd_helix --cov-report=html

# 类型检查
mypy src/
```

### 代码规范

```powershell
# 格式化
ruff format src/ tests/

# Lint 检查
ruff check src/ tests/
```

## 架构设计

本项目严格按照 `current_blueprint/` 目录下的蓝图文档构建：

1. **配置与控制面** (`01_config_control_plane.md`) → `config.py`
2. **运行时 Supervisor** (`02_runtime_supervisor.md`) → `main.py`
3. **Worker 运行时** (`03_runtime_worker.md`) → `worker.py`
4. **传输层** (`04_ingest_transport.md`) → `transport.py`
5. **解析引擎** (`05_parser_engine.md`) → `parser.py`
6. **指标类型** (`06_metrics_types.md`) → `metrics_types.py`
7. **聚合与 Flush** (`07_aggregator_flush.md`) → `aggregator.py`, `flush.py`
8. **后端核心** (`08_backends_core.md`) → `backends/`
9. **Logger Backend** (`09_logger_backend.md`) → `backends/logger.py`
10. **Graphite Backend** (`10_graphite_backend.md`) → `backends/graphite.py`

详细设计与实现说明请参考各蓝图文档。

## 性能目标

| 阶段 | 吞吐量 | Flush 延迟 | 丢包率 | Timer 误差 |
|------|--------|-----------|--------|-----------|
| MVP (P0) | 40k pkt/s | P95 <1.5s | <0.3% | P99 <1% |
| P1 | 80k pkt/s | P95 <1.5s | <0.3% | P99 <1% |

## 许可证

MIT License

## 贡献指南

1. 所有代码变更必须遵循对应蓝图文档的技术规范
2. 提交 PR 前运行完整测试套件与类型检查
3. 重大架构调整需先提交 ADR（Architecture Decision Record）
4. 质量门禁：未通过 `current_blueprint/13_testing_quality.md` 规定的三层测试不得合并

---

**项目状态**：🚧 开发中 - MVP (P0) 阶段

有关详细的项目计划与路线图，请参考 `PROJECT_DELIVERY_PLAN.md`。
