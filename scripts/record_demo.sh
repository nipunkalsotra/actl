#!/usr/bin/env bash
# §28 P9: records a full terminal transcript of the four-minute demo
# script (scripts/demo.sh) using `script`(1) -- the standard util-linux
# terminal-session recorder already on the dev/CI image, not a new
# dependency (Debian/Arch/most Linux distros ship it; on macOS/BSD the
# argument order differs -- `script -q <file> ./scripts/demo.sh` there).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

OUT="${1:-demo_recording.txt}"
script -q -c "./scripts/demo.sh" "${OUT}"
echo "recorded to ${OUT}"
