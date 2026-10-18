import hashlib
import json
from pathlib import Path


# Successful model answers on disk, keyed by everything that shapes them: a rerun after a 429, or new cases, only pays for what is missing.
class CallCache:

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, dict] | None = None

    @staticmethod
    def key(model: str, temperature: float | None, prompt: str, run: int) -> str:
        content = json.dumps([model, temperature, prompt, run])
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, dict]:
        if self._entries is None:
            self._entries = {}
            if self._path.exists():
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        entry = json.loads(line)
                        self._entries[entry["key"]] = entry["result"]
        return self._entries

    def get(self, key: str) -> dict | None:
        return self._load().get(key)

    # Errors are never cached, so a failed call is retried on the next run.
    def put(self, key: str, result: dict) -> None:
        if result.get("error"):
            return
        self._load()[key] = result
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as cache_file:
            cache_file.write(json.dumps({"key": key, "result": result}) + "\n")
