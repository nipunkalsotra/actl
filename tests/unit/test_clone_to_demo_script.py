"""§28 P10 release-readiness correction: scripts/clone_to_demo.sh must
never touch a caller's real .env/secrets, must never collide with an
already-running local ACTL stack, and must never hang forever waiting on
a container that never becomes healthy. The three functions the script
extracts for exactly this purpose -- generate_reviewer_env,
derive_compose_project_name, wait_ready -- are sourced here and exercised
directly, without cloning a repo or touching real Docker: the script
guards its own `main` behind a `BASH_SOURCE == $0` check, so `source`-ing
it here only defines the functions.
"""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "clone_to_demo.sh"


def _run(
    function_call: str, *, cwd: Path, path_prepend: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env_path = f"{path_prepend}:$PATH" if path_prepend else "$PATH"
    command = f'source "{SCRIPT}"; PATH="{env_path}"; {function_call}'
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )


def test_generate_reviewer_env_forces_safe_values_and_never_invents_secrets(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "PAYMENT_PROVIDER=razorpay\n"
        "LLM_ENABLED=true\n"
        "RAZORPAY_KEY_ID=rzp_test_placeholder\n"
        "GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx\n"
    )
    target = tmp_path / ".env"

    result = _run(f'generate_reviewer_env "{example}" "{target}"', cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    content = target.read_text()
    assert "PAYMENT_PROVIDER=simulator" in content
    assert "LLM_ENABLED=false" in content
    # The example file's own (always-placeholder) values pass through
    # verbatim -- generate_reviewer_env never invents or fetches a real
    # credential, it only forces the two safety-relevant fields above.
    assert "RAZORPAY_KEY_ID=rzp_test_placeholder" in content
    assert "GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx" in content


def test_generate_reviewer_env_sets_restrictive_permissions(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("PAYMENT_PROVIDER=razorpay\nLLM_ENABLED=true\n")
    target = tmp_path / ".env"

    result = _run(f'generate_reviewer_env "{example}" "{target}"', cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected mode 600, got {oct(mode)}"


def test_derive_compose_project_name_is_unique_per_workdir_and_compose_safe() -> None:
    result_a = _run(
        'derive_compose_project_name "/tmp/actl-clone-to-demo.AbC123"', cwd=Path("/tmp")
    )
    result_b = _run(
        'derive_compose_project_name "/tmp/actl-clone-to-demo.XyZ789"', cwd=Path("/tmp")
    )

    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    name_a = result_a.stdout.strip()
    name_b = result_b.stdout.strip()

    # Compose project names must be lowercase alnum/-/_ starting with alnum.
    compose_safe = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    assert compose_safe.match(name_a), name_a
    assert compose_safe.match(name_b), name_b
    assert name_a != name_b, "two distinct workdirs must not derive the same Compose project name"
    assert "." not in name_a
    assert name_a == name_a.lower()


def test_wait_ready_times_out_with_diagnostics_instead_of_hanging(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then echo FAKE_DOCKER_COMPOSE_PS; exit 0; fi\n'
        'if [ "$1" = "compose" ] && [ "$2" = "logs" ]; then\n'
        "  echo FAKE_DOCKER_COMPOSE_LOGS; exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_docker.chmod(0o755)

    result = _run('wait_ready "postgres" 1 false', cwd=tmp_path, path_prepend=fake_bin)

    assert result.returncode != 0, "wait_ready must exit non-zero on timeout, never hang forever"
    assert "did not become ready within 1s" in result.stderr
    assert "docker compose ps" in result.stderr
    assert "docker compose logs" in result.stderr
    # wait_ready redirects the diagnostic docker calls' own stdout to
    # stderr too (>&2) -- these are diagnostics, not program output.
    assert "FAKE_DOCKER_COMPOSE_PS" in result.stderr
    assert "FAKE_DOCKER_COMPOSE_LOGS" in result.stderr


def test_wait_ready_succeeds_immediately_when_the_check_passes(tmp_path: Path) -> None:
    result = _run('wait_ready "postgres" 5 true', cwd=tmp_path)

    assert result.returncode == 0, result.stderr
