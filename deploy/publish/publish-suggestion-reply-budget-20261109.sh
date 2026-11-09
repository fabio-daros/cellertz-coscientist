#!/bin/bash
set -eu
umask 077

# The PDL1 evidence notes are already published. This publishes only the
# suggester module, so the Hotspot Identification Agent can actually report
# what the notes now support: its reply was capped at 400 output tokens, which
# a hotspot list does not fit, and a truncated reply fails the JSON parse and
# yields no suggestion. Raises the cap to 1200 and asks the agent for a focused
# residue set. No note, no frontend and no plan is changed.

dev=/home/fabio/projects/cellertz-coscientist
install_root=/mnt/hd10tb/cellertz-apptainer-demonstration
state=/mnt/hd10tb/cellertz-pdl1-demonstration-state
attempt="$state/journey-attempts/attempt.19d10373"
api=http://127.0.0.1:8765
request_id=request_b0ad05e6273e412aa613d6c314e2440f
plan_id=plan_c20d1bd58ead4c998ec406d428a8f5a0
digest=61382a60b2aef673372ec2de0091aca10899436a221729c983a8afc9ae152942

module=src/cellertz/application/plan_configuration_suggestion.py
install_module="$install_root/$module"

backup=$(mktemp -d /tmp/cellertz-reply-budget.XXXXXXXX)
module_copied=0
restarted=0

fail() {
  echo "$1" >&2
  if [ "$module_copied" = 1 ] && [ -f "$backup/plan_configuration_suggestion.py" ]; then
    install -o fabio -g fabio -m 0644 \
      "$backup/plan_configuration_suggestion.py" "$install_module" || true
  fi
  if [ "$restarted" = 1 ]; then
    sudo systemctl restart cellertz-api.service || true
  fi
  exit 1
}

active() {
  pgrep -u fabio -f 'bindcraft.py|apptainer exec|apptainer_demonstration --supervise' >/dev/null 2>&1
}

if active; then
  echo "geracao_em_andamento" >&2
  exit 1
fi

test -f "$dev/$module" || fail "modulo_ausente"
test -f "$install_module" || fail "modulo_instalado_ausente"
grep -F -q 'SUGGESTION_MAX_OUTPUT_TOKENS = 1200' "$dev/$module" || fail "teto_de_saida_nao_ajustado"
grep -F -q 'prefer at most five residues' "$dev/$module" || fail "selecao_focada_ausente"

# This publish only makes sense once the evidence it reports is in place.
vault="$install_root/second_brain"
test -f "$vault/evidence/pdl1_pd1_interface_4zqk.md" || fail "evidencia_nao_publicada"
test -f "$vault/datasets/pdb_4zqk.md" || fail "dataset_nao_publicado"

before_plans=$(sha256sum "$state/generation-plans.sqlite" | awk '{print $1}')
before_drafts=$(sha256sum "$state/design-drafts.sqlite" | awk '{print $1}')
before_journeys=$(sha256sum "$state/design-journeys.sqlite" | awk '{print $1}')
before_suggestions=$(sha256sum "$state/design-plan-suggestions.sqlite" | awk '{print $1}')
before_progress=$(sha256sum "$attempt/progress.json" | awk '{print $1}')
before_review=$(sha256sum "$attempt/review.json" | awk '{print $1}')

cp -a "$install_module" "$backup/plan_configuration_suggestion.py"
install -o fabio -g fabio -m 0644 "$dev/$module" "$install_module"
module_copied=1
python3 -m py_compile "$install_module" || fail "sintaxe_invalida"
grep -F -q 'SUGGESTION_MAX_OUTPUT_TOKENS = 1200' "$install_module" || fail "modulo_nao_publicado"
grep -F -q 'prefer at most five residues' "$install_module" || fail "modulo_nao_publicado"

# The vault must still parse with the published module in place.
(cd "$install_root" && python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from cellertz.application.plan_configuration_suggestion import SUGGESTION_MAX_OUTPUT_TOKENS
from cellertz.knowledge.health import inspect_vault
health = inspect_vault(Path('second_brain'))
if not health.ok or health.broken_links:
    raise SystemExit('vault unhealthy: ' + str(health.issues))
if SUGGESTION_MAX_OUTPUT_TOKENS != 1200:
    raise SystemExit('cap not loaded')
print('vault nodes:', health.nodes, '| reply cap:', SUGGESTION_MAX_OUTPUT_TOKENS)
") || fail "modulo_publicado_invalido"

if active; then
  fail "geracao_em_andamento"
fi
sudo systemctl restart cellertz-api.service || fail "reinicio_falhou"
restarted=1

python3 - "$api" "$request_id" "$plan_id" "$digest" << 'PY' || fail "plano_alterado"
import json, sys, time, urllib.error, urllib.request
api, request_id, plan_id, digest = sys.argv[1:5]
url = f"{api}/api/generation-plan?request_id={request_id}"
deadline = time.monotonic() + 40
while True:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
        break
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        if time.monotonic() >= deadline:
            raise SystemExit(1)
        time.sleep(1)
plans = [item for item in payload.get("plans") or [] if item.get("plan_id") == plan_id]
if len(plans) != 1:
    raise SystemExit(1)
current = plans[0]
if current.get("revision") != 3 or current.get("digest") != digest:
    raise SystemExit(1)
if current.get("approved") is not True or current.get("execution_authorized") is not False:
    raise SystemExit(1)
PY

after_plans=$(sha256sum "$state/generation-plans.sqlite" | awk '{print $1}')
after_drafts=$(sha256sum "$state/design-drafts.sqlite" | awk '{print $1}')
after_journeys=$(sha256sum "$state/design-journeys.sqlite" | awk '{print $1}')
after_suggestions=$(sha256sum "$state/design-plan-suggestions.sqlite" | awk '{print $1}')
after_progress=$(sha256sum "$attempt/progress.json" | awk '{print $1}')
after_review=$(sha256sum "$attempt/review.json" | awk '{print $1}')
test "$before_plans" = "$after_plans" || fail "planos_alterados"
test "$before_drafts" = "$after_drafts" || fail "pedidos_alterados"
test "$before_journeys" = "$after_journeys" || fail "jornadas_alteradas"
test "$before_suggestions" = "$after_suggestions" || fail "sugestoes_reescritas"
test "$before_progress" = "$after_progress" || fail "tentativa_alterada"
test "$before_review" = "$after_review" || fail "revisao_da_tentativa_alterada"

echo "backup_modulo=$backup/plan_configuration_suggestion.py"
echo "publicacao=plan_configuration_suggestion"
echo "teto_de_saida=1200"
echo "modulo_sha=$(sha256sum "$install_module" | awk '{print $1}')"
echo "plano_preservado=$plan_id"
echo "digest_preservado=$digest"
echo "A API foi reiniciada somente depois da verificacao de trabalhos ativos."
echo "Nenhum modelo foi chamado."
echo "A geracao nao foi iniciada."
