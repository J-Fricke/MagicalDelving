from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests import RequestException


_USER_AGENT = "MagicalDelving/0.1 (+https://github.com/J-Fricke/MagicalDelving)"
_COLLECTION_URL = "https://api.scryfall.com/cards/collection"


def _default_cache_path() -> Path:
    """
    Prefer an OS cache dir, but avoid extra deps.
    """
    # XDG on linux, otherwise fallback to ~/.cache
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".cache")
    return base / "magicaldelving" / "scryfall_cache.json"


def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


@dataclass
class ScryfallCache:
    path: Path

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.path)


class ScryfallClient:
    """
    Fetches card objects from Scryfall and caches them locally by normalized name.

    Notes:
      - Uses the /cards/collection endpoint (up to 75 identifiers per request).
      - Cache stores the full Scryfall card JSON so future needs don't require schema edits.
      - If OFFLINE is enabled and a card is missing from cache, we raise.
    """

    def __init__(
        self,
        cache_path: Optional[str | Path] = None,
        offline: bool = False,
        timeout_s: int = 30,
    ) -> None:
        self.cache = ScryfallCache(Path(cache_path) if cache_path else _default_cache_path())
        self.offline = offline or (os.environ.get("MAGICALDELVING_OFFLINE") == "1")
        self.timeout_s = timeout_s

        self._db: Dict[str, Any] = self.cache.load()

    def _write(self) -> None:
        self.cache.save(self._db)

    def get_cached(self, name: str) -> Optional[Dict[str, Any]]:
        return self._db.get(_norm_name(name))

    def put_cached(self, name: str, card_json: Dict[str, Any]) -> None:
        self._db[_norm_name(name)] = card_json

    def fetch_many_by_name(self, names: Iterable[str]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        """
        Returns (found_map, missing_names).
        found_map maps ORIGINAL input name -> Scryfall card JSON.
        """
        wanted = [n for n in (names or []) if (n or "").strip()]
        found: Dict[str, Dict[str, Any]] = {}
        missing: List[str] = []

        # 1) serve from cache
        unfetched: List[str] = []
        for n in wanted:
            cached = self.get_cached(n)
            if isinstance(cached, dict):
                found[n] = cached
            else:
                unfetched.append(n)

        if not unfetched:
            return found, missing

        if self.offline:
            return found, unfetched

        # 2) fetch remaining in chunks of 75
        CHUNK = 75
        for i in range(0, len(unfetched), CHUNK):
            chunk = unfetched[i : i + CHUNK]
            payload = {"identifiers": [{"name": c} for c in chunk]}
            try:
                r = requests.post(
                    _COLLECTION_URL,
                    json=payload,
                    timeout=self.timeout_s,
                    headers={"User-Agent": _USER_AGENT},
                )
                r.raise_for_status()
                data = r.json()
            except RequestException as e:
                raise RuntimeError(
                    "Failed to reach Scryfall. If you're running offline, set MAGICALDELVING_OFFLINE=1 "
                    "(or pass --offline in mulligan-sim) after warming the cache once on a machine with internet."
                ) from e
            cards = data.get("data") if isinstance(data, dict) else None
            if not isinstance(cards, list):
                # if Scryfall is unhappy, treat everything as missing
                missing.extend(chunk)
                continue

            # Build a quick index by exact name (case-insensitive), and also accept printed_name.
            by_name: Dict[str, Dict[str, Any]] = {}
            for c in cards:
                if not isinstance(c, dict):
                    continue
                nm = c.get("name")
                if isinstance(nm, str):
                    by_name[_norm_name(nm)] = c

            # The /collection endpoint may return errors in "not_found"
            not_found = data.get("not_found")
            if isinstance(not_found, list):
                for nf in not_found:
                    if isinstance(nf, str):
                        missing.append(nf)

            # resolve each requested chunk element
            for req_name in chunk:
                key = _norm_name(req_name)
                c = by_name.get(key)
                if c is None:
                    # best-effort: allow prefix match for basic lands / punctuation mismatches
                    # (keep conservative; we don't want wrong cards)
                    c = by_name.get(key.replace("’", "'"))
                if c is None:
                    missing.append(req_name)
                    continue

                found[req_name] = c
                self.put_cached(req_name, c)

        # persist cache updates
        self._write()
        return found, missing
