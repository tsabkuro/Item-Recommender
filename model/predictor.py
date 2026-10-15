"""Turn model scores into recommendations: candidate filtering and top-k

Candidates for a player are their champion's item pool (items making up >= 1% of what that
champion builds in training data), minus:
    - anything already owned
    - a second pair of boots
    - items locked to another champion
The model only ranks candidates
"""
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from threadpoolctl import threadpool_limits

from model.dataset import Encoder
from recommender.catalog import Catalog

POOL_MIN_SHARE = 0.01
SINGLE_THREAD_MAX_ROWS = 64


@dataclass
class ItemPools:
    by_champion: dict[str, frozenset[int]]
    by_role: dict[str, frozenset[int]]  # fallback for champions unseen in training

    @classmethod
    def fit(cls, train: pl.DataFrame, min_share: float = POOL_MIN_SHARE) -> "ItemPools":
        def pools(key: str) -> dict[str, frozenset[int]]:
            shares = (train.group_by(key, "label").len()
                .with_columns(share=pl.col("len") / pl.col("len").sum().over(key))
                .filter(pl.col("share") >= min_share))
            return {k[0]: frozenset(g["label"].to_list()) for k, g in shares.group_by(key)}
        return cls(pools("champion"), pools("role"))

    def get(self, champion: str, role: str) -> frozenset[int]:
        return self.by_champion.get(champion) or self.by_role.get(role, frozenset())


def candidate_mask(df: pl.DataFrame, classes: np.ndarray, pools: ItemPools, catalog: Catalog) -> np.ndarray:
    """Boolean (rows x classes): which items may be recommended for each row"""
    n, c = len(df), len(classes)
    owned = df.select([f"own_{i}" for i in classes]).to_numpy().astype(bool)
    is_boots = np.array([i in catalog.tier2_boots for i in classes])
    locked_to = [catalog.items[i].required_champion if i in catalog.items else None for i in classes]

    mask = np.zeros((n, c), dtype=bool)
    col = {item: j for j, item in enumerate(classes)}
    champions, roles = df["champion"].to_list(), df["role"].to_list()
    pool_rows: dict[tuple[str, str], np.ndarray] = {}
    for r, (champion, role) in enumerate(zip(champions, roles)):
        key = (champion, role)
        if key not in pool_rows:
            row = np.zeros(c, dtype=bool)
            row[[col[i] for i in pools.get(champion, role) if i in col]] = True
            for j, required in enumerate(locked_to):
                if required and required != champion:
                    row[j] = False
            pool_rows[key] = row
        mask[r] = pool_rows[key]
    mask &= ~owned
    mask[df["has_boots"].to_numpy().astype(bool)[:, None] & is_boots[None, :]] = False
    return mask


def top_k(scores: np.ndarray, mask: np.ndarray, k: int = 3) -> np.ndarray:
    """Indices of the k best allowed classes per row (best first)"""
    masked = np.where(mask, scores, -np.inf)
    idx = np.argpartition(-masked, kth=min(k, masked.shape[1] - 1), axis=1)[:, :k]
    order = np.take_along_axis(masked, idx, axis=1).argsort(axis=1)[:, ::-1]
    return np.take_along_axis(idx, order, axis=1)


@dataclass
class Recommender:
    model: object  # anything with predict_proba(X) -> (rows x classes)
    encoder: Encoder
    classes: np.ndarray  # item id per model output column
    pools: ItemPools
    name: str
    meta: dict

    def scores(self, df: pl.DataFrame) -> np.ndarray:
        if hasattr(self.model, "predict_proba_df"):  # the frequency baseline works on rows directly
            return self.model.predict_proba_df(df)
        X = self.encoder.transform(df)
        if len(df) <= SINGLE_THREAD_MAX_ROWS:
            with threadpool_limits(1):
                return self.model.predict_proba(X)
        return self.model.predict_proba(X)

    def recommend(self, df: pl.DataFrame, catalog: Catalog, k: int = 3,
            exclude: frozenset[int] = frozenset()) -> tuple[np.ndarray, np.ndarray]:
        """
        (item ids, probabilities renormalized over allowed items), each rows x k
        `exclude`: items never to recommend, on top of the usual candidate mask
        """
        scores = self.scores(df)
        mask = candidate_mask(df, self.classes, self.pools, catalog)
        mask[:, np.isin(self.classes, list(exclude))] = False
        idx = top_k(scores, mask, k)
        allowed = np.where(mask, scores, 0).sum(axis=1, keepdims=True)
        probs = np.take_along_axis(scores, idx, axis=1) / np.maximum(allowed, 1e-12)
        return self.classes[idx], probs

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> "Recommender":
        return joblib.load(path)
