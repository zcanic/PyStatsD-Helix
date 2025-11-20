【S-Rank 任务简报：Python StatsD 重构可行性分析】
任务目标: 全面评估使用 Python 重构（或实现）一个高性能 StatsD 兼容服务器的可行性、必要性及潜在挑战。
Agent 执行官:claude code
背景: StatsD 是一个广泛使用的网络守护进程，它通过 UDP（或 TCP）监听指标数据（如 Counters, Timers, Gauges），在内存中聚合它们，并在固定的时间间隔（Flush Interval）将聚合结果发送到各种后端（如 Graphite, Prometheus, InfluxDB）。原始实现是 Node.js。
核心调查象限 (必须详细报告):
象限一：【需求与动机分析】(我们为什么要这么做？)
1.1. 痛点挖掘:
调查当前 Node.js 版 StatsD (Etsy 原版及后续 forks) 的主要“痛点”是什么？
搜索指令:
"statsd nodejs performance issues"
"statsd memory leak"
"why replace statsd"
"statsd alternatives" site:reddit.com
"statsd pain points" site:news.ycombinator.com
1.2. Python 生态位的需求:
调查 Python 社区中是否存在对“原生 Python StatsD 服务器”的明确需求？
分析: 开发者是否因为“不想在纯 Python 栈中混入 Node.js 依赖”而感到困扰？他们是否希望用 Python 编写自定义的聚合逻辑或后端插件？
搜索指令:
"python statsd server" pypi
"python statsd daemon" github
"python implementation of statsd" stackoverflow
象限二：【现存生态与竞品分析】(是不是已经有做过了？)
2.1. 现有项目检索 (PyPI & GitHub):
是否存在已有的、成熟的 Python StatsD 服务器 实现？(注意：不是 客户端，客户端很多！)
搜索指令:
pypi search "statsd server"
github search "statsd server" language:python
github search "statsd daemon" language:python
2.2. 竞品评估 (如果存在):
如果找到了实现，对最热门的 1-3 个项目进行深入分析：
维护状态: 最后一次 commit 是什么时候？Issue 是否活跃？
社区接受度: GitHub Stars, Forks, PyPI 下载量？
功能完整性: 它支持哪些 StatsD 指标类型（Gauges, Counters, Timers, Histograms, Sets）？是否支持 Tag (Datadog/InfluxDB 格式)？
性能基准: 它们是否提供了与 Node.js StatsD 的性能对比 (Benchmarks)？
架构: 它是基于 asyncio, multiprocessing 还是其他模型？
象限三：【技术栈与难度评估】(这只“猎物”有多难抓？)
3.1. 核心协议实现:
评估: StatsD 的线路协议 (Line Protocol) 本身的解析难度。
分析: 协议是基于文本的 (e.g., metric:1|c|@0.1)。使用 Python 的标准库（如 re 或 字符串分割）解析的难度和性能如何？
3.2. 高性能网络层:
评估: 构建一个能处理高吞吐量（每秒数十万甚至数百万 UDP 包）的服务器的难度。
分析:
使用 asyncio 的 create_datagram_endpoint 是不是最佳选择？
Python 的 GIL (全局解释器锁) 在这种高 I/O 场景下是否会成为瓶颈？
是否需要 multiprocessing 来利用多核处理 UDP 包？
uvloop (libuv for Python) 在此场景下的性能提升有多大？
3.3. 核心聚合逻辑 (!!! 难度最高的Boss !!!):
评估: 在内存中高效实现所有指标类型的聚合逻辑的复杂度。
分析 (重点):
Counters/Gauges/Sets: 实现简单（加法、赋值、Set 操作）。
Timers/Histograms: 这是最难的部分。StatsD 需要计算百分位数（e.g., p90, p95, p99.9）、均值、标准差等。
问题: 如何在 Python 中高效地存储大量（百万级）的计时数据，并在 flush-interval 结束时快速计算出这些统计数据？是使用 list (内存爆炸) 还是专门的数据结构（如 T-Digest, HDR Histograms 的 Python 实现）？
3.4. 可插拔后端系统:
评估: 设计一个灵活的“后端”系统（将聚合数据发送到 Graphite, Prometheus 等）的架构难度。
分析: 如何设计一个插件式架构，让用户可以轻松添加自己的 Python 后端？
3.5. 性能对标 (最大的风险):
评估: Python 实现版本在性能上（CPU 占用、内存消耗、处理延迟）是否有可能接近或超过 Node.js (V8) 的原生实现？
调查: 查找 Python vs Node.js 在“高吞吐量 UDP 服务器”和“密集型内存计算（聚合）”方面的基准测试。
象限四：【总结与行动建议】
4.1. 综合评估: 结合以上三点，给出一个明确的“Go / No-Go / Go with Caution”建议。
4.2. 最小可行产品 (MVP) 定义: 如果决定“Go”，一个 MVP 版本的 Python StatsD 应该包含哪些最小功能集？(e.g., 仅支持 Counter/Gauge, 仅支持一个 Graphite 后端)。

参考两个研究报告，总任务：【pystatsd重构】
角色：你是本项目的“首席系统架构师”
【你的核心目标】: 你的唯一目标是设计一个完整、详细、深入、生产就绪的 Python StatsD 服务器架构。你将输出一系列 Markdown 格式的“施工蓝图”。
你的目标：对接下来AI进行的任务进行完全、详细、深入的规范和指导，从任务分模块方法，到每一个模块的技术选型、api设计，交互等等等等细枝末节，完成一系列任务必要的设计任务，符合最佳实践。你可以认为即将进行实际构建的AI coder是只会写代码，什么设计都不会的搬砖工，由你完成一切模块的总指挥。
对于每个模块（也就是每一个文件）都会有一个基于你的输出而改编而成的md文件作为开工指导。你必须定义你将要输出的、用于指导“搬砖工 AI”的 .md 文件列表。
输出格式：
一、 模块文件格式
二、每一个不同的md文档的共同内容，包含大致结构、功能、模块、api设计
三、分别每一个md文件特有的内容，包含极其细致的技术选型、ap接口等等等等，不用给出具体代码示例，但是需要清晰说明。