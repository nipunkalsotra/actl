"""start.sh/stop.sh/status.sh/logs.sh must never touch a real secret,
never hang forever waiting on a service that never becomes healthy,
never mistake a stale or unrelated process for one it owns, and never
kill something it didn't start. The pure functions behind all of that
live in scripts/_launcher_lib.sh -- sourced here (it only defines
functions/constants, so sourcing it is side-effect-free) and exercised
directly with stubbed ps/lsof/docker/curl on PATH, mirroring
tests/unit/test_clone_to_demo_script.py's approach. Never touches real
Docker, a real network call, or a real ACTL process.
"""

from __future__ import annotations

import re
import stat
import subprocess
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "scripts" / "_launcher_lib.sh"
START_SH = Path(__file__).resolve().parents[2] / "start.sh"
REPO_ROOT = LIB.resolve().parents[1]


def _run(
    function_call: str, *, cwd: Path, path_prepend: Path | None = None, timeout: float = 10
) -> subprocess.CompletedProcess[str]:
    env_path = f"{path_prepend}:$PATH" if path_prepend else "$PATH"
    # `source` runs the library in *this* shell, so its own `set -e`
    # carries over into the test command below it -- without `set +e`
    # here, a deliberately-failing check as the last part of an `&&`/`;`
    # chain (exactly what several tests below assert on) would kill this
    # whole bash -c invocation before its trailing `echo "exit=$?"` runs.
    command = f'source "{LIB}"; set +e; PATH="{env_path}"; {function_call}'
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


def _write_fake(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


# --------------------------------------------------------------------------
# Safe .env generation -- never invents/exposes a secret
# --------------------------------------------------------------------------


def test_generate_safe_env_forces_safe_defaults_and_never_invents_secrets(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "PAYMENT_PROVIDER=razorpay\n"
        "LLM_ENABLED=true\n"
        "ANCHOR_PROVIDER=noop\n"
        "RAZORPAY_KEY_ID=rzp_test_placeholder\n"
    )
    target = tmp_path / ".env"

    result = _run(f'generate_safe_env "{example}" "{target}"', cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    content = target.read_text()
    assert "PAYMENT_PROVIDER=simulator" in content
    assert "LLM_ENABLED=false" in content
    assert "ANCHOR_PROVIDER=noop" in content
    # Only the three safety-relevant fields are ever forced -- every other
    # line (including a placeholder "secret") passes through verbatim,
    # never invented or fetched from anywhere.
    assert "RAZORPAY_KEY_ID=rzp_test_placeholder" in content


def test_generate_safe_env_appends_a_default_missing_from_the_example(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("PAYMENT_PROVIDER=razorpay\nLLM_ENABLED=true\n")  # no ANCHOR_PROVIDER line
    target = tmp_path / ".env"

    result = _run(f'generate_safe_env "{example}" "{target}"', cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "ANCHOR_PROVIDER=noop" in target.read_text()


def test_generate_safe_env_sets_restrictive_permissions(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("PAYMENT_PROVIDER=razorpay\n")
    target = tmp_path / ".env"

    result = _run(f'generate_safe_env "{example}" "{target}"', cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected mode 600, got {oct(mode)}"


# --------------------------------------------------------------------------
# Bounded readiness timeout -> diagnostics printed, never an unbounded hang
# --------------------------------------------------------------------------


def test_wait_http_ready_times_out_with_log_tail_instead_of_hanging(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake(fake_bin / "curl", "exit 1\n")  # never succeeds

    logfile = tmp_path / "backend.log"
    logfile.write_text("boot line 1\nboot line 2\n")

    start = time.monotonic()
    result = _run(
        f'wait_http_ready "backend" 1 "http://127.0.0.1:8000/readyz" "{logfile}"',
        cwd=tmp_path,
        path_prepend=fake_bin,
        timeout=10,
    )
    elapsed = time.monotonic() - start

    assert result.returncode != 0, "wait_http_ready must exit non-zero on timeout, never hang"
    assert elapsed < 5, f"wait_http_ready did not honour its 1s bound (took {elapsed:.1f}s)"
    assert "did not become ready within 1s" in result.stderr
    assert str(logfile) in result.stderr
    assert "boot line 1" in result.stderr


def test_wait_http_ready_succeeds_immediately_when_the_check_passes(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake(fake_bin / "curl", "exit 0\n")

    result = _run(
        'wait_http_ready "backend" 5 "http://127.0.0.1:8000/readyz" "/dev/null"',
        cwd=tmp_path,
        path_prepend=fake_bin,
    )

    assert result.returncode == 0, result.stderr


def test_wait_compose_ready_times_out_with_compose_diagnostics(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake(
        fake_bin / "docker",
        'if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then echo FAKE_PS; exit 0; fi\n'
        'if [ "$1" = "compose" ] && [ "$2" = "logs" ]; then echo FAKE_LOGS; exit 0; fi\n'
        "exit 0\n",
    )

    result = _run('wait_compose_ready "postgres" 1 false', cwd=tmp_path, path_prepend=fake_bin)

    assert result.returncode != 0, "wait_compose_ready must exit non-zero on timeout, never hang"
    assert "did not become ready within 1s" in result.stderr
    assert "FAKE_PS" in result.stderr
    assert "FAKE_LOGS" in result.stderr


# --------------------------------------------------------------------------
# ACTL-owned PID reuse, stale PID handling, unrelated port owner
# --------------------------------------------------------------------------


def test_actl_owned_backend_pid_is_recognised_and_reused(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    proc = subprocess.Popen(["sleep", "30"])
    try:
        _write_fake(
            fake_bin / "ps",
            f'if [ "$1" = "-p" ] && [ "$2" = "{proc.pid}" ]; then '
            'echo "uv run uvicorn actl.main:app --port 8000"; exit 0; fi\n'
            "exit 1\n",
        )
        pidfile = tmp_path / "backend.pid"
        pidfile.write_text(str(proc.pid))

        result = _run(
            f'pid="$(read_pidfile "{pidfile}")" && is_actl_backend_pid "$pid" && echo REUSE',
            cwd=tmp_path,
            path_prepend=fake_bin,
        )
        assert result.returncode == 0, result.stderr
        assert "REUSE" in result.stdout
    finally:
        proc.terminate()
        proc.wait()


def test_actl_owned_frontend_pid_checks_both_command_and_working_directory(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    proc = subprocess.Popen(["sleep", "30"])
    try:
        _write_fake(
            fake_bin / "ps",
            f'if [ "$1" = "-p" ] && [ "$2" = "{proc.pid}" ]; then '
            'echo "node node_modules/.bin/vite"; exit 0; fi\n'
            "exit 1\n",
        )
        # process_cwd parses lsof -Fn's "p<pid>" / "n<cwd>" lines.
        _write_fake(fake_bin / "lsof", f'echo "p{proc.pid}"\necho "n{REPO_ROOT / "web"}"\n')
        pidfile = tmp_path / "frontend.pid"
        pidfile.write_text(str(proc.pid))

        result = _run(
            f'pid="$(read_pidfile "{pidfile}")" && is_actl_frontend_pid "$pid" && echo REUSE',
            cwd=tmp_path,
            path_prepend=fake_bin,
        )
        assert result.returncode == 0, result.stderr
        assert "REUSE" in result.stdout
    finally:
        proc.terminate()
        proc.wait()


def test_actl_owned_frontend_pid_rejects_a_vite_process_in_a_different_dir(tmp_path: Path) -> None:
    # A `vite` dev server for some *other* project on the same machine
    # must never be treated as this repo's own frontend, even though its
    # command line alone would otherwise match.
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    proc = subprocess.Popen(["sleep", "30"])
    try:
        _write_fake(
            fake_bin / "ps",
            f'if [ "$1" = "-p" ] && [ "$2" = "{proc.pid}" ]; then '
            'echo "node node_modules/.bin/vite"; exit 0; fi\n'
            "exit 1\n",
        )
        _write_fake(fake_bin / "lsof", f'echo "p{proc.pid}"\necho "n/some/other/project/web"\n')

        result = _run(
            f"is_actl_frontend_pid {proc.pid}; echo exit=$?", cwd=tmp_path, path_prepend=fake_bin
        )
        assert "exit=1" in result.stdout
    finally:
        proc.terminate()
        proc.wait()


def test_stale_pid_file_is_never_treated_as_actl_owned(tmp_path: Path) -> None:
    # PID recorded but the process is long gone -- read_pidfile only
    # validates the file's *format*, so it still returns it; the caller's
    # is_actl_backend_pid check is what must catch this and say "no".
    dead_proc = subprocess.Popen(["true"])
    dead_proc.wait()
    pidfile = tmp_path / "backend.pid"
    pidfile.write_text(str(dead_proc.pid))

    result = _run(
        f'pid="$(read_pidfile "{pidfile}")" && is_actl_backend_pid "$pid"; echo "exit=$?"',
        cwd=tmp_path,
    )
    assert "exit=1" in result.stdout, "a dead PID must never be recognised as an ACTL-owned process"


def test_garbage_pid_file_content_is_rejected(tmp_path: Path) -> None:
    pidfile = tmp_path / "backend.pid"
    pidfile.write_text("not-a-pid\n")

    result = _run(f'read_pidfile "{pidfile}"; echo "exit=$?"', cwd=tmp_path)

    lines = result.stdout.strip().splitlines()
    assert "exit=1" in lines
    assert "not-a-pid" not in lines, "malformed PID-file content must never be echoed back"


def test_unrelated_port_owner_is_identified_but_never_reported_as_actl_owned(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    proc = subprocess.Popen(["sleep", "30"])
    try:
        _write_fake(fake_bin / "lsof", f'echo "{proc.pid}"\n')
        # No ps stub here on purpose: the real `ps` output for a genuine
        # `sleep 30` process never contains "actl.main:app", which is
        # itself part of what this test is proving.

        result = _run(
            f'owner="$(port_owner_pid 8000)"; '
            f'[ "$owner" = "{proc.pid}" ] && echo "OWNER_FOUND=$owner"; '
            f'is_actl_backend_pid "$owner"; echo "owned=$?"',
            cwd=tmp_path,
            path_prepend=fake_bin,
        )
        assert f"OWNER_FOUND={proc.pid}" in result.stdout
        assert "owned=1" in result.stdout, (
            "a real port occupant that isn't ACTL-owned must never be reported as ACTL-owned -- "
            "this is the exact condition start.sh relies on to refuse instead of killing it"
        )
    finally:
        proc.terminate()
        proc.wait()


def test_start_sh_never_calls_kill() -> None:
    # start.sh only ever starts or reuses services -- refusing on a port
    # conflict, never terminating an unrelated process, is enforced here
    # as a static fact about the script: it never invokes `kill` as a
    # command (mentions of the word inside comments/echoed strings, e.g.
    # the refusal message's own "kill <pid> yourself" suggestion, don't
    # count -- only `kill` as the first word of a statement would).
    text = START_SH.read_text()
    assert not re.search(r"^[ \t]*kill\b", text, re.MULTILINE), (
        "start.sh must never call kill -- an unrelated port owner is reported, not stopped"
    )
