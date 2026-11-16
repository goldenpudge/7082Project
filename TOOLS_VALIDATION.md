# Tools v0.2 验证记录

日期：2026-11-16，香港。

## 本地检查

- 18 项检查通过：6 项原行情工具检查 + 12 项新增工具接口/数据/错误检查。
- 新 Notebook 的全部代码格已离线执行；默认虚构数据，未发出模型请求。
- 已独立解压交付包，验证环境外的模块导入、18 项检查、3 个默认演示工具及所有文件校验和；依赖检查通过。
- 对 12 次真实调用的成功返回核对来源文件 SHA-256；对财务表已逐项回读 CSV，检查输出值与原始行列一致。
- 此验证检查程序处理与供应商返回是否一致，尚未独立对照 SEC/IR 原始财报。

## 真实 Yahoo 验证

区间：行情请求 [2025-11-16, 2026-11-16)。概况和报表为抓取时的供应商当前版本。

| 公司 | 工具 | 频率 | 状态 |
| --- | --- | --- | --- |
| AAPL | get_price_history | — | ok |
| AAPL | get_company_profile | — | ok |
| NVDA | get_price_history | — | ok |
| NVDA | get_company_profile | — | ok |
| PLTR | get_price_history | — | ok |
| PLTR | get_company_profile | — | ok |
| AAPL | get_financial_statements | annual | ok |
| AAPL | get_financial_statements | quarterly | partial |
| NVDA | get_financial_statements | annual | ok |
| NVDA | get_financial_statements | quarterly | ok |
| PLTR | get_financial_statements | annual | ok |
| PLTR | get_financial_statements | quarterly | ok |

结果：11 次 ok、1 次 partial、0 次 error。Apple 季度利润表的部分期间缺少 diluted_eps，保留 null 并明确警告；其余已取得数据继续返回。没有补算或虚构缺失 EPS。

所有年度和季度报表调用均返回三张表各 4 个期间；完整原始样例及 verification_manifest.json 保存在本地交付包。精简仓库不提交这些自动生成文件；运行工具后可在本机保存新的 result.json 和原始表。

## 当前限制

- 只对 AAPL、NVDA、PLTR 做了真实数据验证，其他课程公司尚未验收。
- Yahoo 财务表、估值快照不是历史 as-of 数据；披露日期、财政季度名称和期间开始日尚未获取。
- SEC 10-K/10-Q 原文与 Seeking Alpha/IR 电话会全文尚未接入。
- 未调用任何 LLM API；远程发布不属于本数据验证步骤。

复验命令：`.venv/bin/python -m unittest -v test_market_data.py test_equity_tools.py`。

## 精简仓库说明

本仓库保留 4 个核心 Python 文件、2 个测试文件、1 个教学 Notebook、2 个 JSON 规格/映射文件、2 份依赖清单、3 份说明和 .gitignore，共 15 个文件。完整交付包及其真实样例仍保留在本地。
