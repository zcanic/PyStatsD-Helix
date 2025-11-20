# 施工蓝图: testing & benchmarking
# 目标文件: tox.ini, pyproject.toml (tooling), tests/**, benchmarks/**, tools/chaos/**

## 1. 核心目标
- 用可重复的测试/基准体系验证 Shared-Nothing StatsD 的功能、性能、韧性。
- 将质量门控前置到 CI（PR + nightly），防止“先 merge 再修复”的习惯。
- 形成发布证明（release certification）供 SRE 与产品签字。

## 2. 测试金字塔
| 层级 | 范围 | 工具 | 频率 | 成败标准 |
| --- | --- | --- | --- | --- |
| Unit | parser、aggregator shard、config schema 等纯逻辑 | `pytest`, `pytest-asyncio`, `hypothesis` | 每次 commit | 覆盖率 ≥85%，关键路径（parser/timer）100% branch |
| Component | Worker pipeline、flush dispatcher、control API | `pytest`, `docker compose` | 每 PR | 成功率 100%，观察指标无 regress |
| System | 全量 daemon + fake Graphite | `tox -e system`, Kind/K8s | Nightly | 运行 30m 无错误，drops<0.5% |
| Performance | UDP 吞吐、flush 延迟、内存 | `benchmarks/ingest_bench.py`, pktgen | Weekly/发版前 | ≥40k pkt/s、P95 flush<1.5s、RAM<2GiB/worker |
| Chaos | Worker kill、backend blackhole、config flap | `tools/chaos/*.py` + k8s job | Weekly | 自动恢复，无人工干预 |

## 3. 质量闸口
1. `ruff` + `mypy --strict` 零告警。
2. `pytest -m "not chaos"` 必须绿。
3. System/Perf baseline：与 `benchmarks/baselines/*.json` 对比，回归>5% 直接 fail。
4. Chaos: `pytest -m chaos` 在 nightly job 执行，任何失败将自动创建 incident。
5. Windows 兼容：`tox -e system-win` 在 CI 触发一次，保证守护进程在 Windows Server 2022 上可运行（开发者可选择性跳过但需提供说明）。

## 4. 工程与脚本
- `tox.ini`：定义 `lint`, `type`, `unit`, `integration`, `system`, `perf-smoke`, `chaos` 环境，并在 `ci.yml` 中并行执行。
- `Makefile`：本地开发入口，`make test` -> `tox -e lint,type,unit`。
- `benchmarks/`：包含 ingest/flush/histogram accuracy 脚本，支持 `--profile` 输出 `py-spy` flamegraph。
- `tools/packet_gen/`：Rust 或 Go 实现的高吞吐 UDP 发生器，供本地/CI 复现性能。
- `tools/chaos/`：封装 `kubectl`/`ssh` 操作的脚本，支持 `--dry-run`。

## 5. 数据与治具
- `tests/fixtures/metrics/*.jsonl`：真实流量样本（web、电商、IoT）。
- `benchmarks/data/latency_ground_truth.csv`：与 HdrHistogram 对比的黄金数据。
- `tests/fixtures/config/*.toml`：各种合法/非法配置组合。
- Chaos 场景 YAML：描述 fault 注入 sequence + 预期指标。

## 6. 报告与认证
- CI 将 coverage、基准结果上传至 `artifacts/quality/`，Grafana 面板展示趋势。
- 发布前运行 `scripts/release-cert.py`，生成 Markdown 表格列出各 gate 是否 PASS。
- 历史记录保存 90 天，供合规审计。

## 7. Legacy 映射 (07_testing_benchmarking.md)
- 旧文稿中的金字塔、质量闸口、数据集、自动化日程已完全纳入上文，且补充了 stricter lint/type 要求与 Windows 门槛。
- 夜间失败自动建票、Slack 通知机制继续沿用，由 `ci/nightly.yml` 实现。
- 旧文件将在仓库内保留 stub，指向本蓝图。
