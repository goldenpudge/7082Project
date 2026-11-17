"""Run three tools without an LLM API key; demo is the default."""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

from tools.client import ToolClient
from tools.demo_provider import DemoTicker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="访问 Yahoo；默认使用虚构样例")
    parser.add_argument("--symbol", choices=["AAPL", "NVDA", "PLTR"], default="AAPL")
    parser.add_argument("--all", action="store_true", help="处理用户选择的全部三家公司")
    parser.add_argument("--frequency", choices=["annual", "quarterly"], default="annual")
    parser.add_argument("--periods", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()
    demo = not args.live
    client = ToolClient(args.output, ticker_factory=DemoTicker if demo else None, demo=demo)
    end = args.end or ("2024-01-04" if demo else date.today().isoformat())
    try:
        start = args.start or ("2024-01-02" if demo else (date.fromisoformat(end) - timedelta(days=365)).isoformat())
        if date.fromisoformat(start) >= date.fromisoformat(end):
            raise ValueError("start 必须早于 end")
    except ValueError:
        parser.error("请提供有效的 YYYY-MM-DD 日期，且 start 早于 end。")
    print("模式：", "SYNTHETIC DEMO（虚构数据）" if demo else "真实 Yahoo 数据")
    print("保存位置：", client.output_dir)
    failed = False
    for symbol in (["AAPL", "NVDA", "PLTR"] if args.all else [args.symbol]):
        requests = [
            ("get_price_history", {"symbol": symbol, "start": start, "end": end}),
            ("get_company_profile", {"symbol": symbol}),
            ("get_financial_statements", {"symbol": symbol, "frequency": args.frequency, "periods": args.periods}),
        ]
        for name, arguments in requests:
            result = client.call_tool(name, arguments)
            failed |= result["status"] == "error"
            print(json.dumps({"symbol": symbol, "tool": name, "status": result["status"],
                              "warnings": result["warnings"], "error": result["error"],
                              "result_file": next((p for p in result["artifacts"] if p.endswith("result.json")), None)},
                             ensure_ascii=False, indent=2))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
