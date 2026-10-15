"""Raw storage of API responses as gzipped JSON

Layout: {data_dir}/raw/{region}/{patch}/{kind}/{match_id}.json.gz
"""
import gzip
import json
import os
from pathlib import Path

GZIP_LEVEL = 6


def raw_path(data_dir: Path, region: str, patch: str, kind: str, match_id: str) -> Path:
    return data_dir / "raw" / region / patch / kind / f"{match_id}.json.gz"


def write_gz(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(body, GZIP_LEVEL))
    os.replace(tmp, path)


def read_json_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
