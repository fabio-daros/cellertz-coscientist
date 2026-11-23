#!/usr/bin/env bash
# Promote this checkout to the service behind https://cellertz.fabiodaros.com:8444,
# switched to mBER and the CD22 input root.
#
#   promote.sh check              preflight and build only; changes nothing, needs no sudo
#   promote.sh apply              back up, switch, restart (asks for sudo, and for a typed yes)
#   promote.sh rollback <backup>  put the env file and the served front back, then restart
#
# What changes on the server, and nothing else:
#   - /etc/cellertz/coscientist.env: CELLERTZ_PDL1_INPUT_ROOT -> the CD22 root, and
#     CELLERTZ_DEMONSTRATION_GENERATOR=mber. Every other line is left as it is.
#   - the built front in /var/www/cellertz-coscientist (assets/ and index.html). The
#     static/ directory belongs to the Django admin and is never touched.
#   - the API service is restarted, which is what loads this week's code.
#
# What it does NOT change: the adopted sqlite state, the Postgres prompts, the
# Django admin service, nginx. The old PD-L1 requests stay saved and readable, but a
# Run on one of them is refused because its recorded inputs are no longer the live ones.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP="$REPO/apps/protein-design-workspace"
SERVICE=cellertz-coscientist-api.service
ENVFILE=/etc/cellertz/coscientist.env
WWW=/var/www/cellertz-coscientist
STATE_DIR=/mnt/hd10tb/cellertz-pdl1-demonstration-state
CD22_INPUTS=/home/fabio/cellertz-ensaios/cd22-professor/inputs
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/mnt/hd10tb/cellertz-promote-backup.$STAMP"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ABORTED: %s\n' "$*" >&2; exit 1; }

preflight() {
  say "preflight"
  [[ -z "$(git -C "$REPO" status --porcelain)" ]] || die "the working tree has uncommitted changes; commit them so the deployed code is a commit"
  for f in target.pdb complex.pdb preparation.json; do
    [[ -f "$CD22_INPUTS/$f" ]] || die "$CD22_INPUTS/$f is missing"
  done
  "$REPO/.venv/bin/python" - "$CD22_INPUTS/preparation.json" <<'PY' || die "preparation.json does not name target_id"
import json, sys
assert json.load(open(sys.argv[1])).get("target_id"), "no target_id"
PY
  echo "commit to deploy: $(git -C "$REPO" log --oneline -1)"

  say "no launch in flight"
  "$REPO/.venv/bin/python" - "$STATE_DIR/design-journeys.sqlite" <<'PY' || die "a launch is still running; wait for it to finish"
import json, sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
busy = []
for (raw,) in db.execute("select document from design_journeys"):
    doc = json.loads(raw)
    ex, post = doc.get("execution") or {}, doc.get("post_generation") or {}
    if (ex.get("consumed") and ex.get("terminal") is not True) or post.get("status") in ("queued", "running"):
        busy.append(doc.get("journey_id"))
if busy:
    print("in flight:", busy)
    sys.exit(1)
print("none in flight")
PY

  say "the GPU: only what is not ours may be on it"
  nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader || true

  say "build and checks"
  (cd "$APP" && npx tsc --noEmit && npx vite build --outDir "$BUILD/front" --emptyOutDir >/dev/null)
  grep -q "Run next round" "$BUILD"/front/assets/*.js || die "the build lacks the rounds panel"
  grep -q "Avoid the partner" "$BUILD"/front/assets/*.js || die "the build lacks the direction choice"
  "$REPO/.venv/bin/python" -m pytest -q --no-header \
    "$REPO/tests/test_design_journey.py" "$REPO/tests/test_journey_orchestration.py" \
    "$REPO/tests/test_registered_target.py" "$REPO/tests/test_partner_measurement.py" \
    "$REPO/tests/test_mber_demonstration.py" "$REPO/tests/test_request_draft_atlas_fields.py" >/dev/null \
    || die "the smoke tests fail"
  echo "build ok, smoke tests ok"
}

edited_env() {
  # Two lines change. The rest, credentials included, is copied through untouched.
  local out="$1"
  awk -v root="$CD22_INPUTS" '
    /^CELLERTZ_PDL1_INPUT_ROOT=/ { print "CELLERTZ_PDL1_INPUT_ROOT=" root; root_seen = 1; next }
    /^CELLERTZ_DEMONSTRATION_GENERATOR=/ { print "CELLERTZ_DEMONSTRATION_GENERATOR=mber"; gen_seen = 1; next }
    { print }
    END {
      if (!gen_seen) print "CELLERTZ_DEMONSTRATION_GENERATOR=mber"
      if (!root_seen) print "CELLERTZ_PDL1_INPUT_ROOT=" root
    }' "$ENVFILE" >"$out"
  local changed
  changed="$(diff <(sort "$ENVFILE") <(sort "$out") | grep -c '^[<>]' || true)"
  (( changed <= 4 )) || die "the edit would change $changed lines; expected at most 4"
}

case "${1:-}" in
  check)
    preflight
    say "the env edit that apply would make (credentials hidden)"
    edited_env "$BUILD/env.new"
    diff <(sort "$ENVFILE") <(sort "$BUILD/env.new") | grep -E '^[<>]' | sed -E 's/(KEY|PASSWORD|SECRET|TOKEN)=.*/\1=<hidden>/' || true
    echo; echo "check passed. Nothing was changed."
    ;;

  apply)
    preflight
    edited_env "$BUILD/env.new"
    say "this will"
    echo "  - stop the service, back everything up to $BACKUP, switch it to mBER + CD22, restart"
    echo "  - PD-L1 runs from the 8444 screen stop working until you roll back"
    read -r -p "type SUBSTITUIR to continue: " answer
    [[ "$answer" == "SUBSTITUIR" ]] || die "not confirmed"
    sudo -v

    say "stop the test instance, so two launchers never share the card"
    "$REPO/deploy/cd22-trial/trial.sh" stop || true

    say "back up"
    mkdir -p "$BACKUP"
    sudo cp -a "$ENVFILE" "$BACKUP/coscientist.env"
    sudo tar -C /var/www -czf "$BACKUP/www.tar.gz" cellertz-coscientist
    {
      echo "deployed_at=$STAMP"
      echo "deployed_commit=$(git -C "$REPO" rev-parse HEAD)"
      started="$(systemctl show "$SERVICE" -p ActiveEnterTimestamp --value)"
      echo "previous_service_start=$started"
      echo "previous_commit=$(git -C "$REPO" log -1 --format=%H --before="$started" 2>/dev/null || true)"
    } >"$BACKUP/meta.txt"
    sudo systemctl stop "$SERVICE"
    mkdir -p "$BACKUP/state"
    cp -a "$STATE_DIR"/*.sqlite* "$BACKUP/state/"
    echo "backup in $BACKUP"

    say "switch"
    sudo install -m 640 -o root -g fabio "$BUILD/env.new" "$ENVFILE"
    sudo rsync -a --delete "$BUILD/front/assets/" "$WWW/assets/"
    sudo rsync -a --exclude=/assets --exclude=/static "$BUILD/front/" "$WWW/"

    say "restart"
    sudo systemctl start "$SERVICE"
    for _ in $(seq 1 30); do
      curl -fsS -o /dev/null http://127.0.0.1:8766/api/design-draft && break
      sleep 1
    done
    curl -fsS -o /dev/null http://127.0.0.1:8766/api/design-draft || {
      echo "the service did not answer. Roll back with:" >&2
      echo "  $0 rollback $BACKUP" >&2
      exit 1
    }

    say "verify what the running process loaded"
    pid="$(systemctl show "$SERVICE" -p MainPID --value)"
    tr '\0' '\n' </proc/"$pid"/environ | grep -E '^CELLERTZ_(DEMONSTRATION_GENERATOR|PDL1_INPUT_ROOT)='
    systemctl is-active "$SERVICE"
    echo
    echo "done. Open https://cellertz.fabiodaros.com:8444 and reload the page (Ctrl+Shift+R)."
    echo "to go back:  $0 rollback $BACKUP"
    ;;

  rollback)
    backup="${2:-}"
    [[ -f "$backup/coscientist.env" && -f "$backup/www.tar.gz" ]] || die "usage: $0 rollback <backup dir>"
    sudo -v
    sudo systemctl stop "$SERVICE"
    sudo install -m 640 -o root -g fabio "$backup/coscientist.env" "$ENVFILE"
    sudo tar -C /var/www -xzf "$backup/www.tar.gz"
    sudo systemctl start "$SERVICE"
    echo "env file and served front restored."
    echo "the CODE is not rolled back: the service runs whatever this checkout holds."
    echo "to run the previous code too:  git -C $REPO checkout \$(grep previous_commit $backup/meta.txt | cut -d= -f2)"
    echo "the sqlite state was not restored. A copy from before the switch is in $backup/state"
    ;;

  *) echo "usage: $0 check | apply | rollback <backup dir>" >&2; exit 2 ;;
esac
