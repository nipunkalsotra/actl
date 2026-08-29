#!/usr/bin/env bash
# §20.1 the four-minute demo script, for live narration/recording
# (§28 P9 instruction 7's "recording script" -- wrapped by `make record`
# via scripts/record_demo.sh). Runs the six real §20.1 commands in
# order: the five named scenarios through the real `actl demo` CLI,
# then verify-chain as the sixth, closing command. Never calls Razorpay
# or Groq: LLM_ENABLED/PAYMENT_PROVIDER are forced offline here
# regardless of what .env otherwise says.
#
# Prerequisite: a freshly migrated database (`make up && make migrate`
# against a clean volume, or `docker compose down -v` first if you've
# run this before) -- `application.demo.run_scenario` seeds each
# scenario's mandate/quote/order with IDs deterministically derived from
# the scenario name, so a second run against a database that still has
# the first run's rows will collide on those same ids (see
# src/actl/application/demo.py's own module docstring). This is the
# live/human-facing counterpart to `make demo`, which validates all six
# items' traces (the five scenarios plus verify_chain) against committed
# golden fixtures inside their own fresh, isolated, disposable Postgres
# every time -- always safely re-runnable, unlike this script.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export LLM_ENABLED=false
export PAYMENT_PROVIDER=simulator

for scenario in happy_path over_cap stale_price declined llm_down; do
  echo "=== actl demo --scenario ${scenario} ==="
  uv run python -m actl.cli demo --scenario "${scenario}"
  echo
done

echo "=== actl verify-chain (the sixth, closing §20.1 command) ==="
uv run python -m actl.cli verify-chain --from 1 \
  --to "$(uv run python -m actl.cli chain-head | grep -oE 'seq=[0-9]+' | cut -d= -f2)"

echo
echo "6 scenarios completed"
