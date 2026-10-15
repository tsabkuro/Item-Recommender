"""Per-champion lookup-table baseline: 
    what do players on this champion usually finish next given what they already own?

Scores back off from specific to general:
    1. champion + role + exact set of finished items owned
    2. champion + role + number of finished items owned
    3. champion
    4. role
"""
import numpy as np
import polars as pl

LEVEL_WEIGHTS = (1.0, 1e-3, 1e-6, 1e-9)
LEVEL_KEYS = (("champion", "role", "owned"), ("champion", "role", "n_finished"), ("champion",), ("role",))


class FrequencyBaseline:
    def __init__(self, classes: np.ndarray, min_support: int = 20):
        self.classes = classes
        self.min_support = min_support
        self._tables: list[pl.DataFrame] = []  # per level: key columns, label column index, share

    def _keyed(self, df: pl.DataFrame) -> pl.DataFrame:
        """Key cols of every level. owned items as one string (e.g. "0010...")"""
        owned = pl.concat_str([(pl.col(f"own_{i}") > 0).cast(pl.Int8).cast(pl.String) for i in self.classes])
        return df.select("champion", "role", "n_finished", owned=owned)

    def fit(self, train: pl.DataFrame) -> "FrequencyBaseline":
        col = {int(item): j for j, item in enumerate(self.classes)}
        label_col = train["label"].replace_strict(col, default=None, return_dtype=pl.Int32)
        keyed = self._keyed(train).with_columns(label_col=label_col).drop_nulls("label_col")
        for keys in LEVEL_KEYS:
            counts = keyed.group_by(*keys, "label_col").len()
            self._tables.append(counts
                .with_columns(total=pl.col("len").sum().over(keys))
                .filter(pl.col("total") >= self.min_support)
                .select(*keys, "label_col", share=(pl.col("len") / pl.col("total")).cast(pl.Float32)))
        return self

    def predict_proba_df(self, df: pl.DataFrame) -> np.ndarray:
        scores = np.zeros((len(df), len(self.classes)), dtype=np.float32)
        keyed = self._keyed(df).with_row_index("row")
        for weight, table, keys in zip(LEVEL_WEIGHTS, self._tables, LEVEL_KEYS):
            hits = keyed.join(table, on=list(keys))
            np.add.at(scores, (hits["row"].to_numpy(), hits["label_col"].to_numpy()), weight * hits["share"].to_numpy())
        return scores
