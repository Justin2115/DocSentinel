"""Ranking metrics for manually judged document search queries."""

import math


def ranking_metrics(retrieved_ids: list[int], relevant_ids: list[int], k: int = 10) -> dict[str, float]:
    relevant = set(relevant_ids)
    if not relevant:
        return {f"recall@{k}": 0.0, f"precision@{k}": 0.0, "mrr": 0.0, f"ndcg@{k}": 0.0}
    ranked = retrieved_ids[:k]
    found = [doc_id for doc_id in ranked if doc_id in relevant]
    reciprocal_rank = next((1 / (index + 1) for index, doc_id in enumerate(retrieved_ids) if doc_id in relevant), 0.0)
    dcg = sum((1 / math.log2(index + 2)) for index, doc_id in enumerate(ranked) if doc_id in relevant)
    ideal_count = min(len(relevant), k)
    idcg = sum(1 / math.log2(index + 2) for index in range(ideal_count))
    return {
        f"recall@{k}": len(found) / len(relevant),
        f"precision@{k}": len(found) / max(len(ranked), 1),
        "mrr": reciprocal_rank,
        f"ndcg@{k}": dcg / max(idcg, 1e-12),
    }
