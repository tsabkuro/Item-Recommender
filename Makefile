# Needs Python 3.11+ and Node 20+. Crawling also needs RIOT_API_KEY in .env
PY = .venv/bin/python

setup:     # install everything, download Data Dragon, build the page
	python3 -m venv .venv && $(PY) -m pip install -q -r requirements.txt
	cd web && npm install --silent && npm run build
	$(PY) -m crawler.ddragon

demo:      # replay a recorded game with the bundled model, then open http://localhost:8765
	$(PY) -m live.app --replay demo/recording.jsonl.gz --model-dir demo/model --speed 5 --start-minute 5

live:      # during a real game
	$(PY) -m live.app

test:
	$(PY) -m pytest -q

crawl-sample:  # crawl 20 NA games in the foreground, then build silver + gold from them (needs .env)
	$(PY) -m crawler.run --region na --max-matches 20
	$(MAKE) pipeline

crawl:     # NA, EUW, KR crawlers + daily Data Dragon sync, in the background
	scripts/start_crawlers.sh

stop:
	scripts/stop_crawlers.sh

status:
	$(PY) scripts/status.py

pipeline:  # bronze -> silver -> gold
	$(PY) -m pipeline.silver && $(PY) -m pipeline.gold

train:     # ~5 hours on 1.5M rows
	$(PY) -m model.train --train-rows 1500000 --test-rows 300000 --out data/models/full

.PHONY: setup demo live test crawl-sample crawl stop status pipeline train
