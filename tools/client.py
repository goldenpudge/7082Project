"""Tools v0.2: provider-neutral callable tools, without any LLM request.

Workflow entry point:
    client = ToolClient()
    result = client.call_tool('get_company_profile', {'symbol': 'AAPL'})
"""

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid

from jsonschema import Draft202012Validator
import pandas as pd

from paths import ROOT
from tools.market_data import resolve_company, summarize_prices

SCHEMA_VERSION = "0.2"
TOOL_SPECS = json.loads((ROOT / "tool_specs.json").read_text(encoding="utf-8"))
SPECS = {spec["name"]: spec for spec in TOOL_SPECS}

# (stable metric name, provider row candidates, unit type)
STATEMENTS = {
    "income_statement": ("get_income_stmt", [
        ("revenue", ["Total Revenue"], "money"),
        ("gross_profit", ["Gross Profit"], "money"),
        ("operating_income", ["Operating Income"], "money"),
        ("net_income", ["Net Income"], "money"),
        ("diluted_eps", ["Diluted EPS"], "per_share"),
    ]),
    "balance_sheet": ("get_balance_sheet", [
        ("total_assets", ["Total Assets"], "money"),
        ("total_liabilities", ["Total Liabilities Net Minority Interest"], "money"),
        ("stockholders_equity", ["Stockholders Equity"], "money"),
        ("cash_and_short_term_investments", ["Cash Cash Equivalents And Short Term Investments"], "money"),
        ("total_debt", ["Total Debt"], "money"),
    ]),
    "cash_flow": ("get_cash_flow", [
        ("operating_cash_flow", ["Operating Cash Flow"], "money"),
        ("capital_expenditure", ["Capital Expenditure"], "money"),
        ("free_cash_flow", ["Free Cash Flow"], "money"),
    ]),
}


class DataUnavailable(Exception):
    """The provider returned no usable data."""


class EvidenceStorageError(Exception):
    """Evidence could not be saved; this is not a provider/network failure."""


def number(value):
    """JSON has null, but not NaN or infinity. Preserve a genuine zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float):
        return number(value)
    if hasattr(value, "item"):
        return json_safe(value.item())
    return str(value)


class ToolClient:
    """One client per run. Output paths are relative to output_dir.

    ticker_factory/demo are application-controlled dependency injection,
    never arguments that an LLM can supply.
    """

    def __init__(self, output_dir=None, *, ticker_factory=None, demo=False):
        self.output_dir = Path(output_dir or ROOT / "data" / "tools_v02").resolve()
        self._factory = ticker_factory
        self.demo = bool(demo)
        self._info_cache = {}
        self._info_retrieved = {}

    def _ticker(self, symbol):
        if self._factory is None:
            import yfinance as yf
            cache = ROOT / ".cache" / "yfinance"
            cache.mkdir(parents=True, exist_ok=True)
            yf.set_tz_cache_location(str(cache))
            return yf.Ticker(symbol)
        return self._factory(symbol)

    def _info(self, symbol, ticker):
        # Cache only within this client's run, not as a durable historical record.
        if symbol not in self._info_cache:
            info = ticker.get_info()
            if not isinstance(info, dict) or not info:
                raise DataUnavailable("Yahoo 未返回公司概况。")
            self._info_cache[symbol] = info
            self._info_retrieved[symbol] = datetime.now(timezone.utc).isoformat()
        return self._info_cache[symbol]

    def call_tool(self, name, arguments):
        """Validate, dispatch, save evidence and always return a JSON-safe envelope."""
        result = {
            "schema_version": SCHEMA_VERSION, "tool": name,
            "status": "error", "symbol": None,
            "data": None, "sources": [], "warnings": [], "error": None,
            "artifacts": [],
            "data_kind": "synthetic_demo" if self.demo else "provider_snapshot",
        }
        stage = "validation"
        context = None
        try:
            if name not in SPECS:
                return self._error(result, "UNKNOWN_TOOL", "工具名称不在允许清单中。")
            errors = list(Draft202012Validator(SPECS[name]["parameters"]).iter_errors(arguments))
            if errors:
                return self._error(result, "INVALID_ARGUMENTS", "参数不符合 tool_specs.json；请检查必填项、类型、范围和额外参数。")
            company = resolve_company(arguments["symbol"])
            arguments = dict(arguments)
            if "periods" in arguments:
                # JSON Schema also accepts 2.0 as an integer; Python slicing needs int.
                arguments["periods"] = int(arguments["periods"])
            result["symbol"] = company["course_symbol"]
            # Validate dates before making any provider request or creating a run folder.
            if name == "get_price_history":
                start = date.fromisoformat(arguments["start"])
                end = date.fromisoformat(arguments["end"])
                if start >= end:
                    raise ValueError("start 必须早于 end；end 不包含在结果中。")
                if (end - start).days > 3660:
                    raise ValueError("本工具单次行情区间最多 3660 天。")
            stage = "provider"
            ticker = self._ticker(company["yahoo_symbol"])
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
            context = {
                "relative_dir": Path(company["course_symbol"]) / name / stamp,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "company": company,
            }
            handlers = {
                "get_price_history": self._price,
                "get_company_profile": self._profile,
                "get_financial_statements": self._financials,
            }
            data, sources, warnings, partial = handlers[name](ticker, arguments, context)
            result.update(data=data, sources=sources, warnings=warnings,
                          status="partial" if partial else "ok")
            if self.demo:
                result["warnings"].insert(0, "完全虚构的教学数据；不能用于任何真实公司的研究结论。")
            for source in sources:
                result["artifacts"].append(source["raw_path"])
            stage = "storage"
            result_path = context["relative_dir"] / "result.json"
            result["artifacts"].append(result_path.as_posix())
            self._write_json(result_path, result)
            return json_safe(result)
        except (ValueError, TypeError) as exc:
            if stage == "validation":
                return self._error(result, "INVALID_ARGUMENTS", str(exc))
            return self._error(result, "INVALID_PROVIDER_DATA", "供应商返回的数据无法可靠解析。")
        except DataUnavailable as exc:
            return self._error(result, "NO_DATA", str(exc))
        except EvidenceStorageError:
            return self._error(result, "STORAGE_ERROR", "无法保存证据文件，请检查输出目录。")
        except OSError:
            if stage == "storage":
                return self._error(result, "STORAGE_ERROR", "无法保存证据文件，请检查输出目录。")
            return self._error(result, "UPSTREAM_ERROR", "数据源请求失败，请检查网络或稍后重试。", retryable=True)
        except Exception as exc:
            # Do not leak provider request URLs, cookies, credentials or arbitrary exception text.
            return self._error(result, "UPSTREAM_ERROR", f"工具执行失败（{type(exc).__name__}），未生成研究结论。", retryable=True)

    @staticmethod
    def _error(result, code, message, retryable=False):
        result.update(status="error", data=None,
                      error={"code": code, "message": message, "retryable": retryable})
        return json_safe(result)

    def _write_json(self, relative_path, value):
        path = self.output_dir / relative_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        except OSError as exc:
            raise EvidenceStorageError() from exc
        return path

    def _source(self, context, label, url, raw, *, table=False, parameters=None):
        relative = context["relative_dir"] / (label + (".csv" if table else ".json"))
        path = self.output_dir / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if table:
                raw.to_csv(path, index_label="Date" if label == "price_history" else "provider_metric")
            else:
                self._write_json(relative, raw)
        except OSError as exc:
            raise EvidenceStorageError() from exc
        import yfinance as yf
        return {
            "source_id": f"{context['company']['course_symbol']}:{context['relative_dir'].name}:{label}",
            "provider": "SYNTHETIC_DEMO" if self.demo else "Yahoo Finance via yfinance",
            "source_url": None if self.demo else url,
            "retrieved_at_utc": context["retrieved_at_utc"],
            "raw_path": relative.as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "parameters": parameters or {},
            "library_version": yf.__version__,
        }

    def _price(self, ticker, args, context):
        table = ticker.history(start=args["start"], end=args["end"], interval="1d",
                               auto_adjust=False, actions=True, keepna=True,
                               timeout=20, raise_errors=True)
        if table is None or table.empty:
            raise DataUnavailable("行情为空，不能把空数据解释为 0% 涨跌。")
        summary = summarize_prices(table)
        dates = table.index.date
        if (dates < date.fromisoformat(args["start"])).any() or (dates >= date.fromisoformat(args["end"])).any():
            raise ValueError("Provider returned prices outside the requested interval")
        warnings = ["Close 已反映拆股，Adj Close 还包含分红调整；成交量单位为股。",
                    "没有填补缺失交易日；若包含尚未收市的交易日，末根日线可能未完成。"]
        try:
            metadata = ticker.get_history_metadata() or {}
        except Exception:
            metadata = {}
        currency = metadata.get("currency")
        if not currency:
            warnings.append("Yahoo 未提供报价币种，currency 保留 null。")
        source = self._source(context, "price_history",
                              f"https://finance.yahoo.com/quote/{context['company']['yahoo_symbol']}/history/",
                              table.sort_index(), table=True,
                              parameters={**args, "interval": "1d", "auto_adjust": False})
        metadata_source = self._source(context, "history_metadata",
                                       source["source_url"], metadata)
        data = {"summary": {**summary, "currency": currency, "source_id": source["source_id"]},
                "exchange_timezone": metadata.get("exchangeTimezoneName"),
                "requested_start_inclusive": args["start"], "requested_end_exclusive": args["end"]}
        return data, [source, metadata_source], warnings, not bool(currency)

    def _profile(self, ticker, args, context):
        symbol = context["company"]["yahoo_symbol"]
        info = self._info(symbol, ticker)
        source = self._source(context, "company_info", f"https://finance.yahoo.com/quote/{symbol}/", info)
        source["retrieved_at_utc"] = self._info_retrieved[symbol]
        text_fields = {
            "company_name": "longName", "sector": "sector", "industry": "industry",
            "country": "country", "website": "website", "exchange": "exchange",
            "quote_currency": "currency", "financial_currency": "financialCurrency",
        }
        data = {name: info.get(key) for name, key in text_fields.items()}
        data["business_summary"] = (info.get("longBusinessSummary") or "")[:6000] or None
        numeric_fields = {"market_cap": "marketCap", "current_price": "currentPrice",
                          "trailing_pe": "trailingPE", "forward_pe": "forwardPE",
                          "price_to_book": "priceToBook", "beta": "beta"}
        data["valuation_snapshot"] = {name: number(info.get(key)) for name, key in numeric_fields.items()}
        data["source_fields"] = {**text_fields, "business_summary": "longBusinessSummary", **numeric_fields}
        data.update(source_id=source["source_id"],
                    snapshot_at_utc=source["retrieved_at_utc"],
                    historical_as_of_supported=False)
        warnings = ["这是当前抓取快照；不是历史时点数据，也不代表估值合理性判断。",
                    "trailing/forward PE 保留供应商字段定义；缺失指标保留 null。"]
        missing = [key for key in ("company_name", "quote_currency") if not data[key]]
        if missing:
            warnings.append("概况缺少关键字段：" + ", ".join(missing))
        return data, [source], warnings, bool(missing)

    def _financials(self, ticker, args, context):
        symbol = context["company"]["yahoo_symbol"]
        freq = "yearly" if args["frequency"] == "annual" else "quarterly"
        warnings = [
            "Yahoo 财务表是供应商当前版本，可能包含重述；不支持历史 as-of 过滤。",
            "period_end 来自供应商列标签，仍需与原始财报核对精确财政期间。",
            "期间开始日、披露日及财政季度名称未在此接口中可靠提供，因此保留 null；须用 SEC/IR 原文补齐。",
            "现金流量表的季度口径仍须对照原始财报核验；未自动将累计值换算成单季度。",
            "金额保持供应商基础货币单位，未除以百万；EPS 单位为报表币种/股。CapEx 保留原符号。",
        ]
        sources = []
        try:
            info = self._info(symbol, ticker)
            currency = info.get("financialCurrency")
            # Save only the currency fields here. The full profile already has its
            # own evidence file; writing it again under this label duplicated the
            # artifact byte for byte and left the label describing the wrong thing.
            currency_evidence = {"symbol": symbol, "financialCurrency": currency,
                                 "currency": info.get("currency")}
            currency_source = self._source(context, "financial_currency_info",
                                           f"https://finance.yahoo.com/quote/{symbol}/financials/",
                                           currency_evidence)
            sources.append(currency_source)
            currency_source["retrieved_at_utc"] = self._info_retrieved[symbol]
        except EvidenceStorageError:
            raise
        except Exception:
            currency = None
        partial = not bool(currency)
        if not currency:
            warnings.append("未获取报表币种；保留 null，不能用报价币种代替报表币种。")
        statements = {}
        available = 0
        for statement, (method, metrics) in STATEMENTS.items():
            statement_url = {"income_statement": "financials", "balance_sheet": "balance-sheet", "cash_flow": "cash-flow"}[statement]
            try:
                table = getattr(ticker, method)(freq=freq)
                if table is None or table.empty:
                    raise DataUnavailable("empty statement")
                if not isinstance(table, pd.DataFrame) or table.index.has_duplicates or table.columns.has_duplicates:
                    raise ValueError("Invalid or duplicate statement rows/columns")
                source = self._source(context, statement,
                                      f"https://finance.yahoo.com/quote/{symbol}/{statement_url}/",
                                      table, table=True, parameters={"frequency": args["frequency"]})
                sources.append(source)
                periods = []
                columns = sorted(table.columns, key=lambda col: pd.Timestamp(col), reverse=True)[:args["periods"]]
                missing = set()
                for column in columns:
                    row = {"period_end": pd.Timestamp(column).date().isoformat(),
                           "period_start": None, "filing_date": None, "fiscal_period_label": None,
                           "period_type": "fiscal_year" if freq == "yearly" else "provider_fiscal_quarter",
                           "currency": currency, "source_id": source["source_id"], "metrics": {}}
                    for metric, candidates, unit_type in metrics:
                        # pretty=False returns CamelCase; pretty=True uses spaced names.
                        labels = [label for candidate in candidates for label in (candidate, candidate.replace(" ", ""))]
                        source_row = next((candidate for candidate in labels if candidate in table.index), None)
                        value = number(table.at[source_row, column]) if source_row else None
                        if value is None:
                            missing.add(metric)
                        unit = currency if unit_type == "money" else (f"{currency}/share" if currency else None)
                        row["metrics"][metric] = {"value": value, "unit": unit,
                                                  "source_row": source_row,
                                                  "source_column": pd.Timestamp(column).date().isoformat()}
                    periods.append(row)
                if not any(metric["value"] is not None for row in periods for metric in row["metrics"].values()):
                    raise DataUnavailable("no usable selected metrics")
                statements[statement] = {"status": "ok", "periods": periods}
                available += 1
                if missing:
                    partial = True
                    warnings.append(f"{statement} 部分期间缺失指标：" + ", ".join(sorted(missing)))
                if len(periods) < args["periods"]:
                    partial = True
                    warnings.append(f"{statement} 仅返回 {len(periods)} 个期间，少于请求的 {args['periods']} 个。")
            except EvidenceStorageError:
                raise
            except Exception as exc:
                partial = True
                statements[statement] = {"status": "unavailable", "periods": [],
                                          "error_type": type(exc).__name__}
                warnings.append(f"{statement} 未获取可用数据；其他已取得的报表继续保留。")
        if not available:
            raise DataUnavailable("三张财务表均没有可用的目标指标。")
        return {"frequency": args["frequency"], "requested_periods": args["periods"],
                "financial_currency": currency, "historical_as_of_supported": False,
                "statements": statements}, sources, warnings, partial


if __name__ == "__main__":
    print(json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2))
