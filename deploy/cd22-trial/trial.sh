#!/usr/bin/env bash
# A second, throwaway instance for trying a CD22 mBER campaign on screen.
#
# It exists because the 8444 service serves PD-L1 on BindCraft from adopted
# state, restarting it needs sudo, and pointing it at CD22 would take PD-L1 off
# the air. This instance shares none of that: its own sqlite files, its own
# attempt directory, the CD22 input root, loopback only.
#
#   trial.sh start | stop | status
#
# It reads /etc/cellertz/coscientist.env for the model credentials and the
# prompt database, and overrides every path that file sets. It refuses to start
# if any overridden path is one the production service uses: two writers on one
# sqlite file take its .instance.lock and corrupt it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT="${CELLERTZ_TRIAL_ROOT:-/home/fabio/cellertz-ensaios/cd22-professor}"
STATE="$ROOT/trial-state"
API_PORT="${CELLERTZ_TRIAL_API_PORT:-8767}"
FRONT_PORT="${CELLERTZ_TRIAL_FRONT_PORT:-5174}"
PRODUCTION_ENV=/etc/cellertz/coscientist.env

load_production_env() {
  # systemd's EnvironmentFile is not shell syntax, so it is parsed, not sourced.
  local key value
  while IFS='=' read -r key value; do
    [[ -z "${key// }" || "$key" == \#* ]] && continue
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    export "$key=$value"
  done < "$PRODUCTION_ENV"
}

override() {
  local name="$1" value="$2"
  if grep -qE "^${name}=" "$PRODUCTION_ENV" && grep -qE "^[A-Z_]+=[\"']?${value}[\"']?$" "$PRODUCTION_ENV"; then
    echo "refusing: $name would be $value, which production uses" >&2
    exit 2
  fi
  export "$name=$value"
}

case "${1:-status}" in
  start)
    for port in "$API_PORT" "$FRONT_PORT"; do
      if ss -ltnH "( sport = :$port )" | grep -q .; then
        echo "port $port is already in use" >&2; exit 2
      fi
    done
    [[ -f "$ROOT/inputs/target.pdb" && -f "$ROOT/inputs/complex.pdb" ]] || {
      echo "the CD22 root needs target.pdb and complex.pdb in $ROOT/inputs" >&2; exit 2; }
    mkdir -p "$STATE"/{journey-attempts,catalog,memory,idempotency,runs,run-state}

    load_production_env
    for store in design-drafts design-journeys generation-plans campaigns coordinator-traces approved-runs \
                 scientific-authorizations scientific-runs; do
      case "$store" in
        design-drafts) name=CELLERTZ_DESIGN_DRAFT_DB ;;
        design-journeys) name=CELLERTZ_DESIGN_JOURNEY_DB ;;
        generation-plans) name=CELLERTZ_GENERATION_PLAN_DB ;;
        campaigns) name=CELLERTZ_CAMPAIGN_DB ;;
        coordinator-traces) name=CELLERTZ_COORDINATOR_TRACE_DB ;;
        approved-runs) name=CELLERTZ_APPROVED_RUN_DB ;;
        scientific-authorizations) name=CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB ;;
        scientific-runs) name=CELLERTZ_SCIENTIFIC_RUN_DB ;;
      esac
      override "$name" "$STATE/$store.sqlite"
    done
    override CELLERTZ_ATTEMPT_CATALOG "$STATE/catalog"
    override CELLERTZ_JOURNEY_ATTEMPT_ROOT "$STATE/journey-attempts"
    override CELLERTZ_PDL1_INPUT_ROOT "$ROOT/inputs"
    override CELLERTZ_SCIENTIFIC_MEMORY_ROOT "$STATE/memory"
    override CELLERTZ_IDEMPOTENCY_ROOT "$STATE/idempotency"
    override CELLERTZ_RUN_ARTIFACT_ROOT "$STATE/runs"
    override CELLERTZ_RUN_STATE_ROOT "$STATE/run-state"
    export CELLERTZ_SECOND_BRAIN_ROOT="$REPO/second_brain"
    export CELLERTZ_DEMONSTRATION_GENERATOR=mber
    export CELLERTZ_CONVERSATION_PORT="$API_PORT"
    export PYTHONPATH="$REPO/src"
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
    unset CELLERTZ_APPTAINER_DEMONSTRATION_ROOT

    # cwd is the state directory so nothing relative lands in the repository.
    # Backgrounded directly, not inside a subshell: $! has to be the process
    # itself, because stop signals its group and a wrapper's pid would end the
    # wrapper and leave the server running.
    cd "$STATE"
    setsid nohup "$REPO/.venv/bin/python" -m coscientist.conversation >"$STATE/api.log" 2>&1 &
    echo $! >"$STATE/api.pid"
    cd "$REPO/apps/protein-design-workspace"
    CELLERTZ_DEV_API_ORIGIN="http://127.0.0.1:$API_PORT" \
      setsid nohup npx vite --host 127.0.0.1 --port "$FRONT_PORT" --strictPort >"$STATE/front.log" 2>&1 &
    echo $! >"$STATE/front.pid"
    echo "started: api 127.0.0.1:$API_PORT, front http://127.0.0.1:$FRONT_PORT (logs in $STATE)"
    ;;
  stop)
    for name in front api; do
      if [[ -f "$STATE/$name.pid" ]]; then
        pid="$(cat "$STATE/$name.pid")"
        # setsid made the process its own group leader: end the whole group.
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        rm -f "$STATE/$name.pid"
        echo "stopped $name ($pid)"
      fi
    done
    ;;
  status)
    for name in api front; do
      if [[ -f "$STATE/$name.pid" ]] && kill -0 "$(cat "$STATE/$name.pid")" 2>/dev/null; then
        echo "$name running (pid $(cat "$STATE/$name.pid"))"
      else
        echo "$name not running"
      fi
    done
    ;;
  *) echo "usage: trial.sh start|stop|status" >&2; exit 2 ;;
esac
