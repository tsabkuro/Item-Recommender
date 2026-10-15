"""Train and evaluate all recommenders

    python -m model.train [--train-rows 300000] [--test-rows 200000] [--out data/models/dev]

Trains, on the same training rows:
    baseline        per-champion frequency table (model/baseline.py)
    win             P(win | game state)
    imitation       gradient boosting predicting the next finished item, every row weighted 1
    awr_beta{b}     same, rows weighted by exp(b * (won - P(win | state))): decisions whose outcome
                    beat what the situation predicted count more (advantage weighting)
Then evaluates them all on the held-out newest games and saves everything
"""
import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, roc_auc_score

from crawler import config as crawler_config
from model.baseline import FrequencyBaseline
from model.dataset import Encoder, load_split
from model.evaluate import evaluate, print_table
from model.predictor import ItemPools, Recommender
from recommender.catalog import Catalog
from recommender.features import FEATURE_VERSION

MIN_CLASS_EXAMPLES = 200
WEIGHT_CLIP = (0.1, 10.0)
MAX_ITER = 300
BETAS = (1.0,)  # advantage-weighting strengths for the awr_beta{b} models


def gbm(categorical_mask: list[bool]) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        learning_rate=0.1, max_iter=MAX_ITER, max_leaf_nodes=31, min_samples_leaf=100, l2_regularization=1.0,
        categorical_features=categorical_mask, early_stopping=True, validation_fraction=0.1, n_iter_no_change=10,
        random_state=0)


def crossfit_win_probability(X: np.ndarray, won: np.ndarray, folds: np.ndarray, mask: list[bool]) -> np.ndarray:
    """
    P(win | state) for each training row

    In-sample predictions would partly memorize each game's outcome and shrink the advantage
    (won - p) toward zero, so each half of the games is scored by a model trained on the other half
    """
    p = np.zeros(len(won))
    for fold in (0, 1):
        model = gbm(mask).fit(X[folds != fold], won[folds != fold])
        p[folds == fold] = model.predict_proba(X[folds == fold])[:, 1]
    return p


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train-rows", type=int, default=300_000)
    parser.add_argument("--test-rows", type=int, default=200_000)
    parser.add_argument("--out", type=Path, default=crawler_config.MODELS_DIR / "dev")
    args = parser.parse_args(argv)

    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    catalog = Catalog.load(crawler_config.STATIC_DIR)
    train, test = load_split(crawler_config.DATA_DIR, max_train_rows=args.train_rows, max_test_rows=args.test_rows)
    counts = train.group_by("label").len()
    classes = np.array(sorted(counts.filter(pl.col("len") >= MIN_CLASS_EXAMPLES)["label"].to_list()))
    train = train.filter(pl.col("label").is_in(classes.tolist()))
    log(f"train {len(train):,} rows / test {len(test):,} rows (newest games) / {len(classes)} item classes")

    encoder = Encoder.fit(train, catalog)
    X = encoder.transform(train)
    y = np.searchsorted(classes, train["label"].to_numpy())
    won = train["win"].cast(pl.Int8).to_numpy()
    X_test = encoder.transform(test)
    pools = ItemPools.fit(train)
    champion_games = train.group_by("champion").agg(pl.col("match_id").n_unique().alias("games"))
    rare = set(champion_games.filter(pl.col("games") <= champion_games["games"].quantile(0.25))["champion"])
    meta = {"feature_version": FEATURE_VERSION, "catalog_version": catalog.version,
        "patches": sorted(train["patch"].unique().to_list()), "train_rows": len(train),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    args.out.mkdir(parents=True, exist_ok=True)

    recommenders = [Recommender(FrequencyBaseline(classes).fit(train), encoder, classes, pools, "baseline", meta)]
    log("trained baseline")
    folds = (train["match_id"].hash() % 2).to_numpy()
    del train  # the models only need the encoded matrix X from here

    log("win model (2-fold cross-fit for training-row advantages)...")
    p_train = crossfit_win_probability(X, won, folds, encoder.categorical_mask)
    win_model = gbm(encoder.categorical_mask).fit(X, won)
    p_test = win_model.predict_proba(X_test)[:, 1]
    won_test = test["win"].cast(pl.Int8).to_numpy()
    win_metrics = {"auc": round(roc_auc_score(won_test, p_test), 4),
        "log_loss": round(log_loss(won_test, p_test), 4), "base_rate": round(float(won_test.mean()), 4)}
    log(f"win model on test games: {win_metrics}")
    joblib.dump({"model": win_model, "encoder": encoder, "meta": meta}, args.out / "win.joblib")

    for beta in [0.0, *BETAS]:
        name = "imitation" if beta == 0 else f"awr_beta{beta:g}"
        weights = np.clip(np.exp(beta * (won - p_train)), *WEIGHT_CLIP)
        started = time.monotonic()
        model = gbm(encoder.categorical_mask).fit(X, y, sample_weight=weights / weights.mean())
        log(f"trained {name}: {model.n_iter_} iterations in {time.monotonic() - started:.0f}s")
        recommenders.append(Recommender(model, encoder, classes, pools, name, {**meta, "beta": beta}))

    results = []
    for rec in recommenders:
        rec.save(args.out / f"{rec.name}.joblib")
        results.append(evaluate(rec, test, catalog, rare, p_test))
    print_table(results)
    (args.out / "report.json").write_text(json.dumps({"win_model": win_metrics, "recommenders": results,
        "meta": meta}, indent=2))
    log(f"saved models and report.json to {args.out}")


if __name__ == "__main__":
    main()
