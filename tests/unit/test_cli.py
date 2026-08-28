from pathlib import Path

import pytest

from actl.cli import _policy_check

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_policy_check_denies_over_cap_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = _policy_check(FIXTURES / "mandate_a.json", FIXTURES / "intent_over_cap.json")
    out = capsys.readouterr().out
    assert exit_code == 1
    assert out.startswith("DENY UNIT_CAP_EXCEEDED\n")
    assert 'rule cap.unit {"unit": 500000, "limit": 300000} -> fail' in out
    assert "12 rules evaluated, 1 failed, engine policy/1.0.0" in out
