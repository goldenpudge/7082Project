"""第一课：Yahoo Finance -> DataFrame -> dict -> 可核验的本地文件。"""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from paths import ROOT

COMPANIES = json.loads((ROOT / "companies.json").read_text(encoding="utf-8"))


def resolve_company(symbol):
    """只接受课程名单中的代码，也接受对应的 Yahoo 代码。"""
    symbol = symbol.strip().upper()
    for company in COMPANIES:
        if symbol in (company["course_symbol"], company["yahoo_symbol"]):
            return company
    raise ValueError("代码不在课程的 15 家名单中，请运行 --list 查看。")


def summarize_prices(table):
    """纯计算函数：它不访问网络，也不调用 LLM。"""
    required = ["Close", "Adj Close", "Volume"]
    if not set(required).issubset(table.columns):
        raise ValueError("数据缺少 Close、Adj Close 或 Volume，不能可靠计算。")
    if not isinstance(table.index, pd.DatetimeIndex):
        raise ValueError("数据必须使用日期索引。")
    if len(table) < 2 or table.index.has_duplicates or table.index.hasnans:
        raise ValueError("至少需要两个交易日，日期不能缺失或重复。")
    table = table.sort_index()
    values = table[required].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("价格或成交量存在缺失/无穷值，请核对源数据。")
    if (values[["Close", "Adj Close"]] <= 0).any().any():
        raise ValueError("收盘价必须为正数。")
    if (values["Volume"] < 0).any() or (values["Volume"] % 1 != 0).any():
        raise ValueError("成交股数必须是非负整数。")

    adjusted = values["Adj Close"]
    return {
        "first_session": table.index[0].date().isoformat(),
        "last_session": table.index[-1].date().isoformat(),
        "trading_rows": len(table),
        "first_adjusted_close": float(adjusted.iloc[0]),
        "last_adjusted_close": float(adjusted.iloc[-1]),
        "adjusted_close_change_pct": float((adjusted.iloc[-1] / adjusted.iloc[0] - 1) * 100),
        "last_close": float(values["Close"].iloc[-1]),
        "mean_daily_volume_shares": float(values["Volume"].mean()),
        "last_volume_shares": int(values["Volume"].iloc[-1]),
        "calculation": "(last_adjusted_close / first_adjusted_close - 1) * 100",
        "price_basis": "Close: Yahoo split-adjusted; Adj Close: additionally dividend-adjusted",
    }


def save_evidence(table, summary, source, output_root):
    """每次运行建新目录，保存原始返回表、摘要、来源及 SHA-256。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    source = {**source, "source_id": f"{source['source_id']}-{source['yahoo_symbol']}-{stamp}"}
    folder = Path(output_root) / source["yahoo_symbol"] / stamp
    folder.mkdir(parents=True, exist_ok=False)
    csv_path = folder / "downloaded_bars.csv"
    table.to_csv(csv_path, index_label="Date")

    summary = {**summary, "source_id": source["source_id"], "currency": source.get("currency")}
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    source["files_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (csv_path, summary_path)
    }
    (folder / "provenance.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return {"summary": summary, "source": source, "evidence_dir": str(folder.resolve())}


def get_price(symbol, start, end, output_root=None):
    """未来可注册成 LLM tool；现在先由我们手动调用它。end 不包含当天。"""
    company = resolve_company(symbol)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date >= end_date:
        raise ValueError("开始日期必须早于结束日期；结束日期不包含在结果中。")

    import yfinance as yf

    cache = ROOT / ".cache" / "yfinance"
    cache.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache))
    ticker = yf.Ticker(company["yahoo_symbol"])
    # 显式关闭自动复权，分别保留 Close、Adj Close 和成交股数。
    table = ticker.history(
        start=start, end=end, interval="1d", auto_adjust=False,
        actions=True, keepna=True, timeout=20, raise_errors=True,
    )
    if table.empty:
        raise ValueError("Yahoo 未返回数据；请检查代码、日期或访问限制，不能把空表当成零。")
    table = table.sort_index()
    dates = table.index.date
    if (dates < start_date).any() or (dates >= end_date).any():
        raise ValueError("返回的数据越过请求日期范围，请核对。")
    summary = summarize_prices(table)
    metadata = ticker.get_history_metadata() or {}
    source = {
        "source_id": "YF-PRICE-1",
        "kind": "downloaded_market_data",
        **company,
        "source_url": f"https://finance.yahoo.com/quote/{company['yahoo_symbol']}/history/",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_start_inclusive": start,
        "requested_end_exclusive": end,
        "interval": "1d",
        "auto_adjust": False,
        "currency": metadata.get("currency"),
        "exchange_timezone": metadata.get("exchangeTimezoneName"),
        "library": "yfinance",
        "library_version": yf.__version__,
        "pandas_version": pd.__version__,
        "warnings": [
            "实际交易日范围以摘要为准；未填补休市日或缺失交易日。",
            "若请求包含尚未收市的交易日，最后一根日线可能尚未完成。",
            "复权序列是供应商口径；后续财报与文字稿需独立采集。",
        ] + ([] if metadata.get("currency") else ["源数据未提供币种，摘要保留 null。"]),
    }
    return save_evidence(table, summary, source, output_root or ROOT / "data")


def run_demo(output_root=None):
    """虚构除息示意：分红 1 单位后 Close 从 100 到 99，复权变化为 0%。"""
    table = pd.DataFrame(
        {"Close": [100.0, 99.0], "Adj Close": [99.0, 99.0], "Volume": [1000, 2000]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    source = {
        "source_id": "DEMO-PRICE-1", "kind": "SYNTHETIC_TEACHING_DATA",
        "yahoo_symbol": "DEMO_NOT_A_COMPANY", "currency": "DEMO_UNIT",
        "source_url": None, "retrieved_at_utc": None,
        "warning": "虚构教学数据，未联网，不能写入公司报告。",
    }
    return save_evidence(table, summarize_prices(table), source, output_root or ROOT / "data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="列出全部 15 家公司")
    parser.add_argument("--demo", action="store_true", help="运行明确标注的虚构数据")
    parser.add_argument("--symbol", help="你选择的课程代码")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD，包含")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD，不包含")
    args = parser.parse_args()
    if args.list:
        for company in COMPANIES:
            print(company["course_symbol"], company["name"], "->", company["yahoo_symbol"])
        return
    if args.demo:
        if args.symbol:
            parser.error("--demo 与 --symbol 不能同时使用。")
        result = run_demo()
    elif args.symbol and args.start and args.end:
        try:
            result = get_price(args.symbol, args.start, args.end)
        except Exception as exc:
            parser.exit(1, f"数据读取失败 ({type(exc).__name__}): {exc}\n")
    else:
        parser.error("请选择 --list、--demo，或同时提供 --symbol、--start、--end。")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
