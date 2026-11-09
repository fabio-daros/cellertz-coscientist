"""Protein Design Supervisor v1.

One-round orchestrator. Delegates to specialists and never
recomputes ranking_score.

Scoring uses fixture structural inputs so the loop can run
without BindCraft, RFdiffusion, mBER, or Boltz GPU.
Those metrics are not a scientific baseline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from cellertz.domains.protein_design.agents.critic import (
    ProteinCriticAgentV1,
    ProteinCriticRecord,
)
from cellertz.domains.protein_design.agents.critic_explanation import (
    CriticExplainer,
    GroundedCriticExplanation,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    GenerationResult,
    NormalizedCandidate,
    ProteinGenerationAgent,
    request_input_hash,
)
from cellertz.domains.protein_design.agents.prioritization import (
    PortfolioPolicy,
    ProteinPortfolioRecord,
    ProteinPrioritizationAgentV1,
)
from cellertz.domains.protein_design.agents.grounded_critic import (
    GroundedCriticContextProvider,
    GroundedCriticResult,
    attach_grounded_context,
    unavailable_context,
)
from cellertz.domains.protein_design.agents.ranking import (
    ProteinRankingAgentV1,
    ProteinRankingRecord,
)
from cellertz.schemas import ProteinCandidate
from cellertz.state.run_state import (
    FileProteinRunStateStore,
    ProteinRunState,
    ProteinRunStageState,
    ProteinRunStateWriter,
    default_run_state_root,
)
from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2EnsembleSummary,
    Boltz2Sample,
    summarize_boltz2_ensemble,
)
from cellertz.tools.protein_scoring.scoring_v2 import (
    ProteinScoreResultV2,
    ProteinScoringToolV2,
)


StageName = Literal[
    "generation",
    "scoring",
    "ranking",
    "critique",
    "prioritization",
    "complete",
    "failed",
]
StageStatus = Literal[
    "pending",
    "running",
    "complete",
    "failed",
]
STAGE_ORDER: tuple[StageName, ...] = (
    "generation",
    "scoring",
    "ranking",
    "critique",
    "prioritization",
)

GENERATION_VERSION = "protein_generation_agent_v1"
SCORING_VERSION = "protein_scoring_tool_v2_fixture_metrics_v1"
RANKING_VERSION = "protein_ranking_agent_v1"
CRITIQUE_VERSION = "protein_critic_agent_v1"
PRIORITIZATION_VERSION = "protein_prioritization_agent_v1"


def supported_professor_boundary() -> dict[str, object]:
    """Report which supervisor stages can run. This does not execute one."""

    return {
        "supervisor": "ProteinSupervisorV1",
        "invoked": "boundary",
        "run_called": False,
        "stages": {
            "generation": {
                "version": GENERATION_VERSION,
                "implementation_kind": "adapter",
                "supported_now": False,
                "reason": (
                    "ProteinGenerationAgent.generate calls a generator adapter. "
                    "GPU execution is disabled, so BindCraft was not called."
                ),
            },
            "scoring": {
                "version": SCORING_VERSION,
                "implementation_kind": "fixture",
                "supported_now": False,
                "reason": (
                    "The default supervisor scorer uses fixture structural inputs. "
                    "Those metrics are not a scientific baseline and are not used here."
                ),
            },
            "ranking": {
                "version": RANKING_VERSION,
                "implementation_kind": "deterministic",
                "supported_now": False,
                "reason": (
                    "Ranking sorts raw ipSAE. No ipSAE evidence exists. "
                    "BindCraft i_pTM is not a substitute."
                ),
            },
            "critique": {
                "version": CRITIQUE_VERSION,
                "implementation_kind": "deterministic",
                "supported_now": False,
                "reason": "Critique annotates ranked candidates. No ranking record exists.",
            },
            "prioritization": {
                "version": PRIORITIZATION_VERSION,
                "implementation_kind": "deterministic",
                "supported_now": False,
                "reason": (
                    "Prioritization selects from criticized candidates. No critic record exists."
                ),
            },
        },
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _unit_interval(text: str) -> float:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


@dataclass
class StageRecord:
    status: StageStatus = "pending"
    artifact: str | None = None
    input_hash: str | None = None
    version: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    missing_ipsae: int | None = None
    missing_boltz2: int | None = None
    missing_ipsae_ranked_last: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ProteinDesignRun:
    run_id: str
    target: str
    request: GenerationRequest
    round: int = 1
    stage: StageName = "generation"
    generation: StageRecord = field(
        default_factory=StageRecord
    )
    scoring: StageRecord = field(default_factory=StageRecord)
    ranking: StageRecord = field(default_factory=StageRecord)
    critique: StageRecord = field(default_factory=StageRecord)
    prioritization: StageRecord = field(
        default_factory=StageRecord
    )
    critique_grounded_context: dict[str, object] | None = None
    critique_explanation: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        critique = self.critique.to_dict()
        if self.critique_grounded_context is not None:
            critique["grounded_context"] = self.critique_grounded_context
        if self.critique_explanation is not None:
            critique["explanation"] = self.critique_explanation
        return {
            "run_id": self.run_id,
            "round": self.round,
            "target": self.target,
            "stage": self.stage,
            "generation": self.generation.to_dict(),
            "scoring": self.scoring.to_dict(),
            "ranking": self.ranking.to_dict(),
            "critique": critique,
            "prioritization": self.prioritization.to_dict(),
        }


def fixture_protein_and_ensemble(
    candidate: NormalizedCandidate,
) -> tuple[ProteinCandidate, Boltz2EnsembleSummary]:
    ipsae = 0.20 + 0.70 * _unit_interval(
        f"{candidate.candidate_id}:ipsae"
    )
    iptm = 0.20 + 0.70 * _unit_interval(
        f"{candidate.candidate_id}:iptm"
    )
    iptm_1 = min(1.0, iptm + 0.01)
    protein = ProteinCandidate(
        id=candidate.candidate_id,
        sequence=candidate.sequence,
        design_type="protein",
        source=candidate.generator,
        target=candidate.target_id,
        metadata={
            "generator": candidate.generator,
            "fixture_metrics": True,
        },
        interface_metrics={
            "ipsae": ipsae,
            "iptm": iptm,
        },
    )
    ensemble = summarize_boltz2_ensemble(
        [
            Boltz2Sample(
                sample_id=f"{candidate.candidate_id}_model_0",
                iptm=iptm,
                model_index=0,
            ),
            Boltz2Sample(
                sample_id=f"{candidate.candidate_id}_model_1",
                iptm=iptm_1,
                model_index=1,
            ),
        ]
    )
    return protein, ensemble


def _score_to_dict(
    result: ProteinScoreResultV2,
) -> dict[str, object]:
    return {
        "candidate_id": result.candidate_id,
        "ranking_score": result.ranking_score,
        "ranking_method": result.ranking_method,
        "is_probability": result.is_probability,
        "recommended_as_hard_filter": (
            result.recommended_as_hard_filter
        ),
        "structural_evidence": dict(
            result.structural_evidence
        ),
        "additional_evidence": dict(
            result.additional_evidence
        ),
        "provenance": dict(result.provenance),
    }


class ProteinSupervisorV1:
    """Advance one pending stage at a time. No LLM policy."""

    def __init__(
        self,
        request: GenerationRequest,
        artifact_dir: str | Path,
        run_id: str | None = None,
        generation_agent: ProteinGenerationAgent | None = None,
        scorer: ProteinScoringToolV2 | None = None,
        ranking_agent: ProteinRankingAgentV1 | None = None,
        critic_agent: ProteinCriticAgentV1 | None = None,
        prioritization_agent: (
            ProteinPrioritizationAgentV1 | None
        ) = None,
        policy: PortfolioPolicy | None = None,
        score_fn: Callable[
            [tuple[NormalizedCandidate, ...]],
            list[ProteinScoreResultV2],
        ]
        | None = None,
        run_state_store: ProteinRunStateWriter | None = None,
        context_provider: GroundedCriticContextProvider | None = None,
        explainer: CriticExplainer | None = None,
    ) -> None:
        self.request = request
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.generation_agent = (
            generation_agent or ProteinGenerationAgent()
        )
        self.scorer = scorer or ProteinScoringToolV2()
        self.ranking_agent = (
            ranking_agent or ProteinRankingAgentV1()
        )
        self.critic_agent = critic_agent or ProteinCriticAgentV1()
        self.prioritization_agent = (
            prioritization_agent
            or ProteinPrioritizationAgentV1(policy)
        )
        self.score_fn = score_fn
        self.context_provider = context_provider
        self.explainer = explainer
        self.run_state_store = run_state_store or FileProteinRunStateStore(
            default_run_state_root()
        )
        self.design_run = ProteinDesignRun(
            run_id=run_id
            or f"protein_run_{request_input_hash(request)[:8]}",
            target=request.target_id,
            request=request,
        )
        self.generation_result: GenerationResult | None = None
        self.score_results: tuple[ProteinScoreResultV2, ...] = ()
        self.ranking_records: tuple[ProteinRankingRecord, ...] = ()
        self.critic_records: tuple[ProteinCriticRecord, ...] = ()
        self.grounded_results: tuple[GroundedCriticResult, ...] = ()
        self.explanations: tuple[GroundedCriticExplanation, ...] = ()
        self.portfolio_records: tuple[
            ProteinPortfolioRecord, ...
        ] = ()
        self._write_run_state()

    def next_stage(self) -> StageName | None:
        for name in STAGE_ORDER:
            record = getattr(self.design_run, name)
            if record.status != "complete":
                return name
        return None

    def run(self) -> ProteinDesignRun:
        while True:
            due = self.next_stage()

            if due is None:
                self._refresh_explanation()
                self.design_run.stage = "complete"
                self._write_run_state()
                return self.design_run

            self._execute(due)

            stage = getattr(self.design_run, due)
            if stage.status == "failed":
                self.design_run.stage = "failed"
                self._write_run_state()
                return self.design_run

    def _execute(self, name: StageName) -> None:
        if name == "generation":
            self._run_generation()
        elif name == "scoring":
            self._run_scoring()
        elif name == "ranking":
            self._run_ranking()
        elif name == "critique":
            self._run_critique()
        elif name == "prioritization":
            self._run_prioritization()

    def _begin(
        self,
        name: StageName,
        record: StageRecord,
        input_hash: str,
        version: str,
    ) -> bool:
        if (
            record.status == "complete"
            and record.input_hash == input_hash
        ):
            return False

        self.design_run.stage = name
        record.status = "running"
        record.input_hash = input_hash
        record.version = version
        record.error = None
        if record.started_at is None:
            record.started_at = _now()
        return True

    def _complete(
        self,
        record: StageRecord,
        artifact: Path,
        extra: dict[str, int] | None = None,
    ) -> None:
        record.status = "complete"
        record.artifact = artifact.as_posix()
        record.completed_at = _now()
        record.error = None
        extra = extra or {}
        if "missing_ipsae" in extra:
            record.missing_ipsae = extra["missing_ipsae"]
        if "missing_boltz2" in extra:
            record.missing_boltz2 = extra["missing_boltz2"]
        if "missing_ipsae_ranked_last" in extra:
            record.missing_ipsae_ranked_last = extra[
                "missing_ipsae_ranked_last"
            ]
        self._write_run_state()

    def _fail(self, record: StageRecord, error: str) -> None:
        record.status = "failed"
        record.error = error
        record.completed_at = _now()
        self._write_run_state()

    def _artifact(self, name: str) -> Path:
        return self.artifact_dir / f"{name}.json"

    def _write_json(self, name: str, payload: object) -> Path:
        path = self._artifact(name)
        path.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        return path

    def _operational_run_state(self) -> ProteinRunState:
        run = self.design_run

        def _stage(record: StageRecord) -> ProteinRunStageState:
            return ProteinRunStageState(
                status=record.status,
                missing_ipsae=record.missing_ipsae,
                error=record.error,
            )

        critique = ProteinRunStageState(
            status=run.critique.status,
            missing_ipsae=run.critique.missing_ipsae,
            error=run.critique.error,
            grounded_context=run.critique_grounded_context,
            explanation=run.critique_explanation,
        )
        return ProteinRunState(
            run_id=run.run_id,
            found=True,
            generation=_stage(run.generation),
            scoring=_stage(run.scoring),
            ranking=_stage(run.ranking),
            critique=critique,
            prioritization=_stage(run.prioritization),
        )

    def _write_run_state(self) -> None:
        (self.artifact_dir / "run.json").write_text(
            json.dumps(
                self.design_run.to_dict(),
                indent=2,
                sort_keys=True,
            )
        )
        self.run_state_store.put_run_state(self._operational_run_state())

    def _run_generation(self) -> None:
        record = self.design_run.generation
        input_hash = request_input_hash(self.request)

        if not self._begin(
            "generation",
            record,
            input_hash,
            GENERATION_VERSION,
        ):
            return

        result = self.generation_agent.generate(self.request)
        self.generation_result = result

        if result.status != "complete":
            self._fail(
                record,
                result.error or "Generation failed.",
            )
            return

        path = self._write_json(
            "generation",
            result.to_dict(),
        )
        self._complete(record, path)

    def _run_scoring(self) -> None:
        record = self.design_run.scoring
        generated = self.generation_result

        if generated is None or generated.status != "complete":
            self._fail(
                record,
                "Scoring requires complete generation.",
            )
            return

        input_hash = _hash_payload(
            {
                "generation": generated.input_hash,
                "version": SCORING_VERSION,
            }
        )

        if not self._begin(
            "scoring",
            record,
            input_hash,
            SCORING_VERSION,
        ):
            return

        try:
            if self.score_fn is not None:
                scored = self.score_fn(generated.candidates)
            else:
                scored = [
                    self.scorer.score(
                        *fixture_protein_and_ensemble(
                            candidate
                        )
                    )
                    for candidate in generated.candidates
                ]
        except Exception as error:
            self._fail(record, str(error))
            return

        self.score_results = tuple(scored)
        missing_ipsae = sum(
            1
            for item in scored
            if item.ranking_score is None
        )
        missing_boltz2 = sum(
            1
            for item in scored
            if item.structural_evidence.get(
                "boltz2_iptm_mean"
            )
            is None
        )
        path = self._write_json(
            "scoring",
            [_score_to_dict(item) for item in scored],
        )
        self._complete(
            record,
            path,
            {
                "missing_ipsae": missing_ipsae,
                "missing_boltz2": missing_boltz2,
            },
        )

    def _run_ranking(self) -> None:
        record = self.design_run.ranking

        if not self.score_results:
            self._fail(
                record,
                "Ranking requires complete scoring.",
            )
            return

        input_hash = _hash_payload(
            {
                "scoring": self.design_run.scoring.input_hash,
                "version": RANKING_VERSION,
            }
        )

        if not self._begin(
            "ranking",
            record,
            input_hash,
            RANKING_VERSION,
        ):
            return

        ranked = self.ranking_agent.rank(list(self.score_results))
        self.ranking_records = tuple(ranked)
        missing_last = sum(
            1
            for item in ranked
            if item.ranking_score is None
        )
        path = self._write_json(
            "ranking",
            [item.to_dict() for item in ranked],
        )
        self._complete(
            record,
            path,
            {"missing_ipsae_ranked_last": missing_last},
        )

    def _run_critique(self) -> None:
        record = self.design_run.critique

        if not self.ranking_records:
            self._fail(
                record,
                "Critique requires complete ranking.",
            )
            return

        input_hash = _hash_payload(
            {
                "ranking": self.design_run.ranking.input_hash,
                "version": CRITIQUE_VERSION,
            }
        )

        if not self._begin(
            "critique",
            record,
            input_hash,
            CRITIQUE_VERSION,
        ):
            return

        reviewed = self.critic_agent.review(
            list(self.ranking_records)
        )
        self.critic_records = tuple(reviewed)
        context = self._grounded_context()
        self.grounded_results = tuple(
            attach_grounded_context(reviewed, context)
        )
        self.design_run.critique_grounded_context = {
            "status": context.status,
            "package_id": context.package_id,
        }
        if context.knowledge_context is not None:
            self.design_run.critique_grounded_context["knowledge_context"] = (
                context.knowledge_context
            )
        path = self._write_json(
            "critique",
            [item.to_dict() for item in reviewed],
        )
        if self.context_provider is not None:
            self._write_json(
                "grounded_critic",
                [item.to_dict() for item in self.grounded_results],
            )
        self._explain_grounded_results()
        self._complete(record, path)

    def _run_prioritization(self) -> None:
        record = self.design_run.prioritization

        if not self.critic_records:
            self._fail(
                record,
                "Prioritization requires complete critique.",
            )
            return

        input_hash = _hash_payload(
            {
                "critique": self.design_run.critique.input_hash,
                "version": PRIORITIZATION_VERSION,
            }
        )

        if not self._begin(
            "prioritization",
            record,
            input_hash,
            PRIORITIZATION_VERSION,
        ):
            return

        selected = self.prioritization_agent.select(
            list(self.critic_records)
        )
        self.portfolio_records = tuple(selected)
        path = self._write_json(
            "prioritization",
            [item.to_dict() for item in selected],
        )
        self._complete(record, path)

    def _grounded_context(self):
        if self.context_provider is None:
            return unavailable_context()
        try:
            return self.context_provider.for_domain(
                domain="protein_design",
                purpose="structural_scoring",
            )
        except Exception:
            return unavailable_context()

    def _explain_grounded_results(self) -> None:
        if self.explainer is None:
            self.explanations = ()
            self.design_run.critique_explanation = {
                "status": "skipped",
                "artifact": None,
                "hermes_run_id": None,
                "error": None,
            }
            return
        if not self.grounded_results and self.critic_records:
            self.grounded_results = tuple(
                attach_grounded_context(
                    list(self.critic_records),
                    self._grounded_context(),
                )
            )
        try:
            explained = tuple(
                self.explainer.explain(item) for item in self.grounded_results
            )
        except Exception as error:
            self.explanations = ()
            self.design_run.critique_explanation = {
                "status": "failed",
                "artifact": None,
                "hermes_run_id": None,
                "error": str(error),
            }
            return
        self.explanations = explained
        path = self._write_json(
            "critic_explanation",
            [item.to_dict() for item in explained],
        )
        hermes_run_id = next(
            (item.hermes_run_id for item in explained if item.hermes_run_id),
            None,
        )
        self.design_run.critique_explanation = {
            "status": "complete",
            "artifact": path.as_posix(),
            "hermes_run_id": hermes_run_id,
            "error": None,
        }

    def _refresh_explanation(self) -> None:
        current = self.design_run.critique_explanation or {}
        if self.design_run.critique.status != "complete":
            return
        if self.explainer is None:
            if not current:
                self._explain_grounded_results()
            return
        if current.get("status") == "complete":
            return
        self._explain_grounded_results()
