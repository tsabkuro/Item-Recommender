"""Load gold, split train/test by time, encode features

Split: games ordered by start time, the newest `test_fraction` of games form the test set
    - whole games stay on one side (rows from the same game are highly correlated)
    - testing on the newest games (train on past and try to predict future)
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from recommender.catalog import Catalog
from recommender.features import CATEGORICAL, feature_names


def load_split(data_dir: Path, test_fraction: float = 0.1, max_train_rows: int | None = None,
        max_test_rows: int | None = None, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    examples = pl.scan_parquet(data_dir / "gold" / "examples" / "*.parquet")
    games = examples.select("match_id", "game_start_ms").unique().sort("game_start_ms").collect()
    cutoff = games["game_start_ms"][int(len(games) * (1 - test_fraction))]
    train = examples.filter(pl.col("game_start_ms") < cutoff)
    test = examples.filter(pl.col("game_start_ms") >= cutoff)
    return _sample(train, max_train_rows, seed), _sample(test, max_test_rows, seed + 1)


def _sample(lf: pl.LazyFrame, n: int | None, seed: int) -> pl.DataFrame:
    total = lf.select(pl.len()).collect().item()
    if not n or total <= n:
        return lf.collect()
    kept_per_million = int(n / total * 1_000_000)
    return (lf.with_row_index("_row").filter(pl.col("_row").hash(seed) % 1_000_000 < kept_per_million)
        .drop("_row").collect())


@dataclass
class Encoder:
    feature_names: list[str]
    vocab: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def fit(cls, train: pl.DataFrame, catalog: Catalog) -> "Encoder":
        names = feature_names(catalog)
        vocab = {c: sorted(train[c].unique().to_list()) for c in CATEGORICAL}
        return cls(names, vocab)

    @property
    def categorical_mask(self) -> list[bool]:
        return [n in CATEGORICAL for n in self.feature_names]

    def transform(self, df: pl.DataFrame) -> np.ndarray:
        cols = []
        for name in self.feature_names:
            if name in CATEGORICAL:
                codes = {v: i for i, v in enumerate(self.vocab[name])}
                cols.append(df[name].replace_strict(codes, default=None, return_dtype=pl.Float32).fill_null(np.nan))
            else:
                cols.append(df[name].cast(pl.Float32))
        return pl.DataFrame(cols).to_numpy()
