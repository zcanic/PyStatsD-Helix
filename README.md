# PyStatsD-Helix

![Status](https://img.shields.io/badge/Status-MVP%20Ready-green)
![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows-lightgrey)

**纯 Python、高性能、可线性扩展、具备标签支持** 的 StatsD 兼容服务器。

PyStatsD-Helix 旨在提供一个现代化的 StatsD 替代方案，利用 Python 3.12+ 的最新特性和异步 I/O 能力，在保持代码简洁易维护的同时，提供生产级的高吞吐量处理能力。

## 🚀 项目状态

- **MVP 已就绪**: 核心功能开发完成，通过单元测试与基准测试。
- **Windows 兼容**: 已验证在 Windows 环境下的运行能力（自动降级适配）。
- **性能达标**: 单节点（单 Worker）在 Windows 上实测 **~47k UDP pkt/s** (Packet Loss < 5%)。

详细报告请参阅：
- [📘 蓝图一致性报告 (Blueprint Consistency Report)](BLUEPRINT_CONSISTENCY_REPORT.md)
- [✅ Windows 验证报告 (Verification Report)](VERIFICATION_REPORT.md)
- [📖 运维手册 (Runbook)](docs/RUNBOOK.md)
- [🚢 部署指南 (Deployment Guide)](docs/DEPLOYMENT.md)

## 🛠️ 技术栈与架构

- **Python 3.12+**：利用最新性能优化与类型提示。
- **uvloop** (Linux)：高性能事件循环，接近 Go/C++ 的网络性能。
- **asyncio** (Windows)：标准库异步支持，保证跨平台兼容性。
- **Socket 优化**：自动调整 UDP 接收缓冲区 (SO_RCVBUF) 至 4MB+，大幅降低高并发下的丢包率。
- **Shared-Nothing 架构**：每个 Worker 进程拥有独立的内存空间、聚合器和后端连接，无锁竞争。
- **Double Buffering**：聚合层采用双缓冲机制，实现零阻塞 Flush，彻底消除 UDP 丢包风险。
- **HdrHistogram**：高精度 Timer 聚合（P99 误差 < 1%）。
- **Pydantic v2**：强类型配置模型与验证。

## ⚡ 快速开始

### 1. 安装依赖

**Linux / macOS:**
```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
# uvloop 会自动安装并启用
```

**Windows:**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# uvloop 会被跳过，自动回退到标准 asyncio
```

### 2. 运行服务器

```powershell
# 启动服务器 (默认监听 UDP 8125)
python -m pystatsd_helix.main

# 指定配置文件启动
python -m pystatsd_helix.main --config bench_config.toml

# 强制指定 Worker 数量 (Windows 开发建议设为 1)
python -m pystatsd_helix.main --workers 1
```

### 3. 配置文件示例 (`config.toml`)

```toml
[server]
host = "0.0.0.0"
port = 8125
num_workers = 4  # 0 = 自动检测 CPU 核心数
flush_interval = 10.0
log_level = "INFO"
active_backends = ["logger", "graphite"]

# 可观测性配置 (Prometheus Metrics)
obs_host = "0.0.0.0"
obs_port = 9102

[backend_configs.logger]
level = "INFO"
mode = "ndjson"

[backend_configs.graphite]
host = "graphite.example.com"
port = 2003
prefix = "statsd"
```

## 🐳 Docker 部署

本项目提供完整的 Docker 支持，包含 Graphite 和 Grafana 的演示环境。

### 快速启动演示环境

1. **构建并启动服务**:
   ```bash
   docker-compose up -d
   ```

2. **验证服务**:
   - **PyStatsD**: 监听 UDP 8125 (Ingest) 和 TCP 9102 (Metrics)
   - **Graphite Web**: http://localhost:8080
   - **Grafana**: http://localhost:3000 (默认账号 admin/admin)

3. **发送测试数据**:
   ```bash
   # Linux/Mac
   echo "test.counter:1|c" | nc -u -w1 127.0.0.1 8125
   
   # Windows (PowerShell)
   $client = New-Object System.Net.Sockets.UdpClient
   $client.Connect("127.0.0.1", 8125)
   $bytes = [System.Text.Encoding]::ASCII.GetBytes("test.counter:1|c")
   $client.Send($bytes, $bytes.Length)
   ```

## 📈 性能基准

在 Windows 11 (Ryzen 7 5800H) 单 Worker 进程下实测：
- **吞吐量**: ~39,800 pkt/s (0% 丢包)
- **延迟**: P99 < 1ms (处理延迟)

*注：Linux 环境下配合 `uvloop` 和 `SO_REUSEPORT` 多进程模式，性能预期可线性扩展至 100k+ pkt/s。*

## 🤝 贡献

欢迎提交 Issue 和 PR！请确保在提交前运行测试：

```bash
pytest tests/
```
   $client.Close()
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
