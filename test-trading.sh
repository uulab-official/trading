#!/bin/bash
# One-command offline paper trial. The bundled default fixture is synthetic.
set -eu

usage() {
    cat <<'HELP'
Usage: ./test-trading.sh [--fixture PATH] [--checkpoint PATH] [paper CLI options]

Offline paper replay only. The bundled default is a synthetic 90-candle example,
not a market-connected service or evidence of real trading performance.

Defaults (relative to the repository):
  fixture:    config/examples/offline_fixture.json
  checkpoint: data/owner-trial/checkpoint.json

Supplied relative paths resolve from your current directory. Rerun the same
command to validate/resume; use a new checkpoint for a different experiment.
Requires Python 3.10+, PyYAML 6+, and local POSIX checkpoint locking.
HELP
}

caller_dir="$PWD"
repo_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fixture="$repo_dir/config/examples/offline_fixture.json"
checkpoint="$repo_dir/data/owner-trial/checkpoint.json"
options=()
set_path() {
    option="$1"
    supplied="$2"
    if [[ -z "$supplied" ]]; then
        printf '%s requires a path. See --help.\n' "$option" >&2
        exit 2
    fi
    if [[ "$supplied" != /* ]]; then
        supplied="$caller_dir/$supplied"
    fi
    if [[ "$option" == --fixture ]]; then
        fixture="$supplied"
    else
        checkpoint="$supplied"
    fi
}
while (($#)); do
    case "$1" in
        --help|-h)
            usage
            exit 0
            ;;
        --fixture|--checkpoint)
            option="$1"
            if (($# < 2)) || [[ "$2" == --* ]]; then
                printf '%s requires a path. See --help.\n' "$option" >&2
                exit 2
            fi
            set_path "$option" "$2"
            shift 2
            ;;
        --fixture=*|--checkpoint=*)
            set_path "${1%%=*}" "${1#*=}"
            shift
            ;;
        *)
            options+=("$1")
            shift
            ;;
    esac
done

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10+ is required. Prepare that runtime, then retry.' >&2
    exit 2
fi
export PYTHONPATH="$repo_dir/packages/core:$repo_dir/packages/data:$repo_dir/packages/strategy:$repo_dir/apps/server${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<'PYCODE'
import sys
if sys.version_info < (3, 10):
    print("Python 3.10+ is required. Prepare that runtime, then retry.", file=sys.stderr)
    raise SystemExit(2)
try:
    import yaml
    supported = int(yaml.__version__.split(".")[0]) >= 6
except (ImportError, AttributeError, ValueError):
    supported = False
if not supported:
    print("PyYAML 6+ is required. In your virtual environment, install requirements-offline.txt, then retry.", file=sys.stderr)
    raise SystemExit(2)
PYCODE

printf '%s\n' 'Offline paper replay: the bundled default fixture is synthetic; supplied files are not a live market feed.' >&2
cd -- "$repo_dir"
exec python3 -m trading_bot.paper_runner "$fixture" --checkpoint "$checkpoint" "${options[@]}"
