"""Protein Prioritization Agent v1.

Selects an experimental portfolio from already ranked and
criticized candidates. This is an experimental policy, not a
new structural ranking.

rank and ranking_score are copied unchanged.
Critic agreement=low is a review flag, not a rejection rule.
"""

from dataclasses import dataclass
from typing import Literal

from cellertz.domains.protein_design.agents.critic import ProteinCriticRecord


SelectionBucket = Literal["core", "novel", "exploratory"]


@dataclass(frozen=True)
class PortfolioPolicy:
    core_slots: int = 6
    novelty_slots: int = 2
    exploratory_slots: int = 2

    def __post_init__(self) -> None:
        for name, value in (
            ("core_slots", self.core_slots),
            ("novelty_slots", self.novelty_slots),
            ("exploratory_slots", self.exploratory_slots),
        ):
            if value < 0:
                raise ValueError(
                    f"{name} must be >= 0."
                )

    @property
    def total_slots(self) -> int:
        return (
            self.core_slots
            + self.novelty_slots
            + self.exploratory_slots
        )


@dataclass(frozen=True)
class ProteinPortfolioRecord:
    candidate_id: str
    rank: int
    ranking_score: float | None
    ranking_signal: str
    selected: bool
    selection_bucket: SelectionBucket | None
    selection_order: int | None
    selection_reason: str
    critic_agreement: str | None
    structural_conflict: bool | None
    review_flag: bool
    is_probability: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "ranking_score": self.ranking_score,
            "ranking_signal": self.ranking_signal,
            "selected": self.selected,
            "selection_bucket": self.selection_bucket,
            "selection_order": self.selection_order,
            "selection_reason": self.selection_reason,
            "critic_agreement": self.critic_agreement,
            "structural_conflict": self.structural_conflict,
            "review_flag": self.review_flag,
            "is_probability": self.is_probability,
        }


def _by_rank(
    records: list[ProteinCriticRecord],
) -> list[ProteinCriticRecord]:
    return sorted(
        records,
        key=lambda item: (
            item.rank,
            item.candidate_id,
        ),
    )


def _cluster_of(
    candidate_id: str,
    clusters: dict[str, str] | None,
) -> str:
    if clusters is None:
        return candidate_id

    return clusters.get(candidate_id, candidate_id)


def _has_score(record: ProteinCriticRecord) -> bool:
    return record.ranking_score is not None


def _agreement(record: ProteinCriticRecord) -> str | None:
    if record.critic_assessment is None:
        return None

    return record.critic_assessment.agreement


def _conflict(record: ProteinCriticRecord) -> bool | None:
    if record.critic_assessment is None:
        return None

    return record.critic_assessment.structural_conflict


def _review_flag(record: ProteinCriticRecord) -> bool:
    agreement = _agreement(record)
    conflict = _conflict(record)

    return agreement == "low" or conflict is True


def _is_exploratory(record: ProteinCriticRecord) -> bool:
    assessment = record.critic_assessment

    if assessment is None:
        return True

    if assessment.missing_evidence:
        return True

    if assessment.agreement in {
        "unknown",
        "mixed",
        "low",
    }:
        return True

    return assessment.structural_conflict is True


def _core_reason(record: ProteinCriticRecord) -> str:
    reason = (
        "High-ranked representative selected "
        "under portfolio policy."
    )

    if _review_flag(record):
        reason += (
            " Structural disagreement flagged "
            "for review, not excluded."
        )

    return reason


class ProteinPrioritizationAgentV1:
    """Deterministic experimental portfolio policy."""

    def __init__(
        self,
        policy: PortfolioPolicy | None = None,
    ) -> None:
        self.policy = policy or PortfolioPolicy()

    def select(
        self,
        reviewed: list[ProteinCriticRecord],
        clusters: dict[str, str] | None = None,
    ) -> list[ProteinPortfolioRecord]:
        candidate_ids = [
            record.candidate_id
            for record in reviewed
        ]

        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(
                "Duplicate candidate_id in "
                "prioritization input."
            )

        ordered = _by_rank(reviewed)
        selected: dict[str, tuple[SelectionBucket, str]] = {}
        selected_clusters: set[str] = set()
        core_count = 0
        novelty_count = 0
        exploratory_count = 0

        for record in ordered:
            if core_count >= self.policy.core_slots:
                break

            if not _has_score(record):
                continue

            selected[record.candidate_id] = (
                "core",
                _core_reason(record),
            )
            selected_clusters.add(
                _cluster_of(
                    record.candidate_id,
                    clusters,
                )
            )
            core_count += 1

        for record in ordered:
            if novelty_count >= self.policy.novelty_slots:
                break

            if record.candidate_id in selected:
                continue

            if not _has_score(record):
                continue

            cluster = _cluster_of(
                record.candidate_id,
                clusters,
            )

            if cluster in selected_clusters:
                continue

            selected[record.candidate_id] = (
                "novel",
                "Novel sequence cluster selected "
                "under portfolio policy.",
            )
            selected_clusters.add(cluster)
            novelty_count += 1

        for record in ordered:
            if exploratory_count >= self.policy.exploratory_slots:
                break

            if record.candidate_id in selected:
                continue

            if not _has_score(record):
                continue

            if not _is_exploratory(record):
                continue

            selected[record.candidate_id] = (
                "exploratory",
                "Exploratory candidate selected "
                "under portfolio policy because of "
                "structural uncertainty or disagreement.",
            )
            exploratory_count += 1

        selection_order = {
            candidate_id: index
            for index, candidate_id in enumerate(
                [
                    record.candidate_id
                    for record in ordered
                    if record.candidate_id in selected
                ],
                start=1,
            )
        }

        results = []

        for record in reviewed:
            picked = selected.get(record.candidate_id)

            if picked is None:
                bucket: SelectionBucket | None = None
                order = None

                if not _has_score(record):
                    reason = (
                        "Missing ipSAE; not eligible "
                        "for experimental portfolio."
                    )
                else:
                    reason = (
                        "Not selected under the "
                        "experimental portfolio policy."
                    )
            else:
                bucket, reason = picked
                order = selection_order[record.candidate_id]

            results.append(
                ProteinPortfolioRecord(
                    candidate_id=record.candidate_id,
                    rank=record.rank,
                    ranking_score=record.ranking_score,
                    ranking_signal=record.ranking_signal,
                    selected=picked is not None,
                    selection_bucket=bucket,
                    selection_order=order,
                    selection_reason=reason,
                    critic_agreement=_agreement(record),
                    structural_conflict=_conflict(record),
                    review_flag=_review_flag(record),
                    is_probability=record.is_probability,
                )
            )

        return results
