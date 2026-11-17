"""Run the equity research graph for one or more assigned companies.

    python run_agent.py                       # all assigned companies, demo data
    python run_agent.py --dry-run             # tools only, no LLM calls
    python run_agent.py --company AAPL        # one company
    python run_agent.py --live                # real Yahoo provider
"""

import argparse
import sys

from agent import config as cfg
from agent.graph import DEFAULT_REQUEST, build_graph, build_llm
from tools.client import ToolClient
from tools.demo_provider import DemoTicker


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", nargs="+", choices=cfg.ASSIGNED,
                        default=list(cfg.ASSIGNED), metavar="SYMBOL",
                        help="course symbols to analyse (default: all assigned)")
    parser.add_argument("--request", default=DEFAULT_REQUEST,
                        help="what the researcher wants; given to the planner")
    parser.add_argument("--live", action="store_true",
                        help="use the real Yahoo provider instead of synthetic demo data")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every tool step but skip the model calls")
    parser.add_argument("--output", default=str(cfg.REPORT_DIR),
                        help="directory for the markdown reports")
    args = parser.parse_args()

    demo = not args.live
    client = ToolClient(ticker_factory=DemoTicker if demo else None, demo=demo)
    graph = build_graph(client, None if args.dry_run else build_llm(),
                        demo=demo, output_dir=args.output)

    failures = 0
    for symbol in args.company:
        try:
            state = graph.invoke({"symbol": symbol, "request": args.request})
        except Exception as exc:
            print(f"{symbol}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
            failures += 1
            continue
        print(f"{symbol}: {state['report_path']}")
        print(f"  data={('demo' if demo else 'live')} "
              f"sources={len(state['sources'])} warnings={len(state['warnings'])} "
              f"periods={state['plan']['periods']} frequency={state['plan']['frequency']}")
        print(f"  tool calls: {', '.join(entry['tool'] for entry in state['tool_log'])}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
