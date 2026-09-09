import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

class RiotAPIError(Exception):
    pass

class RiotAPI:
    def __init__(self, api_key, region):
        self.api_key = api_key
        self.region = region
        self.platform = self._region_to_platform(region)
        self._champ_cache = None

    def _region_to_platform(self, region):
        mapping = {
            "na1": "na1", "br1": "br1", "la1": "la1", "la2": "la2",
            "euw": "euw1", "eune": "eun1", "tr1": "tr1", "ru": "ru",
            "oc1": "oc1", "kr": "kr", "jp1": "jp1"
        }
        return mapping.get(region.lower(), region.lower())

    def _get(self, url, retries=2):
        req = urllib.request.Request(url)
        req.add_header("X-Riot-Token", self.api_key)
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    retry_after = e.headers.get("Retry-After")
                    if retry_after:
                        try:
                            time.sleep(float(retry_after))
                            continue
                        except ValueError:
                            pass
                    time.sleep(1)
                    continue
                body = e.read().decode()
                if e.code == 404:
                    raise RiotAPIError("account not found (check name/tag)")
                raise RiotAPIError(f"HTTP {e.code}: {body[:200]}")
            except urllib.error.URLError as e:
                raise RiotAPIError(f"request failed: {e.reason}")
        raise RiotAPIError("exhausted retries on 429")

    def get_puuid(self, game_name, tag_line):
        url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        data = self._get(url)
        return data["puuid"]

    def _load_champion_names(self):
        cache_dir = Path.home() / ".local" / "share" / "lol-tracker"
        cache_file = cache_dir / "champions.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    self._champ_cache = json.load(f)
                return
            except (json.JSONDecodeError, OSError):
                pass

        url = "https://ddragon.leagueoflegends.com/cdn/14.15.1/data/en_US/champion.json"
        try:
            with urllib.request.urlopen(url) as resp:
                raw = json.loads(resp.read().decode())
        except Exception:
            self._champ_cache = {}
            return

        names = {}
        for key, info in raw.get("data", {}).items():
            cid = int(info["key"])
            names[cid] = info["name"]
        self._champ_cache = names
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump(names, f)
        except OSError:
            pass

    def get_champion_mastery(self, puuid):
        url = f"https://{self.platform}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
        data = self._get(url)
        if self._champ_cache is None:
            self._load_champion_names()
        for entry in data:
            cid = entry.get("championId")
            entry["championName"] = self._champ_cache.get(cid, f"Champion-{cid}")
        return data
