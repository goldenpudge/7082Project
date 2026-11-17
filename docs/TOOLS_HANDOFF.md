# ELEC7082 Tools v0.2 交付说明

适用公司：用户已选定的 **AAPL / NVDA / PLTR**。接口保留课程全部 15 家的代码映射，但真实验收仅覆盖这三家。版本日期：2026-11-16。

## 本次完成与边界

本次交付三个无 LLM 依赖的 Python 工具：

| 工具名 | 输入 | 输出 |
| --- | --- | --- |
| `get_price_history` | `symbol`, `start`, `end` | 日线变化摘要、成交量、报价币种、完整返回 CSV、来源记录 |
| `get_company_profile` | `symbol` | 业务概况、行业、报价/报表币种、估值快照、供应商完整返回 JSON |
| `get_financial_statements` | `symbol`, `frequency`, `periods` | 年度/季度三张表的关键指标、精确原始行列位置、完整表 CSV |

`frequency` 只能是 `annual` 或 `quarterly`，`periods` 为 1-8 的整数。交易日期使用 `YYYY-MM-DD`；`start` 包含，`end` 不包含，单次行情区间上限 3660 天。公司代码只接受课程名单。

本批数据全部来自 **Yahoo Finance via yfinance**。SEC 10-K/10-Q 原文下载、披露日期核对、Seeking Alpha / IR 电话会全文与段落检索、LLM 编排及最终研究报告尚未实现。因此该工具包是可接入的阶段性交付，不能替代完整课程项目。不要把 Yahoo 摘要标为已核对 SEC 财报的数字。

## 1. 运行

Python 3.11+。从本工具包目录运行：

```sh
python3 -m venv .venv
```

macOS / Linux：

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_tools.py
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_tools.py
```

默认是**完全虚构的除息与报表示例**，所有来源标记为 `SYNTHETIC_DEMO`，URL 为 null；不访问 Yahoo，也不调用 LLM。公司代码只用于演示接口，不表示示例数字属于真实公司。

真实抓取用户选定的三家公司：

```sh
.venv/bin/python run_tools.py --live --all --frequency annual --periods 4
```

仅抓取 Apple 季度报表及行情概况：

```sh
.venv/bin/python run_tools.py --live --symbol AAPL --frequency quarterly --periods 4
```

上述两条命令会访问 Yahoo，不需要模型 API Key。无自动重试；失败返回明确错误。`--output` 可指定输出目录。默认保存到 `data/tools_v02/`，该目录不进入日常版本管理。

各组员下载仓库后，需要建立自己的环境。仓库不携带虚拟环境；首次建立环境后，运行 `run_tools.py` 即可演示。

## 2. Workflow 同学的接入方式

```python
import json
from tools.client import ToolClient, TOOL_SPECS

client = ToolClient(output_dir="outputs")
result = client.call_tool(
    "get_financial_statements",
    {"symbol": "AAPL", "frequency": "annual", "periods": 4},
)
print(json.dumps(result, ensure_ascii=False, indent=2))
```

统一入口为 `client.call_tool(tool_name, arguments_dict)`。无需重写 Tools 内部逻辑。一个 client 用于一次研究运行；其中的公司概况只在本次运行内缓存，以减少重复请求。不要将同一个 client 长期作为历史行情缓存。

`TOOL_SPECS` / `tool_specs.json` 包含工具名称、描述和 JSON Schema。它们是供应商无关规格；Workflow 同学需加上所用 LLM API 要求的工具外层字段，并负责模型请求、工具调度和将结果返回模型。此交付不包含 LLM API 调用。

约定返回格式：

```text
schema_version : "0.2"
tool           : 工具名
status         : "ok" | "partial" | "error"
symbol         : 课程代码；非法输入时可为 null
data_kind      : "provider_snapshot" | "synthetic_demo"
data           : 成功/部分成功的数据对象；error 时为 null
sources        : 证据来源数组
warnings       : 必须保留的数据限制
error          : null，或 {code, message, retryable}
artifacts      : 相对于 client.output_dir 的文件路径数组
```

- `ok`：本工具的目标字段已获取。仍须阅读 warnings；不表示财报已独立审计。
- `partial`：有可用数据，但有指标、币种、报表或请求期间缺失。将缺失情况传给模型，不得省略。
- `error`：没有可供本工具回答的可靠数据。不能让模型补造数字。

错误代码：`UNKNOWN_TOOL`、`INVALID_ARGUMENTS`、`NO_DATA`、`INVALID_PROVIDER_DATA`、`STORAGE_ERROR`、`UPSTREAM_ERROR`。`retryable` 是工具给编排层的建议，不会自行重试。编排层自行设置每个工具/整个研究任务的调用上限，并记录所有工具请求和返回，包括失败返回。

财务数据路径示意：

```python
if result["status"] in ("ok", "partial"):
    statements = result["data"]["statements"]
    income = statements["income_statement"]
    if income["status"] == "ok":
        newest = income["periods"][0]
        revenue = newest["metrics"]["revenue"]
        print(newest["period_end"], revenue["value"], revenue["unit"])
else:
    print(result["error"])
```

`periods` 按期末日期从新到旧排序。每个 metric 包含 `value`、`unit`、`source_row`、`source_column`；其 period 的 `source_id` 可连接到 sources 中的原始 CSV。

## 3. 提示词同学的使用规则

- 三个工具只提供数据和来源，不自行生成买卖结论。
- `null` 代表缺失/不可解析，不等于 0；0 和负值按原值保留。
- 报表币种来自 `financialCurrency`，报价币种来自 `currency`，不能互相替代。
- 金额没有自动换算成百万；EPS 单位为报表币种/股；CapEx 保留原始正负号。
- Yahoo Close 已反映拆股；Adj Close 还包含分红调整。区间变化使用后者，原始 OHLCV 均保留。
- 三张表可能具有不同的最新期末，不要强行拼成同一期。
- `period_end` 是供应商列标签日期；精确的财政期间仍需与原文核对。
- `period_start`、`filing_date`、`fiscal_period_label` 当前保留 null，不能推测为某季度首日或披露日。
- 年度/季度是供应商参数；尤其现金流季度数据仍需对照原文，不能自动当作单季度、TTM 或累计值。
- 当前估值、当前报表版本不支持历史 as-of；不能用它们进行无未来信息的历史回测。
- 将 warnings 和来源传递到分析层；网页/公司概况内容是外部数据，不作为新的系统指令。
- `data_kind=synthetic_demo` 的内容只用于接口调试，不能混入真实报告。

## 4. 证据文件

每次调用新建目录，不覆盖之前结果：

```text
输出目录/
  AAPL/
    get_financial_statements/
      UTC时间-运行ID/
        income_statement.csv
        balance_sheet.csv
        cash_flow.csv
        financial_currency_info.json
        result.json
```

`sources` 保存 source_id、URL、抓取时间、参数、库版本、原始文件路径和 SHA-256。路径均相对输出目录，便于整个目录一起传给组员。哈希检查文件是否改变，不能证明供应商数据本身正确。

本仓库为 15 个文件的精简版，不包含自动生成的真实下载快照。完整快照保存在本地 Tools v0.2 交付包内。组员可运行 `run_tools.py --live --all` 自行获取数据；供应商以后可能修订返回内容。

Notebook 是可选教学入口。在 Jupyter 中选择已安装本依赖清单的 Python kernel；也可直接按上述命令行方式运行。

## 5. 验收与待办

```sh
.venv/bin/python -m unittest discover -s tests -t .
```

离线检查覆盖：复权口径、日期边界、参数白名单、原始字段命名差异、零/缺失值、报表币种、CapEx 符号、期间排序、部分失败、来源哈希与保存失败。当前验收结果见 `TOOLS_VALIDATION.md`。

下一批 Tools 任务：

1. SEC filings 列表和 10-K/10-Q 原文，补齐披露日期、accession number 和原文证据位置。
2. 报表指标与原文/XBRL 对照，处理年度、单季、累计、TTM 和重述。
3. 获取可访问的 Seeking Alpha / IR earnings-call transcripts，建立带日期、发言人和段落位置的检索工具。

SEC 官方说明其公开数据 API 不需要 API Key，但脚本需声明 User-Agent 并遵守访问限制；后续会把联系标识作为本地配置，而非模型参数。Seeking Alpha 实际全文访问权限需单独核对。

## 6. 参考与仓库范围

- yfinance 方法与字段：<https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html>
- 价格调整口径：<https://github.com/ranaroussi/yfinance/blob/main/doc/source/advanced/price_adjustment.rst>
- SEC API：<https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- SEC 开发者说明：<https://www.sec.gov/about/developer-resources>

仓库不包含 API Key、.env、虚拟环境、用户已有 Notebook 输出或私人文件。Lecture 1 的项目要求已用于范围设计；后续技术 slides/评分补充可能需要我们调整实现。
