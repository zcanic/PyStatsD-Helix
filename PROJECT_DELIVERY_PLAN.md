# PyStatsD-Helix 项目总计划

> 本文档作为执行团队的“开工入口”，统一梳理项目目标、阶段性产物、关键依赖与协作契约。所有工人应先阅读此计划，再根据 `current/` 目录下的施工蓝图进入具体模块。

## 1. 项目北极星目标
- 交付一套 **纯 Python、可线性扩展、具备标签支持** 的 StatsD 兼容服务器。
- 单节点（4 worker）稳定处理 **≥ 40k UDP pkt/s**，Timer P99 误差 < 1%。
- 具备可插拔后端（Logger、Graphite、扩展接口）与完善的观测、测试、部署手册。
- 在 2026-Q1 完成 MVP 试运行，2026-Q2 发布 GA。

## 2. 范围与路线图
| 阶段 | 时间盒 | 主要交付物 | 退出标准 | 责任模块 |
| --- | --- | --- | --- | --- |
| **MVP (Stage P0)** | 8 周 | UDP ingest、bytes-only parser、Aggregator + Logger/Graphite flush、基础观测指标、CI smoke | 40k pkt/s、P95 flush <1.5s、CI 全绿、Runbook 草稿 | current/01-11、12、13 |
| **P1** | +4 周 | TCP ingest、配置热更新、Sets/Histograms、扩展 backend devkit、Chaos probes | 7 天 soak test 无数据丢失、Chaos 成功、devkit 发布 | current/04-07、08-11、12 |
| **P2** | +4 周 | HA 控制面、SRE Playbook、部署模板（K8s/容器）、多后端策略 | 双 AZ 预演、SRE 交付、发布 GA | current/01-04、12-14 |

## 3. 构建路径（Workstreams）
1. **控制面 & Runtime**  
   - 参考 `current/01_config_control_plane.md`, `current/02_runtime_supervisor.md`, `current/03_runtime_worker.md`。  
   - 先实现配置模型/CLI，再落地 `spawn + uvloop` 进程编排与信号管理。
2. **入口与解析**  
   - `current/04_ingest_transport.md` 明确 UDP/TCP、SO_REUSEPORT、背压策略。  
   - `current/05_parser_engine.md` + `current/06_metrics_types.md` 规范 bytes-only parser 与 Metric dataclass。
3. **聚合与 Flush**  
   - `current/07_aggregator_flush.md` 定义 Aggregator/Flush dispatcher；  
   - `current/08_backends_core.md`-`11_backends_extensibility.md` 提供内置后端与扩展指南。
4. **观测、质量、部署**  
   - `current/12_observability.md`、`current/13_testing_quality.md`、`current/14_deployment_playbook.md` 负责可观测性、测试矩阵、SRE 交付物。

## 4. 关键成功指标（KSIs）
- **性能**：40k pkt/s（MVP），80k pkt/s（P1），flush P95 < 1.5s。  
- **可靠性**：丢包率 <0.3%，指标误差 <1%。  
- **可观测性**：`pystatsd.*` namespace 指标齐全，Chaos/Probe 全绿。  
- **可维护性**：CI 全自动，回归测试覆盖 parser/aggregator/backends/transport 端到端链路。

## 5. 依赖矩阵
| 上游 | 下游 | 契约 | 验证方法 |
| --- | --- | --- | --- |
| config | supervisor/worker | `ServerConfig` schema + immutable copy | Config 单测 + CLI 集成 |
| worker | transport/parser | 事件循环策略（uvloop + asyncio）、SO_REUSEPORT | Worker 集成测试 |
| parser | aggregator | `Metric` dataclass、类型/采样契约 | Parser fixtures + aggregator单测 |
| aggregator | backends | `AggregatedBatch` 结构 | Flush pipeline 集成测试 |
| observability | testing/deployment | 指标 & probe 列表 | CI gating + Runbook |

## 6. 风险与缓解
| 风险 | 描述 | 优先级 | 缓解措施 |
| --- | --- | --- | --- |
| HdrHistogram/T-Digest 构建失败 | C 扩展编译/安装失败导致精度下降 | 高 | 预构建 wheel、安装自检失败即拒绝启动 |
| Worker 负载不均或丢包 | SO_REUSEPORT + UDP 缓冲不足 | 高 | 调整 `SO_RCVBUF` (已实施: 4MB)、监控 `gateway.*` 指标、背压策略 |
| Tag cardinality 爆炸 | `(name,tags)` 无上限导致内存增长 | 中 | `config.aggregation.max_series` + LRU 驱逐 + evict 指标 |
| Backend 阻塞 | Graphite/自定义后端延迟 | 中 | Flush queue + timeout + circuit breaker |

## 7. 协作与治理
- **每日同步**：Runtime、Data Path、Observability、SRE 四条线分别站会，周会合并复盘。
- **变更流程**：所有跨蓝图改动需在 PR 描述中引用相关 `current/XX_xx.md`，若涉及架构调整先提交 ADR（附在 `blueprint/current` 或 `docs/adr/`）。
- **质量门禁**：`current/13_testing_quality.md` 规定的单测/性能/混沌三层测试未通过不得合并至 main。

## 8. 开工前检查清单
- [ ] `current/` 蓝图全量审阅确认无遗漏。  
- [ ] 选择并配置基础依赖（Python 3.12, uvloop, hdrhistogram_py, tdigest 等）。  
- [ ] 建立 `tools/bench_ingest.py` 或等效压测脚本。  
- [ ] 准备统一的日志/监控 namespace。  
- [ ] 约定组件责任人和交付时间。

## 9. 下一步
1. 根据本计划确认责任人和时间线，填入团队排期板。  
2. 从 `current/01_config_control_plane.md` 着手实现配置模型与 CLI 骨架。  
3. 并行启动 `transport/parser` 与 `aggregator` 的原型开发，同时准备性能基准脚本。  
4. 在首个迭代结束前完成 Logger backend 与基础观测指标，供 MVP 自检使用。

> **若计划有更新**：请直接编辑本文件，并同步在 `blueprint/current` 相应蓝图里登记引用，确保所有执行人员使用一致版本。

A. 扩展 Backend 生态 (最有效！)：

目前只有 Logger 和 Graphite。

行动： 既然这是“大数据管理”专业，请立刻增加 InfluxDB Backend、Prometheus Backend、MySQL Backend (存原始数据做分析)。每一个 Backend 都是几百行实打实的代码！

理由： 既增加了 LOC，又体现了“大数据”的扩展性。

B. 增加 CLI 工具链：

现在的 main.py 比较简单。

行动： 编写一个功能强大的 pystatsd-cli 工具，支持 config check (配置校验)、benchmark (自带压测工具)、doctor (环境诊断)。

理由： 工具类代码量大且独立，非常适合凑行数。

C. 引入更复杂的类型提示 (Type Hinting)：

行动： 在所有文件里使用极其严格的 Python 类型提示 (typing 模块)，甚至定义大量的 Protocol 和 TypedDict。

理由： 类型定义也是有效代码，而且显得非常专业！