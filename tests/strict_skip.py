"""Skip a test locally, but fail it when an environment variable says it must run.

CI sets these so a missing dependency or missing git tags cannot pass silently.
"""

from __future__ import annotations

import os

import pytest

REQUIRE_SEMANTIC_ENV = "AO_REQUIRE_SEMANTIC"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no")


def skip_unless_required(reason: str, env_var: str) -> None:
    """pytest.fail when env_var is truthy, pytest.skip otherwise."""
    if env_flag(env_var):
        pytest.fail(f"{reason} ({env_var} is set, so this test must run)", pytrace=False)
    pytest.skip(reason)
