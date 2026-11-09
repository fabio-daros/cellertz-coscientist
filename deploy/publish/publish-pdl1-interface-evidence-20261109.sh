#!/bin/bash
set -eu
umask 077

# Publishes two Second Brain notes that give the Hotspot Identification Agent
# its first admissible PDL1 evidence, plus the suggester module that can now
# report it: the specialist reply was truncated at 400 output tokens, which a
# hotspot list does not fit. No frontend and no plan is changed. The API is
# restarted because FileKnowledgeReader indexes the vault once in __init__ and
# the suggester is cached on the supervisor.

dev=/home/fabio/projects/cellertz-coscientist
install_root=/mnt/hd10tb/cellertz-apptainer-demonstration
vault="$install_root/second_brain"
state=/mnt/hd10tb/cellertz-pdl1-demonstration-state
attempt="$state/journey-attempts/attempt.19d10373"
api=http://127.0.0.1:8765
request_id=request_b0ad05e6273e412aa613d6c314e2440f
plan_id=plan_c20d1bd58ead4c998ec406d428a8f5a0
digest=61382a60b2aef673372ec2de0091aca10899436a221729c983a8afc9ae152942

evidence=second_brain/evidence/pdl1_pd1_interface_4zqk.md
dataset=second_brain/datasets/pdb_4zqk.md
module=src/cellertz/application/plan_configuration_suggestion.py
install_module="$install_root/$module"

backup=$(mktemp -d /tmp/cellertz-pdl1-evidence.XXXXXXXX)
notes_copied=0
module_copied=0
restarted=0

fail() {
  echo "$1" >&2
  if [ "$notes_copied" = 1 ]; then
    rm -f "$vault/evidence/pdl1_pd1_interface_4zqk.md" || true
    rm -f "$vault/datasets/pdb_4zqk.md" || true
  fi
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

# The notes must already exist in the source tree and must not be published twice.
test -f "$dev/$evidence" || fail "nota_de_evidencia_ausente"
test -f "$dev/$dataset" || fail "nota_de_dataset_ausente"
test ! -e "$vault/evidence/pdl1_pd1_interface_4zqk.md" || fail "evidencia_ja_publicada"
test ! -e "$vault/datasets/pdb_4zqk.md" || fail "dataset_ja_publicado"

# The residue numbering claim is what makes the evidence admissible.
grep -F -q 'selected structure chain A residue 56' "$dev/$evidence" || fail "numeracao_nao_declarada"
grep -F -q 'd3c95434dcadf26d005340b15bd92be61e101ed921478c26f2a5550f198e61f6' "$dev/$evidence" || fail "estrutura_registrada_ausente"
grep -F -q 'Dataset: [[pdb_4zqk]]' "$dev/$evidence" || fail "dataset_nao_vinculado"
grep -i -q 'uniprot' "$dev/$evidence" && fail "numeracao_externa_na_nota"

# The module must carry the larger reply budget and the focused-selection rule,
# or the specialist reply is truncated and no hotspot survives the parse.
test -f "$dev/$module" || fail "modulo_ausente"
test -f "$install_module" || fail "modulo_instalado_ausente"
grep -F -q 'SUGGESTION_MAX_OUTPUT_TOKENS = 1200' "$dev/$module" || fail "teto_de_saida_nao_ajustado"
grep -F -q 'prefer at most five residues' "$dev/$module" || fail "selecao_focada_ausente"

# The registered structure must still be the one the notes were computed against.
test "$(sha256sum /mnt/hd10tb/cellertz-pdl1-demonstration-inputs/target.pdb | awk '{print $1}')" \
  = d3c95434dcadf26d005340b15bd92be61e101ed921478c26f2a5550f198e61f6 || fail "estrutura_registrada_mudou"

before_plans=$(sha256sum "$state/generation-plans.sqlite" | awk '{print $1}')
before_drafts=$(sha256sum "$state/design-drafts.sqlite" | awk '{print $1}')
before_journeys=$(sha256sum "$state/design-journeys.sqlite" | awk '{print $1}')
before_suggestions=$(sha256sum "$state/design-plan-suggestions.sqlite" | awk '{print $1}')
before_progress=$(sha256sum "$attempt/progress.json" | awk '{print $1}')
before_review=$(sha256sum "$attempt/review.json" | awk '{print $1}')

cp -a "$vault" "$backup/second_brain-before"
cp -a "$install_module" "$backup/plan_configuration_suggestion.py"

install -o fabio -g fabio -m 0644 "$dev/$evidence" "$vault/evidence/pdl1_pd1_interface_4zqk.md"
install -o fabio -g fabio -m 0644 "$dev/$dataset" "$vault/datasets/pdb_4zqk.md"
notes_copied=1

install -o fabio -g fabio -m 0644 "$dev/$module" "$install_module"
module_copied=1
python3 -m py_compile "$install_module" || fail "sintaxe_invalida"
grep -F -q 'SUGGESTION_MAX_OUTPUT_TOKENS = 1200' "$install_module" || fail "modulo_nao_publicado"

# The published vault must parse and stay healthy before the service is restarted.
(cd "$install_root" && python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from cellertz.knowledge.health import inspect_vault
from cellertz.knowledge.reader import FileKnowledgeReader
health = inspect_vault(Path('second_brain'))
items = {i.id for i in FileKnowledgeReader(Path('second_brain')).list(domain=None)}
if not health.ok or health.broken_links:
    raise SystemExit('vault unhealthy: ' + str(health.issues))
if 'pdl1_pd1_interface_4zqk' not in items or 'pdb_4zqk' not in items:
    raise SystemExit('notes not readable')
print('vault nodes:', health.nodes)
") || fail "vault_publicado_invalido"

if active; then
  fail "geracao_em_andamento"
fi
sudo systemctl restart cellertz-api.service || fail "reinicio_falhou"
restarted=1

# The plan the professor already approved must survive the restart untouched.
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

echo "backup_vault=$backup/second_brain-before"
echo "backup_modulo=$backup/plan_configuration_suggestion.py"
echo "publicacao=second_brain+plan_configuration_suggestion"
echo "notas=pdl1_pd1_interface_4zqk,pdb_4zqk"
echo "teto_de_saida=1200"
echo "modulo_sha=$(sha256sum "$install_module" | awk '{print $1}')"
echo "plano_preservado=$plan_id"
echo "digest_preservado=$digest"
echo "A API foi reiniciada somente depois da verificacao de trabalhos ativos."
echo "Nenhum modelo foi chamado."
echo "A geracao nao foi iniciada."
