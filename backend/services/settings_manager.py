"""Settings manager — persists user preferences to data/settings.json.

Implements secret masking for jina_api_key: the stored value is never
returned raw by get_settings(); only a masked placeholder is sent to
the frontend.  If the masked placeholder is received back on update,
the existing stored secret is retained unchanged.
"""

from pathlib import Path
from typing import Any, Dict

from backend.paths import SETTINGS_FILE
from backend.services.storage import write_json_atomic, read_json_safe

_JINA_KEY_MASK = "sk-••••••••"

DEFAULT_SETTINGS: Dict[str, Any] = {
    # Crawler defaults
    "max_pages": 200,
    "max_depth": 5,
    "concurrent": 3,
    "delay": 1.5,
    "max_time": 30,
    "respect_robots": True,
    # Pipeline stage toggles
    "enable_crawl4ai": True,
    "enable_scrapling": True,
    "enable_jina": True,
    "enable_curl_tls": True,
    "enable_curl_rotated": True,
    "enable_httpx": True,
    # External keys (stored but never returned raw)
    "jina_api_key": "",
    # UI preferences
    "theme": "light",
    "polling_interval": 2,
}


class SettingsManager:
    def __init__(self, storage_file: Path = SETTINGS_FILE):
        self.storage_file = Path(storage_file)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _load_raw(self) -> Dict[str, Any]:
        """Load settings merged with defaults (raw — includes real jina_api_key)."""
        saved = read_json_safe(self.storage_file, {})
        merged = {**DEFAULT_SETTINGS, **saved}
        return merged

    def _save(self, data: Dict[str, Any]):
        write_json_atomic(self.storage_file, data)

    # ── Public API ──────────────────────────────────────────────────────────

    def get_settings(self) -> Dict[str, Any]:
        """Return current settings with jina_api_key redacted."""
        raw = self._load_raw()
        result = {k: v for k, v in raw.items() if k != "jina_api_key"}
        # Replace with safe summary
        has_key = bool(raw.get("jina_api_key", ""))
        result["has_jina_key"] = has_key
        result["jina_key_masked"] = _JINA_KEY_MASK if has_key else ""
        return result

    def update_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and persist settings updates.

        If the masked placeholder is submitted for jina_api_key, the existing
        stored secret is retained.  Returns masked settings.
        """
        raw = self._load_raw()

        allowed_keys = set(DEFAULT_SETTINGS.keys())
        for key, value in updates.items():
            if key not in allowed_keys:
                continue
            # Secret retention: don't overwrite with the mask
            if key == "jina_api_key":
                if value == _JINA_KEY_MASK or value == "":
                    continue  # keep existing
                raw[key] = str(value)
                continue
            # Basic type coercion / validation
            expected_type = type(DEFAULT_SETTINGS[key])
            try:
                if expected_type == bool:
                    raw[key] = bool(value)
                elif expected_type == int:
                    raw[key] = int(value)
                elif expected_type == float:
                    raw[key] = float(value)
                else:
                    raw[key] = value
            except (ValueError, TypeError):
                pass  # skip invalid values

        self._save(raw)
        return self.get_settings()

    def reset_settings(self) -> Dict[str, Any]:
        """Reset all settings to factory defaults."""
        self._save(dict(DEFAULT_SETTINGS))
        return self.get_settings()

    def get_effective_pipeline_config(self) -> Dict[str, Any]:
        """Return pipeline-relevant settings with the real jina_api_key.

        Used by the crawl runner to build PipelineConfig — never sent to frontend.
        """
        raw = self._load_raw()
        return {
            "timeout": 30,
            "enable_crawl4ai": raw.get("enable_crawl4ai", True),
            "enable_scrapling": raw.get("enable_scrapling", True),
            "enable_jina": raw.get("enable_jina", True),
            "enable_curl_tls": raw.get("enable_curl_tls", True),
            "enable_curl_rotated": raw.get("enable_curl_rotated", True),
            "enable_httpx": raw.get("enable_httpx", True),
            "jina_api_key": raw.get("jina_api_key", ""),
        }

    def get_effective_crawler_config(self) -> Dict[str, Any]:
        """Return crawler-relevant settings for use as crawl defaults."""
        raw = self._load_raw()
        return {
            "max_pages": raw.get("max_pages", DEFAULT_SETTINGS["max_pages"]),
            "max_depth": raw.get("max_depth", DEFAULT_SETTINGS["max_depth"]),
            "concurrent": raw.get("concurrent", DEFAULT_SETTINGS["concurrent"]),
            "delay": raw.get("delay", DEFAULT_SETTINGS["delay"]),
            "max_time": raw.get("max_time", DEFAULT_SETTINGS["max_time"]),
            "no_robots": not raw.get("respect_robots", True),
        }


settings_manager = SettingsManager()
