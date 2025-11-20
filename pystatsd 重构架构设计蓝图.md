

# **PyStatsD-Helix: 架构施工蓝图**

本文档是 PyStatsD-Helix 项目的综合施工蓝图。它旨在供 AI 编码器（执行者）在首席系统架构师的指导下直接使用。

目标是创建一个高性能的 Python StatsD 服务器，其架构设计旨在消除已知的瓶颈（如 Node.js 的 CPU 锁）并规避 CPython GIL 的限制。

执行必须 *严格* 遵守每个模块中列出的规范。

---

## **I. 模块蓝图文件列表**

项目将基于以下 13 个模块蓝图文件构建。文件名反映了 src/pystatsd\_helix/ 的预期目录结构。

1. 00\_README\_ARCHITECTURE.md  
2. 01\_pystatsd\_helix/config.md  
3. 02\_pystatsd\_helix/main.md  
4. 03\_pystatsd\_helix/worker.md  
5. 04\_pystatsd\_helix/transport.md  
6. 05\_pystatsd\_helix/parser.md  
7. 06\_pystatsd\_helix/aggregator.md  
8. 07\_pystatsd\_helix/metrics\_types.md  
9. 08\_pystatsd\_helix/backends/abc.md  
10. 09\_pystatsd\_helix/backends/logger.md  
11. 10\_pystatsd\_helix/backends/graphite.md  
12. 11\_pystatsd\_helix/backends/loader.md  
13. 12\_pyproject.md

---

## **II. 通用蓝图内容结构**

13 个 .md 文档中的每一个都将严格遵循以下结构。这将确保执行者的一致性和清晰度。

# **施工蓝图: \[模块名称\]**

# **目标文件: \[src/pystatsd\_helix/filename.py\]**

## **1\. 核心目标**

(本模块核心且唯一职责的简要说明。)

## **2\. 架构指令**

(基于可行性报告的、不可协商的要求。例如：“禁止使用 're' 模块”或“必须使用 'HdrHistogram\_py'”。)

## **3\. 关键依赖**

* **外部库:** (例如: uvloop, pydantic)  
* **内部模块:** (例如:.config,.parser)

## **4\. API 与类设计**

(完整的“头文件”规范。类名、公共/私有方法、函数签名，包括参数和返回值的类型注解。)

## **5\. 详细实现逻辑**

(关于代码应如何行为的、逐步的、算法式的描述。执行者将把这些逻辑直接转换为 Python 代码。)

## **6\. 性能与错误处理**

(具体的性能要求，应避免什么（例如：阻塞），以及如何处理预期的异常。)

---

## **III. 各蓝图文件详解**

以下是 13 个文件分别需要实现的详细内容。

### **1\. 00\_README\_ARCHITECTURE.md**

# **施工蓝图: 架构概览**

# **目标文件: README.md**

## **1\. 核心目标**

向执行者提供其正在构建的“Shared-Nothing”（无共享）架构的高级概览，并解释各组件如何交互。

## **2\. 架构指令**

* **架构基石:** uvloop \+ multiprocessing \+ SO\_REUSEPORT。1  
* **设计哲学:** “Shared-Nothing”（无共享）架构。工作进程（Worker）完全独立，互不通信。  
* **扩展方式:** 通过启动 N 个进程（每个 CPU 核心一个）来实现扩展，由 Linux 内核分配 UDP 负载。1

## **3\. 详细实现逻辑**

### **“Shared-Nothing”（无共享）架构**

系统由一 (1) 个主进程和 N 个工作进程（Worker）组成。

1. **主进程 (main.py):**  
   * 它 *不* 执行任何网络工作。  
   * 其 *唯一* 任务是读取配置，并启动 N 个子工作进程。N 默认为 CPU 核心数。  
   * 它监控子进程，并确保在收到 SIGINT/SIGTERM 信号时正确终止它们。  
2. **工作进程 (worker.py):**  
   * 启动 N 个 *完全独立* 的实例。  
   * 它们 *不* 使用 multiprocessing.Queue、Pipe 或 SharedMemory。**没有 IPC（进程间通信）。**  
   * 每个工作进程本质上都是一个 *完整、独立的 StatsD 服务器*。  
   * 它使用 SO\_REUSEPORT 监听同一个端口 (0.0.0.0:8125)。Linux 内核负责在 N 个进程之间分配 UDP 数据包。1  
   * 每个 Worker 都有自己的内存中 Aggregator。  
   * 每隔 flush\_interval 秒，*每个* Worker *独立地* 将 *其* 聚合数据刷新（flush）到后端（例如 Graphite）。

### **数据流 (单个 Worker 内部)**

 \-\> \[Linux 内核\]  
|  
(SO\_REUSEPORT 负载均衡)  
|  
     \+--\> Worker 1 (Core 1\)  
     \+--\> Worker 2 (Core 2\)  
     \+--\> Worker N (Core N)

Worker 内部:  
1\. 接收 \`data: bytes\`  
2\. \-\> \[Parser.parse(data)\]  
3\. \[Parser\] \-\> \[ParsedMetric\] (解析后的指标对象)  
4\. \[Aggregator.process(ParsedMetric)\] (在内存中更新聚合)

周期性 (例如每 10 秒):  
1\. \[Aggregator.flush()\] \-\> \[MetricsPayload\] (待刷新的数据)  
2\. \-\> \[Graphite/Logger\]

最终的后端（Graphite）负责对来自 N 个 Worker 的数据进行最终聚合（这对它来说是标准操作）。

### **2\. 01\_pystatsd\_helix/config.md**

# **施工蓝图: config**

# **目标文件: src/pystatsd\_helix/config.py**

## **1\. 核心目标**

使用 pydantic 为整个应用定义一个严格类型化、可验证的配置。

## **2\. 架构指令**

* 配置必须是集中式的，并且在加载后不可变。

## **3\. 关键依赖**

* **外部库:** pydantic  
* **内部模块:** (无)

## **4\. API 与类设计**

Python

import os  
from pydantic import BaseModel, Field  
from typing import Literal

\# \--- 特定后端的配置 \---

class LoggerConfig(BaseModel):  
    """LoggerBackend 的配置。"""  
    level: Literal \= "INFO"  
    pretty\_print: bool \= False

class GraphiteConfig(BaseModel):  
    """GraphiteBackend 的配置。"""  
    host: str \= "127.0.0.1"  
    port: int \= 2003  
    prefix: str \= "statsd"  
    \# 标签格式: 'graphite' (name;tag=v) 或 'datadog' (name\[tag:v\])  
    tag\_format: Literal\["graphite", "datadog"\] \= "graphite"  
    timeout: float \= 5.0

\# \--- 所有后端的配置容器 \---

class BackendConfigs(BaseModel):  
    """  
    一个容器，包含 \*所有\* 可能后端的配置。  
    活动的后端由 ServerConfig.active\_backends 定义。  
    """  
    logger: LoggerConfig \= Field(default\_factory=LoggerConfig)  
    graphite: GraphiteConfig | None \= None  
    \# 未来的插件 (例如 'influxdb') 将添加于此  
    \# influx: InfluxConfig | None \= None

\# \--- 主服务器配置 \---

class ServerConfig(BaseModel):  
    """应用的主配置。"""  
    host: str \= "0.0.0.0"  
    port: int \= 8125

    \# 0 \= 使用 os.cpu\_count()  
    num\_workers: int \= Field(default=0, ge=0)   
      
    flush\_interval: float \= Field(default=10.0, gt=0)  
    log\_level: Literal \= "INFO"

    \# 要激活的后端名称列表。  
    \# 名称必须匹配 BackendConfigs 中的键  
    \# 和 entry\_points 中的注册名。  
    active\_backends: list\[str\] \= \["logger"\]

    \# 包含所有后端配置的嵌套对象  
    backend\_configs: BackendConfigs \= Field(default\_factory=BackendConfigs)

    \# HdrHistogram 的配置  
    \# (min, max, significant\_figures)  
    \# (最小值, 最大值, 有效数字位数)  
    timer\_histogram\_config: tuple\[int, int, int\] \= (1, 60000, 3) \# 1ms-1min, 3 位有效数字

    def get\_num\_workers(self) \-\> int:  
        """返回要启动的实际 Worker 数量。"""  
        if self.num\_workers \== 0:  
            count \= os.cpu\_count()  
            return count if count else 1  
        return self.num\_workers

def load\_config\_from\_file(path: str) \-\> ServerConfig:  
    """  
    从 TOML 或 YAML 文件加载配置。  
    实现应根据文件扩展名确定类型。  
    """  
  ...

## **5\. 详细实现逻辑**

* load\_config\_from\_file:  
  1. 读取 path 处的文件内容。  
  2. 使用 tomllib (针对 .toml) 或 PyYAML (针对 .yaml/.yml) 将内容解析为 dict。  
  3. 返回 ServerConfig.model\_validate(parsed\_dict)。

### **3\. 02\_pystatsd\_helix/main.md**

# **施工蓝图: main**

# **目标文件: src/pystatsd\_helix/main.py**

## **1\. 核心目标**

应用的入口点 (main)。*仅* 负责启动和管理 N 个子工作进程的生命周期。

## **2\. 架构指令**

* **必须:** 使用 multiprocessing 创建 N 个进程以绕过 GIL。1  
* **禁止:** 此进程 *不得* 执行任何网络工作或指标聚合。只做进程管理。

## **3\. 关键依赖**

* **Stdlib:** multiprocessing, sys, os, signal, logging, time  
* **内部模块:** .config.ServerConfig, .config.load\_config\_from\_file

## **4\. API 与类设计**

Python

import asyncio  
import uvloop  
import logging  
import signal  
import sys  
import os  
import time  
import multiprocessing  
from.config import ServerConfig, load\_config\_from\_file  
from.worker import Worker

def run\_worker\_process(config: ServerConfig, worker\_id: int):  
    """  
    在 \*子进程内部\* 执行的目标函数。  
    设置并运行单个 Worker。  
    """  
  ...  
      
def main():  
    """  
    主入口点。运行主进程管理器。  
    """  
  ...

## **5\. 详细实现逻辑**

### **main()**

1. 为 *主* 进程配置基础日志记录 (例如：\[MainProcess\]...)。  
2. 解析 sys.argv 以获取配置文件路径 (例如：--config \<path\>)。  
3. config \= load\_config\_from\_file(path)  
4. num\_to\_spawn \= config.get\_num\_workers()  
5. logger.info(f"启动 {num\_to\_spawn} 个工作进程...")  
6. processes: list\[multiprocessing.Process\] \=  
7. 设置 multiprocessing.set\_start\_method("spawn") (如果需要，以确保干净的启动状态)。  
8. **启动循环:**  
   Python  
   for i in range(num\_to\_spawn):  
       p \= multiprocessing.Process(  
           target=run\_worker\_process,   
           args=(config, i \+ 1)  
       )  
       p.start()  
       processes.append(p)  
       logger.info(f"工作进程 {p.pid} (ID: {i+1}) 已启动。")

9. **设置优雅关闭:**  
   * shutdown\_event \= multiprocessing.Event()  
   * 创建 handle\_signal(sig, frame) 函数 (用于 SIGINT, SIGTERM)。  
   * 在 handle\_signal 内部: logger.warning("收到终止信号..."); shutdown\_event.set()  
   * signal.signal(signal.SIGINT, handle\_signal)  
   * signal.signal(signal.SIGTERM, handle\_signal)  
10. **监控循环:**  
    * while not shutdown\_event.is\_set():  
    * time.sleep(1.0)  
    * (可选：检查 p.is\_alive() 并在 Worker 意外死亡时重启它，但 MVP 阶段非必需)。  
11. **终止逻辑:**  
    * logger.info("正在停止工作进程...")  
    * for p in processes: p.terminate()  
    * for p in processes: p.join()  
    * logger.info("所有工作进程已停止。退出。")

### **run\_worker\_process()**

1. **必须:** 为此进程 *重新* 配置日志记录。日志应包括 worker\_id 或 os.getpid()，例如：...。  
2. logger.info("工作进程已初始化。")  
3. **必须:** 在 *子进程内部* 设置 uvloop。  
   Python  
   import uvloop  
   import asyncio  
   asyncio.set\_event\_loop\_policy(uvloop.EventLoopPolicy())

4. worker \= Worker(config)  
5. try:  
6. asyncio.run(worker.run())  
7. except KeyboardInterrupt: (尽管信号应在 main 中被捕获)  
8. pass  
9. finally:  
10. logger.info("工作进程正在关闭。")

### **4\. 03\_pystatsd\_helix/worker.md**

# **施工蓝图: worker**

# **目标文件: src/pystatsd\_helix/worker.py**

## **1\. 核心目标**

定义 Worker 类。此类封装了 *单个* 独立 StatsD 服务器的 *所有* 逻辑：网络设置、接收、聚合和周期性刷新。

## **2\. 架构指令**

* **必须:** 使用 uvloop (在 main.py/run\_worker\_process 中设置)。1  
* **必须:** 在 create\_datagram\_endpoint 中包含 reuse\_port=True，这是 asyncio 中 SO\_REUSEPORT 的实现。1

## **3\. 关键依赖**

* **Stdlib:** asyncio, logging, os  
* **内部模块:** .config.ServerConfig, .aggregator.Aggregator, .parser.Parser, .transport.StatsDProtocol, .backends.loader.load\_active\_backends

## **4\. API 与类设计**

Python

import asyncio  
import logging  
import os  
from.config import ServerConfig  
from.aggregator import Aggregator  
from.parser import Parser  
from.transport import StatsDProtocol  
from.backends.loader import load\_active\_backends  
from.backends.abc import AbstractBackend

class Worker:  
    def \_\_init\_\_(self, config: ServerConfig):  
        """初始化 Worker 资源 (聚合器、解析器、后端)。"""  
        self.config \= config  
        self.logger \= logging.getLogger(f"worker.{os.getpid()}")  
          
        self.aggregator \= Aggregator(config)  
        self.parser \= Parser()  
          
        \# 加载 \*实例化的\* 后端 (例如)  
        self.backends: list \= load\_active\_backends(config)  
        self.logger.info(f"已加载后端: {\[b.\_\_class\_\_.\_\_name\_\_ for b in self.backends\]}")

    async def \_flush\_loop(self):  
        """  
        内部协程，负责周期性地  
        刷新聚合器数据。  
        """  
      ...  
              
    async def run(self):  
        """  
        Worker 的主入口点。设置  
        asyncio 服务器并启动刷新循环。  
        """  
      ...

## **5\. 详细实现逻辑**

### **run()**

1. loop \= asyncio.get\_running\_loop()  
2. self.logger.info(f"启动刷新循环，间隔 {self.config.flush\_interval} 秒")  
3. loop.create\_task(self.\_flush\_loop())  
4. self.logger.info(f"在 {self.config.host}:{self.config.port} 上创建 UDP 端点 (启用 SO\_REUSEPORT)")  
5. transport\_protocol \= StatsDProtocol(self.aggregator, self.parser, self.logger)  
6. try:  
7. transport, protocol \= await loop.create\_datagram\_endpoint(  
8.  \`lambda: transport\_protocol,\`

9.  \`local\_addr=(self.config.host, self.config.port),\`

10. \`reuse\_port=True  \# 这是关键的 SO\_REUSEPORT 指令\` 

11. )  
12. except PermissionError:  
13. self.logger.critical("权限错误: 无法绑定到端口。请使用 sudo 或在 \>1024 的端口上运行。")  
14. return  
15. except OSError as e:  
16. self.logger.critical(f"套接字绑定错误: {e}")  
17. return  
18. self.logger.info("UDP 服务器已启动，等待数据报...")  
19. await asyncio.Event().wait() (无限期等待，直到进程被终止)

### **\_flush\_loop()**

1. while True:  
2. await asyncio.sleep(self.config.flush\_interval)  
3. self.logger.debug("刷新循环开始...")  
4. try:  
5. payload \= self.aggregator.flush()  
6. if not payload:  
7.  \`self.logger.debug("没有数据需要刷新。")\`

8.  \`continue\`

9. flush\_tasks \= \[backend.flush(payload) for backend in self.backends\]  
10. results \= await asyncio.gather(\*flush\_tasks, return\_exceptions=True)  
11. for res in results:  
12. \`if isinstance(res, Exception):\`

13.     \`self.logger.error(f"后端刷新失败: {res}")\`

14. except Exception as e:  
15. self.logger.error(f"刷新循环出现严重错误: {e}", exc\_info=True)

### **5\. 04\_pystatsd\_helix/transport.md**

# **施工蓝图: transport**

# **目标文件: src/pystatsd\_helix/transport.py**

## **1\. 核心目标**

实现 asyncio.DatagramProtocol 来接收 UDP 数据包。此模块是“热路径”（hot path），必须尽可能快。

## **2\. 架构指令**

* **性能:** 此代码将每秒执行数万次。它 *必须* 是 bytes-only (纯字节操作)。  
* **无阻塞:** 在 datagram\_received 内部不允许有 sleep、await 或任何阻塞式 I/O。  
* **最小化内存分配:** 避免创建不必要的对象。

## **3\. 关键依赖**

* **Stdlib:** asyncio, logging  
* **内部模块:** .aggregator.Aggregator, .parser.Parser, .parser.ParsingError

## **4\. API 与类设计**

Python

import asyncio  
import logging  
from.aggregator import Aggregator  
from.parser import Parser, ParsingError

class StatsDProtocol(asyncio.DatagramProtocol):  
    def \_\_init\_\_(self, aggregator: Aggregator, parser: Parser, logger: logging.Logger):  
        self.aggregator \= aggregator  
        self.parser \= parser  
        self.logger \= logger  
        self.transport \= None

    def connection\_made(self, transport: asyncio.DatagramTransport):  
        self.transport \= transport

    def datagram\_received(self, data: bytes, addr: tuple\[str, int\]):  
        """  
        这是应用中“最热的路径”。  
        性能优先，操作最少。  
        """  
      ... \# 见下方逻辑  
              
    def error\_received(self, exc: Exception):  
        """当先前的发送操作引发错误时调用。"""  
        self.logger.warning(f"UDP 错误: {exc}")

## **5\. 详细实现逻辑**

### **datagram\_received()**

1. // 一个数据包可能包含多条以 \\n 分隔的指标  
2. lines \= data.split(b'\\n')  
3. for line in lines:  
4. if not line:  
5.  \`continue\` // 跳过空行 (通常是末尾的 \`\\n\`)

6. try:  
7.  // \`parse\` 是一个快速的 \`bytes.split\` 操作

8.  \`parsed\_metric \= self.parser.parse(line)\`

9.  // \`process\` 是一个快速的 \`dict\` 和 \`HdrHistogram\` 操作

10. \`self.aggregator.process(parsed\_metric)\`

11. except ParsingError:  
12. // 仅在 DEBUG 级别记录解析错误，

13. // 以避免在高负载下刷屏日志。

14. \`self.logger.debug(f"解析错误: 无法解析行: {line\!r}")\`

15. except Exception as e:  
16. // 捕获意外错误 (例如 HdrHistogram 内部错误)，防止 Worker 崩溃

17. \`self.logger.error(f"处理数据报时出错: {e}", exc\_info=True)\`

### **6\. 05\_pystatsd\_helix/parser.md**

# **施工蓝图: parser**

# **目标文件: src/pystatsd\_helix/parser.py**

## **1\. 核心目标**

一个高性能、bytes-only (纯字节) 的 StatsD 协议解析器，包括对 Datadog/InfluxDB 风格标签的支持。

## **2\. 架构指令**

* **禁止:** 使用 re (正则表达式) 模块。它太慢了。1  
* **必须:** 仅使用 bytes.split() 和 bytes.partition()。1  
* **必须:** 支持标签 (格式 |\#tag:val,tag2:val2)。1  
* **性能:** 避免 bytes.decode('utf-8')，除非是为了将值转换为 float。指标名称和标签应保持为 bytes。

## **3\. 关键依赖**

* **Stdlib:** enum, typing (NamedTuple)

## **4\. API 与类设计**

Python

from enum import Enum  
from typing import NamedTuple

class MetricType(Enum):  
    COUNTER \= b'c'  
    GAUGE \= b'g'  
    TIMER \= b'ms'  
    SET \= b's'  
    HISTOGRAM \= b'h' \# Datadog 风格的直方图 (Timer的同义词)

\# 用于快速 O(1) 查找指标类型的字典  
METRIC\_TYPE\_MAP \= {  
    b'c': MetricType.COUNTER,  
    b'g': MetricType.GAUGE,  
    b'ms': MetricType.TIMER,  
    b's': MetricType.SET,  
    b'h': MetricType.HISTOGRAM,  
}

class ParsedMetric(NamedTuple):  
    """  
    已解析指标的数据结构。所有字符串都是 bytes。  
    """  
    name: bytes  
    value\_str: bytes \# 保持 bytes 以便延迟转换  
    type: MetricType  
    sample\_rate\_str: bytes | None \= None  
    tags: tuple\[tuple\[bytes, bytes\],...\] | None \= None

class ParsingError(Exception):  
    """当行无法解析时引发。"""  
    pass

class Parser:  
    """  
    无状态解析器。单个实例在整个 Worker 中共享。  
    """  
    def \_\_init\_\_(self):  
        pass

    def parse(self, line: bytes) \-\> ParsedMetric:  
        """  
        解析 \*单行\* 指标 (不含 \\n) 为 ParsedMetric。  
        失败时引发 ParsingError。  
        """  
      ...

    def \_parse\_tags(self, tag\_data: bytes) \-\> tuple\[tuple\[bytes, bytes\],...\]:  
        """解析标签字符串的辅助函数。"""  
      ...

## **5\. 详细实现逻辑**

### **parse()**

1. tags: tuple | None \= None  
2. sample\_rate\_str: bytes | None \= None  
3. try:  
4.  // 1\. 分离标签

5.  \`line, \_, tag\_data \= line.partition(b'|\#')\`

6.  \`if tag\_data:\`

7.      \`tags \= self.\_parse\_tags(tag\_data)\`

8.  // 2\. 分离名称和值

9. \`name, \_, value\_data \= line.partition(b':')\`

10. \`if not value\_data:\`

11.     \`raise ParsingError("缺少 ':' (名称/值 分隔符)")\`

12. // 3\. 解析剩余部分

13. \`parts \= value\_data.split(b'|')\`

14. \`value\_str \= parts\`

15. // 4\. 确定类型

16. \`if len(parts) \< 2:\`

17.     \`raise ParsingError("缺少指标类型 (例如 |c)")\`

18. \`type\_key \= parts\`

19. \`metric\_type \= METRIC\_TYPE\_MAP.get(type\_key)\`

20. \`if metric\_type is None:\`

21.     \`raise ParsingError(f"未知的指标类型: {type\_key\!r}")\`

22. // 5\. 处理采样率

23. \`if len(parts) \> 2:\`

24.     \`sample\_part \= parts\`

25.     \`if sample\_part.startswith(b'@'):\`

26.         \`sample\_rate\_str \= sample\_part\[1:\]\`

27. \`return ParsedMetric(name, value\_str, metric\_type, sample\_rate\_str, tags)\`

28. except (IndexError, ValueError, KeyError) as e:  
29. \`raise ParsingError(f"内部解析错误: {e}") from e\`

### **\_parse\_tags()**

1. tag\_list \=  
2. tags\_split \= tag\_data.split(b',')  
3. for tag\_pair in tags\_split:  
4.  \`key, \_, value \= tag\_pair.partition(b':')\`

5.  \`tag\_list.append((key, value))\` // (如果只有 'tag\_only', value 可能是 b'')

6. return tuple(tag\_list)

### **7\. 06\_pystatsd\_helix/aggregator.md**

# **施工蓝图: aggregator**

# **目标文件: src/pystatsd\_helix/aggregator.py**

## **1\. 核心目标**

管理内存中的指标字典 (dict)。接收 ParsedMetric，查找或创建相应的指标对象 (Counter, Timer 等)，并更新它。

## **2\. 架构指令**

* **聚合键:** 指标必须按 (name, tags) 保持唯一。  
* **刷新规则:** Gauges (计量器) 在刷新时 *不* 被清除。所有其他类型 *都* 被清除。

## **3\. 关键依赖**

* **Stdlib:** logging  
* **内部模块:** .config.ServerConfig, .parser.ParsedMetric, .metrics\_types (所有类)

## **4\. API 与类设计**

Python

import logging  
import os  
from typing import NamedTuple  
from.config import ServerConfig  
from.parser import ParsedMetric, MetricType  
from.metrics\_types import Counter, Gauge, Set, Timer, Histogram

class MetricsPayload(NamedTuple):  
    """刷新时传递给后端的对象。"""  
    counters: dict\[tuple, Counter\]  
    gauges: dict\[tuple, Gauge\]  
    sets: dict  
    timers: dict  
    histograms: dict\[tuple, Histogram\]

class Aggregator:  
    def \_\_init\_\_(self, config: ServerConfig):  
        self.config \= config  
        self.counters: dict\[tuple, Counter\] \= {}  
        self.gauges: dict\[tuple, Gauge\] \= {}  
        self.sets: dict \= {}  
        self.timers: dict \= {}  
        self.histograms: dict\[tuple, Histogram\] \= {}  
        self.logger \= logging.getLogger(f"worker.{os.getpid()}.aggregator")

    def process(self, metric: ParsedMetric):  
        """  
        处理单个已解析的指标，  
        更新内存中相应的对象。  
        """  
      ...  
      
    def flush(self) \-\> MetricsPayload | None:  
        """  
        将所有当前指标收集到 MetricsPayload 中，  
        并清除内部存储。  
        """  
      ...

## **5\. 详细实现逻辑**

### **process()**

1. // 键是 (名称, (排序后的)标签) 的元组。  
2. key \= (metric.name, metric.tags)  
3. try:  
4.  // \`match\` 指标类型

5.  \`m\_type \= metric.type\`

6.  \`if m\_type \== MetricType.COUNTER:\`

7.      \`c \= self.counters.get(key)\`

8.      \`if c is None:\`

9.         \`c \= Counter(metric.name, metric.tags)\`

10.         \`self.counters\[key\] \= c\`

11.     \`c.add(metric.value\_str, metric.sample\_rate\_str)\`

12. \`elif m\_type \== MetricType.GAUGE:\`

13.     \`g \= self.gauges.get(key)\`

14.     \`if g is None:\`

15.         \`g \= Gauge(metric.name, metric.tags)\`

16.         \`self.gauges\[key\] \= g\`

17.     \`g.set(metric.value\_str)\`

18. \`elif m\_type \== MetricType.SET:\`

19.     \`s \= self.sets.get(key)\`

20.     \`if s is None:\`

21.         \`s \= Set(metric.name, metric.tags)\`

22.         \`self.sets\[key\] \= s\`

23.     \`s.add(metric.value\_str)\`

24. \`elif m\_type \== MetricType.TIMER:\`

25.     \`t \= self.timers.get(key)\`

26.     \`if t is None:\`

27.         \`t \= Timer(metric.name, metric.tags, self.config.timer\_histogram\_config)\`

28.         \`self.timers\[key\] \= t\`

29.     \`t.record(metric.value\_str)\`

30. \`elif m\_type \== MetricType.HISTOGRAM:\`

31.     // 作为 Timer 处理

32.     \`h \= self.histograms.get(key)\`

33.     \`if h is None:\`

34.         \`h \= Histogram(metric.name, metric.tags, self.config.timer\_histogram\_config)\`

35.         \`self.histograms\[key\] \= h\`

36.     \`h.record(metric.value\_str)\`

37. except (ValueError, TypeError) as e:  
38. \`self.logger.debug(f"无法处理指标 {metric}: {e}")\`

### **flush()**

1. // 检查是否根本没有任何数据  
2. if not (self.counters or self.gauges or self.sets or self.timers or self.histograms):  
3.  \`return None\`

4. // 创建有效负载。注意：gauges 被复制，其他的被 *移动*。  
5. payload \= MetricsPayload(  
6.  \`counters=self.counters,\`

7.  \`gauges=self.gauges.copy(), \# Gauges (计量器) 不被清除\`

8.  \`sets=self.sets,\`

9. \`timers=self.timers,\`

10. \`histograms=self.histograms\`

11. )  
12. // 清除除 Gauges 外的所有内容。重新创建 dict 比 .clear() 更快。  
13. self.counters \= {}  
14. self.sets \= {}  
15. self.timers \= {}  
16. self.histograms \= {}  
17. return payload

### **8\. 07\_pystatsd\_helix/metrics\_types.md**

# **施工蓝图: metrics\_types**

# **目标文件: src/pystatsd\_helix/metrics\_types.py**

## **1\. 核心目标**

为每种指标类型定义数据类（容器）。聚合逻辑（例如 \+=）和存储在这里实现。

## **2\. 架构指令**

* **必须:** 对 Timer 和 Histogram 使用 HdrHistogram\_py。这是一个 C 扩展，提供 $O(1)$ 的写入时间和 $O(1)$ (恒定) 的内存使用。1  
* **禁止:** 对 Timer 或 Histogram 使用 list.append()。1  
* **性能:** bytes 到 float 的转换必须 *在这里* 发生，且只发生一次。

## **3\. 关键依赖**

* **外部库:** hdrh.histogram.HdrHistogram

## **4\. API 与类设计**

Python

from hdrh.histogram import HdrHistogram

class BaseMetric:  
    """所有指标类型的基类。"""  
    def \_\_init\_\_(self, name: bytes, tags: tuple | None):  
        self.name \= name  
        self.tags \= tags

class Counter(BaseMetric):  
    """计数器：累加值。"""  
    def \_\_init\_\_(self, name: bytes, tags: tuple | None):  
        super().\_\_init\_\_(name, tags)  
        self.value: float \= 0.0

    def add(self, value\_str: bytes, sample\_rate\_str: bytes | None):  
        value \= float(value\_str)  
        if sample\_rate\_str:  
            try:  
                rate \= float(sample\_rate\_str)  
                if rate \> 0:  
                    self.value \+= (value / rate)  
            except ValueError:  
                self.value \+= value \# 采样率无效，按 1.0 处理  
        else:  
            self.value \+= value

class Gauge(BaseMetric):  
    """计量器：存储最后一个值或相对调整。"""  
    def \_\_init\_\_(self, name: bytes, tags: tuple | None):  
        super().\_\_init\_\_(name, tags)  
        self.value: float \= 0.0

    def set(self, value\_str: bytes):  
        if value\_str.startswith((b'+', b'-')):  
            \# 相对变化  
            self.value \+= float(value\_str)  
        else:  
            \# 绝对值  
            self.value \= float(value\_str)

class Set(BaseMetric):  
    """集合：计算唯一值的数量。"""  
    def \_\_init\_\_(self, name: bytes, tags: tuple | None):  
        super().\_\_init\_\_(name, tags)  
        self.data: set\[bytes\] \= set()

    def add(self, value: bytes):  
        self.data.add(value)

class Timer(BaseMetric):  
    """计时器：使用 HdrHistogram 记录值。"""  
    def \_\_init\_\_(self, name: bytes, tags: tuple | None, config: tuple\[int, int, int\]):  
        super().\_\_init\_\_(name, tags)  
        \# config \= (min\_val, max\_val, sigfigs)  
        \# 这会创建一个具有预分配内存的 C 结构  
        self.hdr \= HdrHistogram(config, config\[1\], config)

    def record(self, value\_str: bytes):  
        \# 转换为 float 并记录到 C 结构中  
        self.hdr.record\_value(float(value\_str))

class Histogram(Timer):  
    """直方图 (Histogram) 在我们的实现中是 Timer 的别名。"""  
    pass

### **9\. 08\_pystatsd\_helix/backends/abc.md**

# **施工蓝图: backends.abc**

# **目标文件: src/pystatsd\_helix/backends/abc.py**

## **1\. 核心目标**

为所有后端插件定义抽象基类 (ABC)。这将确保刷新数据时有统一的接口。

## **2\. 架构指令**

* **必须:** 使用 abc.ABC 和 @abstractmethod。1  
* **必须:** flush 方法必须是 async 的，以避免阻塞 Worker 的事件循环。

## **3\. 关键依赖**

* **Stdlib:** abc.ABC, abc.abstractmethod  
* **Pydantic:** pydantic.BaseModel  
* **内部模块:** ...aggregator.MetricsPayload (使用相对导入)

## **4\. API 与类设计**

Python

from abc import ABC, abstractmethod  
from pydantic import BaseModel  
from..aggregator import MetricsPayload

class AbstractBackend(ABC):  
      
    @classmethod  
    @abstractmethod  
    def from\_config(cls, config: BaseModel) \-\> 'AbstractBackend':  
        """  
        工厂方法。  
        从其 pydantic 配置对象创建后端实例。  
        """  
        raise NotImplementedError

    @abstractmethod  
    async def flush(self, payload: MetricsPayload) \-\> None:  
        """  
        异步将有效负载刷新到目标后端。  
        必须处理自己的异常 (例如 Timeout, ConnectionError)  
        并记录它们，而不是让异常冒泡导致 Worker 崩溃。  
        """  
        raise NotImplementedError

### **10\. 09\_pystatsd\_helix/backends/logger.md**

# **施工蓝图: backends.logger**

# **目标文件: src/pystatsd\_helix/backends/logger.py**

## **1\. 核心目标**

实现 MVP 的必备后端：LoggerBackend。它必须将聚合的指标输出到 stdout (通过 logging) 以进行调试。

## **2\. 架构指令**

* **必须:** 必须作为 MVP 的 P0 (Must-Have) 后端提供。1  
* **必须:** 必须实现 AbstractBackend。

## **3\. 关键依赖**

* **Stdlib:** logging, asyncio, os  
* **内部模块:** .abc.AbstractBackend, ..aggregator.MetricsPayload, ..config.LoggerConfig

## **4\. API 与类设计**

Python

import logging  
import asyncio  
import os  
from.abc import AbstractBackend  
from..aggregator import MetricsPayload  
from..config import LoggerConfig

class LoggerBackend(AbstractBackend):  
    def \_\_init\_\_(self, config: LoggerConfig):  
        self.config \= config  
        self.logger \= logging.getLogger(f"worker.{os.getpid()}.backend.logger")  
        self.logger.setLevel(config.level.upper())

    @classmethod  
    def from\_config(cls, config: LoggerConfig) \-\> 'LoggerBackend':  
        return cls(config)

    async def flush(self, payload: MetricsPayload):  
      ...

## **5\. 详细实现逻辑**

### **flush()**

1. self.logger.info("--- 后端刷新 (Logger) \---")  
2. **Counters:**  
   * for (name, tags), c in payload.counters.items():  
   * self.logger.info(f"COUNTER: {name\!r} tags={tags} value={c.value}")  
3. **Gauges:**  
   * for (name, tags), g in payload.gauges.items():  
   * self.logger.info(f"GAUGE: {name\!r} tags={tags} value={g.value}")  
4. **Sets:**  
   * for (name, tags), s in payload.sets.items():  
   * self.logger.info(f"SET: {name\!r} tags={tags} unique={len(s.data)}")  
5. **Timers/Histograms:**  
   * all\_timers \= {\*\*payload.timers, \*\*payload.histograms}  
   * for (name, tags), t in all\_timers.items():  
   * hdr \= t.hdr  
   * if hdr.get\_count() \== 0: continue  
   * self.logger.info(f"TIMER: {name\!r} tags={tags} | " f"count={hdr.get\_count()} " f"mean={hdr.get\_mean():.2f} " f"p95={hdr.get\_value\_at\_percentile(95.0):.2f} " f"max={hdr.get\_max\_value()}")  
6. // asyncio.sleep(0) 是必要的，以将控制权交还给事件循环，  
7. // 以防日志记录 (尤其是 pretty\_print) 花费太多时间。  
8. await asyncio.sleep(0)

### **11\. 10\_pystatsd\_helix/backends/graphite.md**

# **施工蓝图: backends.graphite**

# **目标文件: src/pystatsd\_helix/backends/graphite.py**

## **1\. 核心目标**

实现 MVP 的必备后端：GraphiteBackend。必须将指标格式化为 Graphite 纯文本协议并通过 TCP 发送。

## **2\. 架构指令**

* **必须:** 必须作为 MVP 的 P0 (Must-Have) 后端提供。1  
* **必须:** 使用 asyncio 进行非阻塞 TCP I/O。  
* **必须:** HdrHistogram (Timers) *必须* 被展开为多个 Graphite 指标 (例如 .p95, .mean, .count)。

## **3\. 关键依赖**

* **Stdlib:** asyncio, logging, time, os  
* **内部模块:** .abc.AbstractBackend, ..aggregator.MetricsPayload, ..config.GraphiteConfig

## **4\. API 与类设计**

Python

import asyncio  
import logging  
import time  
import os  
from.abc import AbstractBackend  
from..aggregator import MetricsPayload  
from..config import GraphiteConfig

class GraphiteBackend(AbstractBackend):  
    def \_\_init\_\_(self, config: GraphiteConfig):  
        self.config \= config  
        self.logger \= logging.getLogger(f"worker.{os.getpid()}.backend.graphite")

    @classmethod  
    def from\_config(cls, config: GraphiteConfig) \-\> 'GraphiteBackend':  
        return cls(config)

    async def flush(self, payload: MetricsPayload):  
      ...

    def \_format\_metric(self, name: bytes, tags: tuple | None) \-\> str:  
        """根据 config.tag\_format 格式化名称和标签。"""  
      ...

## **5\. 详细实现逻辑**

### **\_format\_metric()**

1. name\_str \= name.decode('utf-8').replace(' ', '\_')  
2. if not tags:  
3.  \`return name\_str\`

4. if self.config.tag\_format \== 'graphite':  
5.  // 格式: \`name;tag1=val1;tag2=val2\`

6.  \`tag\_strs \= \[f"{t.decode('utf-8')}={t.decode('utf-8')}" for t in tags if t\]\`

7.  \`tag\_strs\_no\_val \= \[f"{t.decode('utf-8')}" for t in tags if not t\]\`

8.  \`all\_tags \= sorted(tag\_strs \+ tag\_strs\_no\_val)\`

9.  \`return f"{name\_str};{';'.join(all\_tags)}"\`

10. elif self.config.tag\_format \== 'datadog':  
11. // 格式: \`name\[tag1:val1,tag2:val2\]\` (Graphite 兼容性较差)

12. \`tag\_strs \= \[f"{t.decode('utf-8')}:{t.decode('utf-8')}" for t in tags if t\]\`

13. \`tag\_strs\_no\_val \= \[f"{t.decode('utf-8')}" for t in tags if not t\]\`

14. \`all\_tags \= sorted(tag\_strs \+ tag\_strs\_no\_val)\`

15. \`return f"{name\_str}\[{','.join(all\_tags)}\]"\`

### **flush()**

1. lines: list\[bytes\] \=  
2. timestamp \= int(time.time())  
3. prefix \= self.config.prefix  
4. // 1\. Counters  
5. for (name, tags), c in payload.counters.items():  
6.  \`metric\_name \= self.\_format\_metric(name, tags)\`

7.  \`lines.append(f"{prefix}.{metric\_name}.count {c.value} {timestamp}\\n".encode('ascii'))\`

8. // 2\. Gauges  
9. for (name, tags), g in payload.gauges.items():  
10. \`metric\_name \= self.\_format\_metric(name, tags)\`

11. \`lines.append(f"{prefix}.{metric\_name} {g.value} {timestamp}\\n".encode('ascii'))\`

12. // 3\. Sets  
13. for (name, tags), s in payload.sets.items():  
14. \`metric\_name \= self.\_format\_metric(name, tags)\`

15. \`lines.append(f"{prefix}.{metric\_name}.count {len(s.data)} {timestamp}\\n".encode('ascii'))\`

16. // 4\. Timers/Histograms (最关键的)  
17. all\_timers \= {\*\*payload.timers, \*\*payload.histograms}  
18. for (name, tags), t in all\_timers.items():  
19. \`hdr \= t.hdr\`

20. \`count \= hdr.get\_count()\`

21. \`if count \== 0: continue\`

22. \`metric\_name \= self.\_format\_metric(name, tags)\`

23. \`m\_prefix \= f"{prefix}.{metric\_name}"\`

24. \`lines.append(f"{m\_prefix}.count {count} {timestamp}\\n".encode('ascii'))\`

25. \`lines.append(f"{m\_prefix}.mean {hdr.get\_mean():.6f} {timestamp}\\n".encode('ascii'))\`

26. \`lines.append(f"{m\_prefix}.max {hdr.get\_max\_value()} {timestamp}\\n".encode('ascii'))\`

27. \`lines.append(f"{m\_prefix}.min {hdr.get\_min\_value()} {timestamp}\\n".encode('ascii'))\`

28. \`lines.append(f"{m\_prefix}.p50 {hdr.get\_value\_at\_percentile(50.0)} {timestamp}\\n".encode('ascii'))\`

29. \`lines.append(f"{m\_prefix}.p90 {hdr.get\_value\_at\_percentile(90.0)} {timestamp}\\n".encode('ascii'))\`

30. \`lines.append(f"{m\_prefix}.p95 {hdr.get\_value\_at\_percentile(95.0)} {timestamp}\\n".encode('ascii'))\`

31. \`lines.append(f"{m\_prefix}.p99 {hdr.get\_value\_at\_percentile(99.0)} {timestamp}\\n".encode('ascii'))\`

32. if not lines: return  
33. // 5\. TCP 发送  
34. try:  
35. \`writer: asyncio.StreamWriter | None \= None\`

36. \`open\_task \= asyncio.open\_connection(self.config.host, self.config.port)\`

37. \`reader, writer \= await asyncio.wait\_for(open\_task, timeout=self.config.timeout)\`

38. \`writer.writelines(lines)\`

39. \`await writer.drain()\`

40. \`self.logger.debug(f"成功刷新 {len(lines)} 条指标到 Graphite。")\`

41. except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:  
42. \`self.logger.error(f"刷新到 Graphite 失败: {e}")\`

43. finally:  
44. \`if writer: writer.close(); await writer.wait\_closed()\`

### **12\. 11\_pystatsd\_helix/backends/loader.md**

# **施工蓝图: backends.loader**

# **目标文件: src/pystatsd\_helix/backends/loader.py**

## **1\. 核心目标**

使用 entry\_points 和 ServerConfig，动态加载并初始化 *仅* 处于活动状态的后端。

## **2\. 架构指令**

* **必须:** 使用 importlib.metadata.entry\_points 来发现插件。1  
* **必须:** 使用 ServerConfig.active\_backends 来决定 *加载哪些* 插件。

## **3\. 关键依赖**

* **Stdlib:** importlib.metadata, logging, os  
* **内部模块:** ..config.ServerConfig, .abc.AbstractBackend

## **4\. API 与类设计**

Python

import logging  
import os  
from importlib.metadata import entry\_points  
from..config import ServerConfig  
from.abc import AbstractBackend

def load\_active\_backends(config: ServerConfig) \-\> list:  
    """  
    加载并初始化仅在 config.active\_backends 中列出的后端。  
    """  
  ...

## **5\. 详细实现逻辑**

1. logger \= logging.getLogger(f"worker.{os.getpid()}.loader")  
2. logger.info("正在加载可用的后端插件...")  
3. available\_plugins: dict\] \= {}  
4. try:  
5.  \`eps \= entry\_points(group='pystatsd\_helix.backends')\`

6. except Exception as e:  
7.  \`logger.error(f"无法加载 entry\_points: {e}")\`

8.  \`eps \=\`

9. for ep in eps:  
10. \`try:\`

11.     \`backend\_class \= ep.load()\`

12.     \`available\_plugins\[ep.name\] \= backend\_class\`

13.     \`logger.debug(f"发现后端: '{ep.name}' \-\> {backend\_class}")\`

14. \`except Exception as e:\`

15.     \`logger.warning(f"无法加载插件 '{ep.name}': {e}")\`

16. initialized\_backends: list \=  
17. logger.info(f"正在激活后端: {config.active\_backends}")  
18. for backend\_name in config.active\_backends:  
19. \`if backend\_name not in available\_plugins:\`

20.     \`logger.error(f"错误: 后端 '{backend\_name}' 在 active\_backends 中指定，但未找到/加载。")\`

21.     \`continue\`

22. \`BackendClass \= available\_plugins\[backend\_name\]\`

23. // 获取此后端的 \*特定\* 配置

24. \`backend\_config\_data \= getattr(config.backend\_configs, backend\_name, None)\`

25. \`if backend\_config\_data is None:\`

26.     \`logger.error(f"错误: 后端 '{backend\_name}' 已激活，但在 backend\_configs 中缺少其配置。")\`

27.     \`continue\`

28. \`try:\`

29.     // 使用工厂方法

30.     \`instance \= BackendClass.from\_config(backend\_config\_data)\`

31.     \`initialized\_backends.append(instance)\`

32.     \`logger.info(f"后端 '{backend\_name}' 已成功初始化。")\`

33. \`except Exception as e:\`

34.     \`logger.error(f"初始化后端 '{backend\_name}' 失败: {e}", exc\_info=True)\`

35. return initialized\_backends

### **13\. 12\_pyproject.md**

# **施工蓝图: pyproject**

# **目标文件: pyproject.toml**

## **1\. 核心目标**

定义项目依赖、元数据，以及 *至关重要* 的：用于控制台脚本和后端插件系统的入口点 (entry\_points)。

## **2\. 架构指令**

* **必须:** 定义 project.entry-points."pystatsd\_helix.backends"。1  
* **必须:** 在 dependencies 中包含 uvloop 和 hdrhistogram\_py。

## **3\. 详细实现逻辑**

Ini, TOML

\[build-system\]  
requires \= \["setuptools\>=61.0"\]  
build-backend \= "setuptools.build\_meta"

\[project\]  
name \= "pystatsd\_helix"  
version \= "0.1.0"  
description \= "High-Performance Python StatsD Server (Project Helix)"  
requires-python \= "\>=3.10"  
license \= { text \= "MIT" }

\# 可行性报告中确定的关键依赖  
dependencies \= \[  
    "uvloop",           \#  用于 I/O 加速  
    "hdrhistogram\_py",  \#  用于 O(1) Timer 聚合  
    "pydantic",         \# 用于配置  
    "pyyaml",           \# 用于加载 YAML 配置 (可选)  
\]

\[project.optional-dependencies\]  
dev \= \[  
    "pytest",  
    "pytest-asyncio",  
\]

\# 用于运行服务器的控制台脚本入口点  
\[project.scripts\]  
pystatsd-helix \= "pystatsd\_helix.main:main"

\# 关键的插件架构   
\#   
\# 这声明了一个组，\*外部\* 包 (例如 pystatsd-helix-influxdb)  
\# 可以挂钩到这个组。  
\# 我们也使用相同的机制注册我们自己的 MVP 后端。  
\[project.entry-points."pystatsd\_helix.backends"\]  
logger \= "pystatsd\_helix.backends.logger:LoggerBackend"  
graphite \= "pystatsd\_helix.backends.graphite:GraphiteBackend"

#### **Works cited**

1. Python StatsD 重构可行性分析