"""Project root, resolved from this file's own location.

Every module imports ROOT from here instead of counting parent directories, so
moving code between packages never changes where companies.json, tool_specs.json,
data/ or reports/ live.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
