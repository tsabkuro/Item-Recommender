"""How good is a recommender? Scored on the test set (the newest games, never trained on)

Every test row is a moment in a real game where we know which item the player finished next
We ask the recommender for its top 3 at that moment and compare against what the player did

Metrics:
    top1            how often the player's actual next item was our #1 pick
    top3            how often it was anywhere in our top 3 (the headline number)
    coverage        how often the actual item was even allowed as a candidate (in the champion's
                        item pool, not owned...
    top3 by role, and for rare vs common champions (rare = least-played quarter of champions)
    follow_gain     did players who happened to buy our #1 do better than expected?
                        "better than expected" = won minus the win model's P(win) for that moment,
                        averaged over rows matching our #1, minus the same over rows that didn't
    latency         time for one single-row recommendation, the same call the live app makes
"""
import time

import numpy as np
import polars as pl

from model.predictor import Recommender, candidate_mask
from recommender.catalog import Catalog


def evaluate(rec: Recommender, test: pl.DataFrame, catalog: Catalog, rare_champions: set[str],
        p_win: np.ndarray) -> dict:
    items, _ = rec.recommend(test, catalog, k=3)
    label = test["label"].to_numpy()
    hit1 = items[:, 0] == label
    hit3 = (items == label[:, None]).any(axis=1)
    mask = candidate_mask(test, rec.classes, rec.pools, catalog)
    col = {item: j for j, item in enumerate(rec.classes)}
    covered = np.array([j is not None and mask[r, j] for r, j in enumerate(col.get(x) for x in label)])

    frame = test.select("role", "champion").with_columns(hit1=hit1, hit3=hit3)
    by_role = {r: round(float(v), 4) for r, v in frame.group_by("role").agg(pl.col("hit3").mean()).iter_rows()}
    rare = frame["champion"].is_in(list(rare_champions)).to_numpy()

    advantage = test["win"].cast(pl.Float64).to_numpy() - p_win
    follow_gain = float(advantage[hit1].mean() - advantage[~hit1].mean())

    sample = test.head(200)
    timings = []
    for r in range(len(sample)):
        start = time.perf_counter()
        rec.recommend(sample[r:r + 1], catalog, k=3)
        timings.append((time.perf_counter() - start) * 1000)

    per_champion = (frame.group_by("champion").agg(n=pl.len(), top3=pl.col("hit3").mean())
        .filter(pl.col("n") >= 200).sort("top3"))
    return {
        "model": rec.name, "rows": len(test),
        "top1": round(float(hit1.mean()), 4), "top3": round(float(hit3.mean()), 4),
        "coverage": round(float(covered.mean()), 4),
        "top3_rare_champions": round(float(hit3[rare].mean()), 4) if rare.any() else None,
        "top3_common_champions": round(float(hit3[~rare].mean()), 4),
        "top3_by_role": by_role,
        "worst_champions_top3": [(c, round(v, 3)) for c, _, v in per_champion.head(5).iter_rows()],
        "follow_gain": round(follow_gain, 4),
        "latency_ms_median": round(float(np.median(timings)), 2),
        "latency_ms_p99": round(float(np.percentile(timings, 99)), 2),
    }


def print_table(results: list[dict]) -> None:
    cols = ["model", "top1", "top3", "coverage", "top3_rare_champions", "top3_common_champions", "follow_gain",
        "latency_ms_median"]
    print("\n" + " | ".join(f"{c:>12}" for c in cols))
    for r in results:
        print(" | ".join(f"{str(r[c]):>12}" for c in cols))
    for r in results:
        print(f"\n{r['model']}: top3 by role {r['top3_by_role']}")
        print(f"  weakest champions (top3): {r['worst_champions_top3']}")
