"""§28 P9 production-readiness correction: the registered §20.1 demo item
set must be exactly what §20.1 documents -- no drift, no typo, no
accidental fifth/seventh name. §20.1's own text: "Recording those six
commands is the backbone of the pitch video" -- five `actl demo
--scenario <name>` invocations (`happy_path`, `over_cap`, `stale_price`,
`declined`, `llm_down`) plus a sixth, differently-shaped closing command,
`actl verify-chain --from 1 --to 80`. `DEMO_ITEMS` formalises that sixth
command as `verify_chain`, a full registered item alongside the five
scenarios -- printed, golden-traced, and offline-verified with the same
parity (docs/adr/0010-p9-failure-theatre-decisions.md decision 20).
`SCENARIOS` (the five names) remains the only valid `--scenario` CLI
value set unchanged -- §20.1 never shows `actl demo --scenario
verify_chain`, only the separate top-level `actl verify-chain` command.
"""

from __future__ import annotations

import pytest

from actl.application.demo import DEMO_ITEMS, SCENARIOS, VERIFY_CHAIN_ITEM
from actl.cli import build_parser


def test_scenario_names_match_section_20_1_exactly() -> None:
    assert SCENARIOS == ("happy_path", "over_cap", "stale_price", "declined", "llm_down")


def test_registered_demo_item_set_is_exactly_the_six_section_20_1_commands() -> None:
    assert DEMO_ITEMS == (
        "happy_path",
        "over_cap",
        "stale_price",
        "declined",
        "llm_down",
        "verify_chain",
    )
    assert len(DEMO_ITEMS) == 6
    assert VERIFY_CHAIN_ITEM == "verify_chain"
    assert (*SCENARIOS, VERIFY_CHAIN_ITEM) == DEMO_ITEMS


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_cli_accepts_every_registered_scenario_name(scenario: str) -> None:
    args = build_parser().parse_args(["demo", "--scenario", scenario])
    assert args.scenario == scenario


def test_cli_rejects_a_scenario_name_outside_the_registered_set() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["demo", "--scenario", "not_a_real_scenario"])


def test_cli_does_not_accept_verify_chain_as_a_scenario_value() -> None:
    """`verify_chain` is a full `DEMO_ITEMS` member for `make demo`/`make
    verify` purposes, but §20.1 never shows `actl demo --scenario
    verify_chain` -- the sixth command is the separate, top-level `actl
    verify-chain`, already its own subcommand."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["demo", "--scenario", VERIFY_CHAIN_ITEM])
