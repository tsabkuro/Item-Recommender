"""Data Dragon: patch versions and static item/champion data

Usage:
    python -m crawler.ddragon            # check now
    python -m crawler.ddragon --daily    # check now, then once every 24h
"""
import argparse
import json
import logging
import shutil
import sys
import time

import requests

from crawler import config

log = logging.getLogger(__name__)


def patch_of(version: str) -> tuple[int, int] | None:
    """'16.19.823.722' or '16.19.1' -> (16, 19)"""
    parts = version.split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    return int(parts[0]), int(parts[1])


def local_version() -> str | None:
    path = config.STATIC_DIR / config.VERSION_FILE
    return path.read_text().strip() if path.exists() else None


def _get_json(url: str):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def sync() -> bool:
    """
    Replace data/static/current if Riot's latest version differs
    Return True if updated
    """
    versions = _get_json(f"{config.DDRAGON_URL}/api/versions.json")
    latest = versions[0]
    installed = local_version()
    complete = all((config.STATIC_DIR / name).exists() for name in config.STATIC_FILES)
    if latest == installed and complete:
        log.info("Data Dragon up to date (%s)", latest)
        return False

    # download into staging dir first (a sibling of current, so the swap is a rename)
    current = config.STATIC_DIR
    staging = current.with_name(f".staging-{latest}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name in config.STATIC_FILES:
        data = _get_json(f"{config.DDRAGON_URL}/cdn/{latest}/data/en_US/{name}")
        (staging / name).write_text(json.dumps(data), encoding="utf-8")
    (staging / config.VERSION_FILE).write_text(latest)

    # switch current with newer
    old = current.with_name(".old")
    shutil.rmtree(old, ignore_errors=True)
    if current.exists():
        current.rename(old)
    staging.rename(current)
    shutil.rmtree(old, ignore_errors=True)
    log.info("Data Dragon updated %s -> %s", installed or "(none)", latest)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--daily", action="store_true", help="check every 24h")
    args = parser.parse_args(argv)

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [ddragon] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(config.LOG_DIR / "ddragon.log")],
    )
    while True:
        try:
            sync()
        except requests.RequestException as exc:
            if not args.daily:
                raise
            log.error("Data Dragon check failed (%s); retrying next cycle", exc)
        if not args.daily:
            return 0
        time.sleep(config.CHECK_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
