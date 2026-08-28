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
