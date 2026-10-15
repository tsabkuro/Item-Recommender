"""Client for the League Live Client Data API

API only exists when game running
"""
import requests
import urllib3

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
\
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LiveClient:
    def __init__(self, session: requests.Session | None = None, url: str = LIVE_URL, timeout: float = 1.0):
        self.session = session or requests.Session()
        self.session.verify = False
        self.url = url
        self.timeout = timeout

    def fetch(self) -> dict | None:
        """Full game snapshot"""
        try:
            resp = self.session.get(self.url, timeout=self.timeout)
        except requests.RequestException:
            return None  # game not running: nothing is listening on the port
        if resp.status_code != 200:
            return None  # e.g. 404 during the loading screen
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if "gameData" in data else None
