# 7082Project

ELEC7082 小组项目：Python LLM Equity Research Agent。

研究范围：Apple（AAPL）、NVIDIA（NVDA）、Palantir（PLTR）。

## 当前交付：Tools v0.2

- `get_price_history`：Yahoo 日线、成交量、复权变化与原始数据。
- `get_company_profile`：业务、行业、币种与估值快照。
- `get_financial_statements`：年度/季度三张财务表和原始行列引用。

工具层不需要 LLM API Key。Workflow 编排和提示词模块由后续开发接入。

## 快速开始

Python 3.11+。macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_tools.py
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_tools.py
```

默认是明确标注的虚构样例，不访问网络，也不调用模型。访问 Yahoo 的真实模式：

```sh
.venv/bin/python run_tools.py --live --all --frequency annual --periods 4
```

## 小组接入

先读 [Tools 接入说明](TOOLS_HANDOFF.md)。工具输入规格见 [tool_specs.json](tool_specs.json)，学习示例见 [02_tools_quickstart.ipynb](02_tools_quickstart.ipynb)。

Workflow 统一调用 `ToolClient.call_tool(tool_name, arguments_dict)`，并保留 `ok` / `partial` / `error`、来源和警告。没有数据时不能由模型补造数字。

## 验证与样例

- 18 项离线检查通过。
- 三家公司共 12 次真实调用：11 次完整返回、1 次部分返回。
- Apple 季度 diluted EPS 的部分缺失值保留为 null。

详情见 [验证记录](TOOLS_VALIDATION.md)。仓库采用 15 个文件的精简版；原始真实样例保留在本地完整交付包。运行工具后会在本机生成数据与来源记录。

```sh
.venv/bin/python -m unittest -v test_market_data.py test_equity_tools.py
```

## 待完成

SEC 10-K/10-Q 原文、电话会全文及检索、LLM 工作流和最终报告尚待接入。Yahoo 财务表尚未对照 SEC/IR 原文核验，当前交付不是完整课程项目。

虚拟环境、缓存、API Key 和本地配置不进入仓库。
