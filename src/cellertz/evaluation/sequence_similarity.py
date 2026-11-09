from dataclasses import dataclass

from Bio import Align


def build_protein_aligner() -> Align.PairwiseAligner:
    aligner = Align.PairwiseAligner(scoring="blastp")
    aligner.mode = "global"
    return aligner


def global_sequence_identity(
    sequence_a: str,
    sequence_b: str,
    aligner: Align.PairwiseAligner | None = None,
) -> float:
    if not sequence_a or not sequence_b:
        raise ValueError("Sequences must not be empty.")

    if aligner is None:
        aligner = build_protein_aligner()

    alignment = aligner.align(
        sequence_a,
        sequence_b,
    )[0]

    counts = alignment.counts()

    if alignment.length == 0:
        return 0.0

    return counts.identities / alignment.length


@dataclass(frozen=True)
class SimilarityEdge:
    candidate_a: str
    candidate_b: str
    identity: float


class UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {
            item: item
            for item in items
        }

    def find(self, item: str) -> str:
        if self.parent[item] != item:
            self.parent[item] = self.find(
                self.parent[item]
            )

        return self.parent[item]

    def union(
        self,
        item_a: str,
        item_b: str,
    ) -> None:
        root_a = self.find(item_a)
        root_b = self.find(item_b)

        if root_a != root_b:
            self.parent[root_b] = root_a


def cluster_from_edges(
    candidate_ids: list[str],
    edges: list[SimilarityEdge],
    threshold: float,
) -> dict[str, int]:
    uf = UnionFind(candidate_ids)

    for edge in edges:
        if edge.identity >= threshold:
            uf.union(
                edge.candidate_a,
                edge.candidate_b,
            )

    roots = {
        candidate_id: uf.find(candidate_id)
        for candidate_id in candidate_ids
    }

    root_to_cluster: dict[str, int] = {}

    for root in sorted(set(roots.values())):
        root_to_cluster[root] = len(
            root_to_cluster
        )

    return {
        candidate_id: root_to_cluster[root]
        for candidate_id, root in roots.items()
    }
