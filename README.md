# League of Legends Live Item Recommender

Recommends your next item during a live game, from your champion, your items and what the enemy
team is building. Trained on ~56k ranked games (Silver–Diamond, NA/EUW/KR) crawled from Riot's API.

## Run it

```bash
make setup    # Python 3.11+ and Node 20+
make demo     # replays a recorded game -> http://localhost:8765
```

With a Riot API key in `.env` (`RIOT_API_KEY=...`), `make crawl-sample` crawls 20 games live and runs
them through the pipeline. `make test` runs the tests.

## How it's built

**`crawler/`**: collects games within Riot's rate limits
- A sliding-window rate limiter, built against how Riot actually counts requests, so it
  uses the whole limit without going over
- 64 threads per region share one client. Work is claimed from SQLite
- Responses are stored byte for byte

**`pipeline/`**: raw JSON → clean tables → training rows
- Riot logs item events, not inventories, so inventories are rebuilt by replaying the events and
  checked against each player's final items
- Training rows only use what the live game can see, including enemy items as out of date as the
  game shows them

**`model/`, `live/`**: a gradient-boosting model, served by a FastAPI app with a React page. The demo bundles a smaller model, since the full
one is too large for the repo.