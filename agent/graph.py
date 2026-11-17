"""The LangGraph pipeline: plan -> analyst -> summarizer -> writer -> finalize.

Two levels of control are deliberately kept apart:

* the *graph* fixes the order of the steps, so the same request always follows the
  same pipeline;
* inside the analyst step the *model* decides which tools to call, with which
  arguments and how many times. More tools will be added later and only the
  required ones are fixed (``cfg.REQUIRED_TOOLS``), so the code guarantees that
  floor and leaves everything else to the model.

Two representations of evidence are kept apart as well:

* the *full tool envelope* (schema_version, sha256, artifacts, ...) is the audit
  record and stays in the state and on disk;
* the *model view* is a small projection of that envelope, with provider ids
  replaced by readable labels such as ``income_statement``.
"""

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from agent import config as cfg
from agent.prompts import (
    ANALYST_PROMPT,
    METRIC_WHITELIST,
    PLAN_SCHEMA,
    PLANNER_PROMPT,
    SUMMARIZER_PROMPT,
    WRITER_PROMPT,
)
from tools.client import TOOL_SPECS

DEFAULT_REQUEST = (
    "Assess this company's recent performance and the forward-looking signals "
    "visible in its financial statements and price history."
)
DEFAULT_FOCUS = ["revenue", "operating_income", "net_income", "free_cash_flow", "total_debt"]
DEFAULT_QUESTIONS = [
    "Is revenue growing, and is the growth converting into operating profit?",
    "Is the balance sheet getting stronger or weaker?",
    "Is cash generation consistent with reported earnings?",
]


class ResearchState(TypedDict, total=False):
    """Explicit state: every hand-off between steps is visible here."""

    symbol: str
    request: str
    plan: dict
    evidence: list  # full envelopes, audit record
    sources: list  # deduplicated provenance, rendered into the report appendix
    tool_log: list  # which tools the model chose, with which arguments and status
    warnings: list
    analysis: str
    summary: str
    report: str
    report_path: str


def build_llm():
    """ChatOpenAI aimed at any OpenAI-compatible endpoint (see agent/config.py)."""
    key = os.environ.get(cfg.API_KEY_ENV)
    if not key:
        raise SystemExit(f"{cfg.API_KEY_ENV} is not set. Set it, or use --dry-run.")
    return ChatOpenAI(
        model=cfg.MODEL,
        api_key=key,
        base_url=cfg.BASE_URL,
        temperature=cfg.TEMPERATURE,
        max_tokens=cfg.MAX_TOKENS,
        timeout=cfg.REQUEST_TIMEOUT,
        max_retries=2,
        # The httpx2 build in this environment raises inside its zstd decoder, so
        # ask the provider for an uncompressed body. Harmless when it is not needed.
        default_headers={"Accept-Encoding": "identity"},
    )


def _message_text(message):
    content = message.content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return content


def _dedupe(items):
    seen, kept = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            kept.append(item)
    return kept


def _price_window(demo):
    if demo:
        return cfg.DEMO_PRICE_WINDOW
    end = date.today()
    return (end - timedelta(days=365 * cfg.LIVE_PRICE_YEARS)).isoformat(), end.isoformat()


def _tool_schemas():
    """tool_specs.json is JSON Schema already, so it maps 1:1 onto OpenAI tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for spec in TOOL_SPECS
    ]


def _source_label(source_id):
    """'AAPL:20240101T...Z-abc:income_statement' -> 'income_statement'."""
    return source_id.rsplit(":", 1)[-1] if source_id else None


def _slim_profile(data):
    summary = data.get("business_summary")
    return {
        "source_ref": _source_label(data.get("source_id")),
        "company_name": data.get("company_name"),
        "sector": data.get("sector"),
        "industry": data.get("industry"),
        "country": data.get("country"),
        "quote_currency": data.get("quote_currency"),
        "financial_currency": data.get("financial_currency"),
        "business_summary": summary[:1200] if summary else None,
        "valuation_snapshot": data.get("valuation_snapshot"),
        "snapshot_at_utc": data.get("snapshot_at_utc"),
        "historical_as_of_supported": data.get("historical_as_of_supported"),
    }


def _slim_price(data):
    summary = dict(data.get("summary") or {})
    return {
        "source_ref": _source_label(summary.get("source_id")),
        "summary": {key: value for key, value in summary.items() if key != "source_id"},
        "exchange_timezone": data.get("exchange_timezone"),
        "requested_start_inclusive": data.get("requested_start_inclusive"),
        "requested_end_exclusive": data.get("requested_end_exclusive"),
    }


def _slim_financials(data):
    statements = {}
    for name, statement in (data.get("statements") or {}).items():
        periods = []
        for period in statement.get("periods") or []:
            periods.append({
                "period_end": period.get("period_end"),
                "period_type": period.get("period_type"),
                "currency": period.get("currency"),
                "metrics": {
                    metric: {"value": value.get("value"), "unit": value.get("unit")}
                    for metric, value in (period.get("metrics") or {}).items()
                },
                "source_ref": _source_label(period.get("source_id")),
            })
        statements[name] = {"status": statement.get("status"), "periods": periods}
    return {
        "frequency": data.get("frequency"),
        "financial_currency": data.get("financial_currency"),
        "historical_as_of_supported": data.get("historical_as_of_supported"),
        "statements": statements,
    }


def _projection(envelope):
    """The model view of one tool result: no hashes, no run folders, readable labels."""
    name = envelope.get("tool")
    if envelope.get("status") == "error":
        return {
            "tool": name,
            "status": "error",
            "error": envelope.get("error"),
        }
    data = envelope.get("data") or {}
    if name == "get_company_profile":
        body = _slim_profile(data)
    elif name == "get_financial_statements":
        body = _slim_financials(data)
    elif name == "get_price_history":
        body = _slim_price(data)
    else:
        body = data
    return {
        "tool": name,
        "status": envelope.get("status"),
        "data_kind": envelope.get("data_kind"),
        "warnings": envelope.get("warnings") or [],
        "data": body,
    }


def _as_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _default_plan(symbol, demo):
    return {
        "frequency": "annual",
        "periods": cfg.DEMO_PERIODS if demo else cfg.REQUESTED_PERIODS,
        "focus": DEFAULT_FOCUS,
        "thesis_questions": DEFAULT_QUESTIONS,
    }


def _checked_plan(plan, demo):
    """Clamp what the model returned to what the tools can actually serve."""
    max_periods = min(cfg.MAX_PERIODS, cfg.DEMO_PERIODS if demo else cfg.MAX_PERIODS)
    frequency = plan.get("frequency")
    if frequency not in ("annual", "quarterly"):
        frequency = "annual"
    try:
        periods = int(plan.get("periods") or cfg.REQUESTED_PERIODS)
    except (TypeError, ValueError):
        periods = cfg.REQUESTED_PERIODS
    focus = [metric for metric in plan.get("focus") or [] if metric in METRIC_WHITELIST]
    questions = [question.strip() for question in plan.get("thesis_questions") or []
                 if isinstance(question, str) and question.strip()]
    return {
        "frequency": frequency,
        "periods": max(1, min(periods, max_periods)),
        "focus": focus[:6] or DEFAULT_FOCUS,
        "thesis_questions": questions[:5] or DEFAULT_QUESTIONS,
    }


def build_graph(client, llm=None, *, demo=False, output_dir=None):
    """Compile the graph. ``llm=None`` runs every tool step but skips the model."""
    out_dir = Path(output_dir or cfg.REPORT_DIR)
    mode = "synthetic demo data (invented, not a real company)" if demo else "live provider data"

    def plan_node(state: ResearchState) -> dict:
        symbol = state["symbol"]
        # Enforced here, not only in the CLI, so any entry point is bound by ASSIGNED.
        if symbol not in cfg.ASSIGNED:
            raise ValueError(f"{symbol} is not an assigned company; choose from {cfg.ASSIGNED}.")
        company = cfg.CATALOG[symbol]
        request = state.get("request") or DEFAULT_REQUEST
        max_periods = min(cfg.MAX_PERIODS, cfg.DEMO_PERIODS if demo else cfg.MAX_PERIODS)
        if llm is None:
            return {"plan": _default_plan(symbol, demo)}
        # Some providers reject response_format=json_schema, so the plan is
        # obtained through a forced tool call instead of strict structured output.
        planner = llm.with_structured_output(PLAN_SCHEMA, method="function_calling")
        raw = planner.invoke(PLANNER_PROMPT.format(
            company=company["name"], symbol=symbol, mode=mode,
            request=request, max_periods=max_periods, metrics=", ".join(METRIC_WHITELIST),
        ))
        return {"plan": _checked_plan(raw or {}, demo)}

    def analyst_node(state: ResearchState) -> dict:
        """ReAct loop: the model picks the tools, the code enforces the floor."""
        symbol = state["symbol"]
        plan = state["plan"]
        start, end = _price_window(demo)
        evidence, sources, tool_log, warnings = [], [], [], []
        seen_sources, cache = set(), {}
        executed = 0

        def run_tool(name, arguments):
            nonlocal executed
            key = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
            if key in cache:
                return cache[key]
            executed += 1
            envelope = client.call_tool(name, arguments)
            cache[key] = envelope
            evidence.append(envelope)
            tool_log.append({
                "tool": name,
                "arguments": arguments,
                "status": envelope["status"],
                "error": (envelope.get("error") or {}).get("code"),
            })
            for source in envelope["sources"]:
                if source["source_id"] not in seen_sources:
                    seen_sources.add(source["source_id"])
                    sources.append(source)
            warnings.extend(envelope["warnings"])
            return envelope

        # The arguments the pipeline uses when it has to fill in a mandatory tool.
        required_arguments = {
            "get_company_profile": {"symbol": symbol},
            "get_financial_statements": {
                "symbol": symbol, "frequency": plan["frequency"], "periods": plan["periods"],
            },
            "get_price_history": {"symbol": symbol, "start": start, "end": end},
        }

        def bundle(analysis):
            return {
                "evidence": evidence,
                "sources": sources,
                "tool_log": tool_log,
                "warnings": _dedupe(warnings),
                "analysis": analysis,
            }

        if llm is None:
            for name in cfg.REQUIRED_TOOLS:
                run_tool(name, required_arguments[name])
            return bundle("[DRY RUN] analyst model skipped: only the mandatory tools were called.")

        company = cfg.CATALOG[symbol]
        messages = [
            SystemMessage(content=ANALYST_PROMPT.format(
                company=company["name"], symbol=symbol, mode=mode,
                today=date.today().isoformat(), price_window=f"{start} to {end}",
                plan=_as_json(plan), required=", ".join(cfg.REQUIRED_TOOLS),
            )),
            HumanMessage(content=(
                "Collect the evidence you need with the tools, then write the analysis."
            )),
        ]
        runner = llm.bind_tools(_tool_schemas())
        response, rounds = None, 0
        while True:
            response = runner.invoke(messages)
            messages.append(response)
            pending = getattr(response, "tool_calls", None) or []
            if not pending:
                break
            rounds += 1
            allowed = rounds <= cfg.MAX_TOOL_ROUNDS
            for call in pending:
                if not allowed or executed >= cfg.MAX_TOOL_CALLS:
                    content = _as_json({
                        "status": "refused",
                        "reason": "Tool budget exhausted. Write the analysis from the evidence "
                                  "already collected.",
                    })
                else:
                    content = _as_json(_projection(run_tool(call["name"], call.get("args") or {})))
                messages.append(ToolMessage(content=content, tool_call_id=call["id"]))
            if not allowed:
                break

        # A failed call does not satisfy the floor: the tool has to actually deliver.
        called = {entry["tool"] for entry in tool_log if entry["status"] != "error"}
        missing = [name for name in cfg.REQUIRED_TOOLS if name not in called]
        text = "" if (response is None or response.tool_calls) else _message_text(response).strip()
        if missing:
            # ToolMessage must answer a tool_call_id, so the injected data travels as a
            # human turn instead: still visible to the model, still auditable on disk.
            blocks = [
                _as_json(_projection(run_tool(name, required_arguments[name])))
                for name in missing
            ]
            messages.append(HumanMessage(content=(
                "The pipeline requires these tools to run every time, so it collected the "
                "missing results for you:\n\n" + "\n\n".join(blocks)
                + "\n\nNow write the analysis using the tool results above."
            )))
            text = ""
        if not text:
            if not missing:
                messages.append(HumanMessage(content=(
                    "Write the analysis now, using only the tool results above."
                )))
            text = _message_text(llm.invoke(messages)).strip()
        return bundle(text)

    def _context(state: ResearchState) -> dict:
        company = cfg.CATALOG[state["symbol"]]
        return {
            "company": company["name"],
            "symbol": state["symbol"],
            "mode": mode,
            "plan": _as_json(state["plan"]),
        }

    def summarizer_node(state: ResearchState) -> dict:
        if llm is None:
            return {"summary": "[DRY RUN] summarizer step skipped: no model configured."}
        context = _context(state)
        prompt = SUMMARIZER_PROMPT.format(
            company=context["company"], symbol=context["symbol"], analysis=state["analysis"],
        )
        return {"summary": _message_text(llm.invoke(prompt))}

    def writer_node(state: ResearchState) -> dict:
        if llm is None:
            return {"report": "[DRY RUN] writer step skipped: no model configured."}
        prompt = WRITER_PROMPT.format(
            summary=state["summary"],
            analysis=state["analysis"],
            sections="\n".join(f"## {name}" for name in cfg.REPORT_SECTIONS),
            **_context(state),
        )
        return {"report": _message_text(llm.invoke(prompt))}

    def finalize_node(state: ResearchState) -> dict:
        """Deterministic tail: the model never formats provenance."""
        symbol = state["symbol"]
        body = state["report"].rstrip()
        if demo:
            body = ("> **Synthetic demo data.** Every figure below comes from an invented "
                    "teaching provider, not from a real filing.\n\n" + body)
        lines = [body, "", "## Data and provenance", ""]
        lines.append(f"- Data mode: {'synthetic demo' if demo else 'live provider'}")
        if state["warnings"]:
            lines.extend(f"- Warning: {warning}" for warning in state["warnings"])
        lines.extend(["", "## Tool calls", ""])
        for entry in state["tool_log"]:
            status = entry["status"] + (f" ({entry['error']})" if entry["error"] else "")
            arguments = json.dumps(entry["arguments"], ensure_ascii=False, sort_keys=True)
            lines.append(f"- `{entry['tool']}` — `{arguments}` — {status}")
        lines.extend(["", "## Sources", ""])
        for source in state["sources"]:
            url = source.get("source_url") or "no URL (synthetic demo)"
            lines.append(
                f"- `{source['source_id']}` — {source['provider']} — {url} — "
                f"file `{source['raw_path']}` — sha256 `{source['sha256']}`"
            )
        report = "\n".join(lines) + "\n"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"{symbol}_{'demo' if demo else 'live'}_{stamp}.md"
        path.write_text(report, encoding="utf-8")
        return {"report": report, "report_path": str(path.resolve())}

    graph = StateGraph(ResearchState)
    graph.add_node("plan", plan_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("summarizer", summarizer_node)
    graph.add_node("writer", writer_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "analyst")
    graph.add_edge("analyst", "summarizer")
    graph.add_edge("summarizer", "writer")
    graph.add_edge("writer", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
