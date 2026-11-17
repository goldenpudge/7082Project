# 7082Project

ELEC7082 小组项目：Python LLM Equity Research Agent。

研究范围：Apple（AAPL）、NVIDIA（NVDA）、Palantir（PLTR）。

## 目录结构

```
7082Project/
├── agent/                      Agent 主体：图、提示词、运行参数
│   ├── config.py               公司名单、模型与运行上限、报告章节
│   ├── prompts.py              四个 prompt 与 planner 的 PLAN_SCHEMA
│   └── graph.py                LangGraph 节点、工具循环、模型视图投影、报告渲染
├── tools/                      工具层：取数、校验、证据落盘
│   ├── client.py               ToolClient：schema 校验 → 分发 → 保存证据 → 返回 envelope
│   ├── market_data.py          companies.json 读取、行情汇总计算，自带命令行
│   └── demo_provider.py        离线虚构数据（DemoTicker），不发网络请求
├── tests/                      离线测试，unittest 写法，pytest 也可跑
├── docs/                       给组员的接入说明与验证记录
├── notebooks/                  工具层入门示例 notebook
├── data/                       工具层证据输出（运行时生成，不进仓库）
├── reports/                    Agent 生成的 Markdown 报告（运行时生成，不进仓库）
├── paths.py                    全项目唯一的 ROOT
├── run_agent.py                Agent 主流程入口
├── run_tools.py                只跑工具、不调模型的入口
├── companies.json              三家公司与 Yahoo 代码的映射
├── tool_specs.json             工具的对外契约（name / description / JSON Schema）
├── requirements.txt            依赖清单
└── requirements-verified.txt   已验证可用的版本组合
```

各文件夹的职责：

| 位置 | 内容 | 不要放什么 |
| --- | --- | --- |
| `agent/` | 只会被 `run_agent.py` 使用的工作流代码：状态定义、节点函数、prompt 文本、模型参数 | 数据抓取逻辑、供应商细节 |
| `tools/` | 与 LLM 无关、可独立调用的工具：参数校验、供应商请求、证据文件写入 | prompt、LangGraph 代码 |
| `tests/` | 所有测试文件，命名 `test_*.py` | 运行产物、样例数据 |
| `docs/` | 给组员看的说明与验证记录 | 可执行代码 |
| `notebooks/` | 教学、探索用 notebook | 被其他模块 import 的代码 |
| `data/` | `ToolClient` 写出的原始证据与 `result.json` | 手写配置 |
| `reports/` | `run_agent.py` 生成的报告 | 手动编辑的内容（会被新报告覆盖） |

依赖方向是单向的：`agent` 依赖 `tools`，`tools` 不依赖 `agent`。`ROOT` 只在 [paths.py](paths.py) 里定义一次，其余模块都从它导入，因此文件在包里怎么移动都不影响 `companies.json`、`data/`、`reports/` 的位置。

## 准备环境

Python 3.11+。macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 运行 Agent 

入口是 [run_agent.py](run_agent.py)，参数如下：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--company SYMBOL [SYMBOL ...]` | 全部 `ASSIGNED` | 要分析的公司，只能是 `AAPL` / `NVDA` / `PLTR` |
| `--request "..."` | `DEFAULT_REQUEST` | 研究诉求，交给 planner |
| `--live` | 关闭（= demo） | 数据源切到真实 Yahoo；不加时用虚构样例 |
| `--dry-run` | 关闭 | 跑完所有工具步骤，但不调用模型 |
| `--output DIR` | `reports/` | 报告输出目录 |

四种常用组合：

```sh
# 1. 虚构数据 + 不调模型：验证代码路径，零网络、零额度
python run_agent.py --company AAPL --dry-run

# 2. 虚构数据 + 调模型：验证 prompt 与工具调用，不访问 Yahoo
python run_agent.py --company AAPL

# 3. 真实数据 + 不调模型：只检查数据源与证据
python run_agent.py --company AAPL --live --dry-run

# 4. 完整流程：真实数据 + 模型
python run_agent.py --company AAPL --live

# 三家一起跑，并自定义诉求与输出目录
python run_agent.py --live --output ./reports/run1 \
  --request "Focus on margin trend and buyback capacity."
```

成功时终端输出：

```
AAPL: C:\...\reports\AAPL_live_20261117T153536Z.md
  data=live sources=7 warnings=2 periods=4 frequency=annual
  tool calls: get_company_profile, get_financial_statements, get_price_history
```

报告保存在 `reports/{SYMBOL}_{demo|live}_{UTC时间戳}.md`，内含来源附录（provider、URL、`retrieved_at`、原始文件路径、sha256）。`--dry-run` 不需要 API Key；其余情况需要下一节的三个环境变量。

## 修改模型 API 配置

模型配置集中在 [agent/config.py](agent/config.py#L18-L23)，只读环境变量：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | 无（必填） | 供应商的 API Key；缺失时 `--live`/调模型的运行会直接退出 |
| `LLM_MODEL` | `deepseek-chat` | 模型名，如 `gpt-4o-mini`、`moonshot-v1-8k` |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容的接口地址，通常以 `/v1` 结尾 |

Windows PowerShell（只对当前窗口生效）：

```powershell
$env:LLM_API_KEY  = "你的 key"
$env:LLM_MODEL    = "deepseek-flash"
$env:LLM_BASE_URL = "https://api.deepseek.com/v1"
```

macOS / Linux：

```sh
export LLM_API_KEY="你的 key"
export LLM_MODEL="deepseek-chat"
export LLM_BASE_URL="https://api.deepseek.com/v1"
```

需要长期生效（Windows，设为用户级变量，之后要重开终端）：

```powershell
setx LLM_API_KEY "你的 key"
setx LLM_MODEL "deepseek-chat"
setx LLM_BASE_URL "https://api.deepseek.com/v1"
```

改供应商时只要这三项配套改：`ChatOpenAI` 通过 `base_url` 指向对方，所以任何「OpenAI 兼容 + 支持 function calling」的接口都能用。换模型后如果报参数超限，调 [agent/config.py](agent/config.py) 里的 `MAX_TOKENS`（默认 30000）；planner 强制使用 tool call 而不是 `response_format=json_schema`，兼容性更好。除这三个变量外，温度、超时、轮次上限等都在 `agent/config.py` 里改。

## 新增 tools

工具名、说明和 JSON Schema 必须修改，改完这几处即可被模型看到并调用。

1. **`tool_specs.json`（必改）**：新增一条 `name` / `description` / `parameters`。`parameters` 是 JSON Schema（Draft 2020-12），建议 `"additionalProperties": false`。`agent/graph.py` 的 `_tool_schemas()` 会自动把它转成 OpenAI function schema，无需另改。
2. **`tools/client.py`（必改）**：
   - 写一个 handler，签名 `def _new_tool(self, ticker, args, context)`，返回四元组 `(data, sources, warnings, partial)`。
   - 在 `call_tool` 内的 `handlers = {...}` 字典里注册 `"新工具名": self._new_tool`。
   - 证据用 `self._source(context, label, url, raw, table=...)` 落盘，它负责 `source_id`、sha256 与 URL。
   - 参数不合法 / 供应商无数据时抛 `ValueError` / `DataUnavailable`，`call_tool` 会统一转成 `error` envelope。
3. **`agent/graph.py` `_projection()`（可选）**：默认未知工具直接透传 `data`，能跑；若 `data` 太大或含内部字段，加一个 `_slim_*` 分支只把模型需要的内容给它。
4. **`agent/config.py` `REQUIRED_TOOLS`（可选）**：新工具如果每次都必须跑到，加进这个列表，图会保底补调。
5. **`tools/demo_provider.py` + `tests/test_tools.py`（可选但建议）**：给 demo 模式补数据，并加一条离线测试。

新增的 `symbol` 必须存在于 `companies.json`。改完用下面几条命令回归：

```sh
python -m pytest -q
python -m unittest discover -s tests -t .
python run_tools.py --symbol AAPL
python run_agent.py --company AAPL --dry-run
```

## 小组接入

先读 [Tools 接入说明](docs/TOOLS_HANDOFF.md)。工具输入规格见 [tool_specs.json](tool_specs.json)，学习示例见 [02_tools_quickstart.ipynb](notebooks/02_tools_quickstart.ipynb)。

Workflow 统一调用 `ToolClient.call_tool(tool_name, arguments_dict)`，并保留 `ok` / `partial` / `error`、来源和警告。没有数据时不能由模型补造数字。

工具层自身也带命令行（因为代码移进了包，要用模块方式跑）：

```sh
python -m tools.market_data --list
python -m tools.market_data --demo --symbol AAPL
```

## 验证与样例

- 18 项离线检查通过。
- 三家公司共 12 次真实调用：11 次完整返回、1 次部分返回。
- Apple 季度 diluted EPS 的部分缺失值保留为 null。

详情见 [验证记录](docs/TOOLS_VALIDATION.md)。运行工具后会在本机生成 `data/` 与 `reports/`，这两个目录不进仓库。

```sh
python -m unittest discover -s tests -t .
python -m pytest
```

## 待完成

SEC 10-K/10-Q 原文、电话会全文及检索、LLM 工作流和最终报告尚待接入。Yahoo 财务表尚未对照 SEC/IR 原文核验，当前交付不是完整课程项目。

虚拟环境、缓存、API Key 和本地配置不进入仓库。
