"""Configuration for the LangGraph equity research agent.

Everything here is decided by the *application*, never by the LLM: which
companies may be analysed, which model is used, and how much history is
requested. None of these are tool arguments, so a model cannot change them.
"""

import os

from paths import ROOT
from tools.market_data import COMPANIES

# The companies this group selected from the course list. Single source of truth:
# the planner schema enum, the prompt catalogue and the CLI all read this list.
ASSIGNED = ["AAPL", "NVDA", "PLTR"]
CATALOG = {company["course_symbol"]: company for company in COMPANIES}

# Any provider with an OpenAI-compatible endpoint works, because ChatOpenAI is
# pointed at it with the three values below. Override them in the environment;
# the defaults are merely what this group happened to test with.
API_KEY_ENV = "LLM_API_KEY"
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
TEMPERATURE = 0.2
MAX_TOKENS = 30000
REQUEST_TIMEOUT = 120

# Tool limits mirror tool_specs.json.
MAX_PERIODS = 8
REQUESTED_PERIODS = 4
DEMO_PERIODS = 2  # the synthetic provider only carries two fiscal years

# The demo provider serves one fixed price window only.
DEMO_PRICE_WINDOW = ("2024-01-02", "2024-01-04")
LIVE_PRICE_YEARS = 3

# The analyst model drives the tool loop, but this floor is enforced by the graph:
# every run must cover these tools even if the model skips one of them. Optional
# tools added later are left entirely to the model.
REQUIRED_TOOLS = ["get_company_profile", "get_financial_statements", "get_price_history"]
MAX_TOOL_ROUNDS = 3  # model turns that may request tools
MAX_TOOL_CALLS = 8  # hard cap on executed tool calls per company

REPORT_SECTIONS = [
    "Business snapshot",
    "Price and volume performance",
    "Income statement trend",
    "Balance sheet and liquidity",
    "Cash flow quality",
    "Recent trajectory and forward-looking signals",
    "What this analysis cannot say",
]

REPORT_DIR = ROOT / "reports"
