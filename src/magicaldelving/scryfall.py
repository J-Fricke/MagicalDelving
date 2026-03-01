from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests import RequestException


_USER_AGENT = "MagicalDelving/0.1 (+https://github.com/J-Fricke/MagicalDelving)"
_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
_NAMED_URL = "https://api.scryfall.com/cards/named"


def _default_cache_path() -> Path:
    """
    Prefer an OS cache dir, but avoid extra deps.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".cache")
    return base / "magicaldelving" / "scryfall_cache.json"


def _norm_name(name: str) -> str:
    # Lowercase + collapse whitespace
    return " ".join((name or "").strip().lower().split())


_DBL_SLASH_RE = re.compile(r"\s*//\s*")


def _sanitize_name(name: str) -> str:
    """
    Normalize common “same name, different formatting” situations:
      - collapse whitespace
      - normalize 'A//B' into 'A // B'
      - normalize curly apostrophe
    """
    s = (name or "").replace("’", "'").strip()
    if not s:
        return s
    # normalize the '//' separator spacing
    s = _DBL_SLASH_RE.sub(" // ", s)
    # collapse any other whitespace
    s = " ".join(s.split())
    return s


def _front_face_name(name: str) -> str:
    s = _sanitize_name(name)
    if " // " in s:
        return s.split(" // ", 1)[0].strip()
    return s


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

    Strategy:
      1) Serve from cache.
      2) Batch fetch via /cards/collection (fast, up to 75 identifiers).
      3) Fallback for misses via /cards/named?fuzzy= (handles tricky names like 'Wear // Tear').
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

    def _key(self, name: str) -> str:
        return _norm_name(_sanitize_name(name))

    def get_cached(self, name: str) -> Optional[Dict[str, Any]]:
        # New key (sanitized)
        v = self._db.get(self._key(name))
        if isinstance(v, dict):
            return v
        # Back-compat: older caches may have stored only _norm_name(name)
        v2 = self._db.get(_norm_name(name))
        return v2 if isinstance(v2, dict) else None

    def put_cached(self, name: str, card_json: Dict[str, Any]) -> None:
        self._db[self._key(name)] = card_json

    def _cache_under_common_names(self, requested_name: str, card_json: Dict[str, Any]) -> None:
        """
        Cache under:
          - requested input name
          - Scryfall canonical card name
          - front face name (for DFC/split convenience)
        """
        self.put_cached(requested_name, card_json)

        nm = card_json.get("name")
        if isinstance(nm, str) and nm.strip():
            self.put_cached(nm, card_json)
            self.put_cached(_front_face_name(nm), card_json)

        self.put_cached(_front_face_name(requested_name), card_json)

        # Also cache face names (if present) to help future lookups
        faces = card_json.get("card_faces")
        if isinstance(faces, list):
            for f in faces:
                if isinstance(f, dict):
                    fn = f.get("name")
                    if isinstance(fn, str) and fn.strip():
                        self.put_cached(fn, card_json)

    def _fetch_named_fuzzy(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Slow path: /cards/named?fuzzy=...
        """
        q = _sanitize_name(name)
        if not q:
            return None
        try:
            r = requests.get(
                _NAMED_URL,
                params={"fuzzy": q},
                timeout=self.timeout_s,
                headers={"User-Agent": _USER_AGENT},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else None
        except RequestException:
            return None

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
        still_missing: List[str] = []

        for i in range(0, len(unfetched), CHUNK):
            chunk = unfetched[i : i + CHUNK]

            # Send sanitized names to Scryfall, but keep mapping to original
            chunk_sanitized: List[str] = [_sanitize_name(c) for c in chunk]
            payload = {"identifiers": [{"name": c} for c in chunk_sanitized]}

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
                still_missing.extend(chunk)
                continue

            # Build index: canonical name + face names
            by_name: Dict[str, Dict[str, Any]] = {}
            for c in cards:
                if not isinstance(c, dict):
                    continue
                nm = c.get("name")
                if isinstance(nm, str) and nm.strip():
                    by_name[self._key(nm)] = c
                    by_name[self._key(_front_face_name(nm))] = c

                faces = c.get("card_faces")
                if isinstance(faces, list):
                    for f in faces:
                        if isinstance(f, dict):
                            fn = f.get("name")
                            if isinstance(fn, str) and fn.strip():
                                by_name[self._key(fn)] = c

            # Resolve each original request
            for req_name in chunk:
                k_full = self._key(req_name)
                k_front = self._key(_front_face_name(req_name))
                c = by_name.get(k_full) or by_name.get(k_front)

                if c is None:
                    # Try simple apostrophe normalization too (already in sanitize, but keep safe)
                    alt = _sanitize_name(req_name.replace("’", "'"))
                    c = by_name.get(self._key(alt)) or by_name.get(self._key(_front_face_name(alt)))

                if c is None:
                    still_missing.append(req_name)
                    continue

                found[req_name] = c
                self._cache_under_common_names(req_name, c)

        # 3) fallback: /cards/named?fuzzy=... for misses (usually just a few)
        final_missing: List[str] = []
        if still_missing:
            for req_name in still_missing:
                c = self._fetch_named_fuzzy(req_name)
                if c is None and " // " in _sanitize_name(req_name):
                    c = self._fetch_named_fuzzy(_front_face_name(req_name))

                if isinstance(c, dict):
                    found[req_name] = c
                    self._cache_under_common_names(req_name, c)
                else:
                    final_missing.append(req_name)

        self._write()
        return found, final_missing