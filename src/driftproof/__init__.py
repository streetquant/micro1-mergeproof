"""DriftProof: an independent release gate for agent-authored dbt repairs."""

from __future__ import annotations

from .gate import baseline_green_gate, review_project
from .models import Verdict

__all__ = ["Verdict", "baseline_green_gate", "review_project"]
__version__ = "0.2.0"
