"""Keep unit tests off the live action-policy model unless a test opts in."""
from __future__ import annotations

import os

# Read at call time by services.action_policy.enabled.
os.environ.setdefault("BRIGHTNEST_ACTION_POLICY", "0")
