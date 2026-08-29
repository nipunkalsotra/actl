"""§21.4 — the live-key guard must abort the process at import time."""

import os
import subprocess
import sys


def test_live_key_prefix_aborts_at_import_time() -> None:
    env = dict(os.environ)
    env["RAZORPAY_KEY_ID"] = "rzp_live_abc"
    result = subprocess.run(
        [sys.executable, "-c", "import actl.config"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "FATAL: ACTL is a test-mode-only system" in result.stderr


def test_default_settings_boot_in_test_mode() -> None:
    env = dict(os.environ)
    env.pop("RAZORPAY_KEY_ID", None)
    result = subprocess.run(
        [sys.executable, "-c", "import actl.config"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_hmac_envelope_test_only_flag_aborts_outside_pytest() -> None:
    """§14.1's HMAC-SHA256 envelope-signing fallback must never be
    reachable in a normal (non-pytest) process, however it was enabled --
    a real .env or production config setting the flag true is exactly
    this scenario."""
    env = dict(os.environ)
    env.pop("PYTEST_VERSION", None)
    env["AGENT_ENVELOPE_HMAC_TEST_ONLY"] = "true"
    result = subprocess.run(
        [sys.executable, "-c", "import actl.config"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "FATAL: agent_envelope_hmac_test_only is enabled outside a pytest run" in result.stderr


def test_hmac_envelope_test_only_flag_boots_under_pytest() -> None:
    env = dict(os.environ)
    env["PYTEST_VERSION"] = "9.1.1"
    env["AGENT_ENVELOPE_HMAC_TEST_ONLY"] = "true"
    result = subprocess.run(
        [sys.executable, "-c", "import actl.config"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_default_settings_never_enable_the_hmac_envelope_fallback() -> None:
    env = dict(os.environ)
    env.pop("AGENT_ENVELOPE_HMAC_TEST_ONLY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import actl.config as c; assert c.settings.agent_envelope_hmac_test_only is False",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
