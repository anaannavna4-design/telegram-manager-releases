from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from dotenv import load_dotenv

from app_paths import accounts_path, settings_path, source_dir


DEFAULT_SETTINGS = {
    "telegram_api_id": 0,
    "telegram_api_hash": "",
    "bot_token": "",
    "bot_panel_enabled": True,
    "auto_discover": True,
    "skip_private_channels": True,
    "channels": [],
    "exclude_channels": [],
    "keywords": [],
    "ai_provider": "ollama",
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3:8b",
    "ollama_timeout": 180,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-5.6-luna",
    "openai_timeout": 120,
    "max_post_chars": 6000,
    "max_comment_length": 400,
    "max_pending_items": 50,
    "ai_request_interval": 10,
    "ai_max_post_chars": 1500,
    "ai_queue_size": 75,
    "batch_size": 50,
    "rotate_interval": 1800,
    "discover_interval": 21600,
    "backfill_limit": 3,
    "max_backfill_age_hours": 48,
    "auto_leave_banned_channels": True,
    "auto_leave_inactive_days": 30,
    "inactive_check_interval": 86400,
    "telegram_login_mode": "qr",
    "autopilot_delay": 90,
    "ai_persona": "Обычный пользователь Telegram",
    "ai_tone": "Дружелюбный",
    "ai_emoji_mode": "Иногда",
    "ai_custom_prompt": "",
}


def _read_json(path: Path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    except (OSError, json.JSONDecodeError):
        pass
    return deepcopy(default)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _legacy_accounts_from_env() -> list[dict]:
    result = []
    raw_all = os.getenv("ACCOUNTS", "")
    for raw in raw_all.split("|"):
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(";")]
        if len(parts) != 4:
            continue
        name, admin_id, session, autopilot = parts
        if not admin_id.isdigit():
            continue
        result.append({
            "name": name,
            "admin_id": int(admin_id),
            "session": session or f"neurocomment_{name}",
            "autopilot": autopilot.lower() in {"1", "true", "yes", "on"},
        })
    return result


def _apply_legacy_env(settings: dict) -> dict:
    # Backward-compatible import from the old .env. JSON remains the source of
    # truth after the first save in the Desktop UI.
    env_map = {
        "telegram_api_hash": ("TELEGRAM_API_HASH", str),
        "bot_token": ("BOT_TOKEN", str),
        "ollama_base_url": ("OLLAMA_BASE_URL", str),
        "ollama_model": ("OLLAMA_MODEL", str),
        "openai_api_key": ("OPENAI_API_KEY", str),
        "openai_base_url": ("OPENAI_BASE_URL", str),
        "openai_model": ("OPENAI_MODEL", str),
    }
    for key, (env_name, cast) in env_map.items():
        raw = os.getenv(env_name)
        if raw not in (None, ""):
            settings[key] = cast(raw)

    if not settings.get("telegram_api_id"):
        settings["telegram_api_id"] = _env_int("TELEGRAM_API_ID", 0)

    int_map = {
        "ollama_timeout": "OLLAMA_TIMEOUT",
        "openai_timeout": "OPENAI_TIMEOUT",
        "ai_request_interval": "AI_REQUEST_INTERVAL",
        "ai_max_post_chars": "AI_MAX_POST_CHARS",
        "ai_queue_size": "AI_QUEUE_SIZE",
        "batch_size": "BATCH_SIZE",
        "rotate_interval": "ROTATE_INTERVAL",
        "discover_interval": "DISCOVER_INTERVAL",
        "backfill_limit": "BACKFILL_LIMIT",
        "max_backfill_age_hours": "MAX_BACKFILL_AGE_HOURS",
        "auto_leave_inactive_days": "AUTO_LEAVE_INACTIVE_DAYS",
        "inactive_check_interval": "INACTIVE_CHECK_INTERVAL",
        "autopilot_delay": "AUTOPILOT_DELAY",
    }
    for key, env_name in int_map.items():
        if os.getenv(env_name) is not None:
            settings[key] = _env_int(env_name, int(settings[key]))

    bool_map = {
        "auto_discover": "AUTO_DISCOVER",
        "skip_private_channels": "SKIP_PRIVATE_CHANNELS",
        "auto_leave_banned_channels": "AUTO_LEAVE_BANNED_CHANNELS",
    }
    for key, env_name in bool_map.items():
        if os.getenv(env_name) is not None:
            settings[key] = _env_bool(env_name, bool(settings[key]))

    if os.getenv("TELEGRAM_LOGIN_MODE"):
        settings["telegram_login_mode"] = os.getenv("TELEGRAM_LOGIN_MODE", "qr").strip().lower()
    if os.getenv("AI_PROVIDER"):
        settings["ai_provider"] = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    return settings


def load_settings() -> dict:
    load_dotenv(source_dir() / ".env")
    path = settings_path()
    exists = path.exists()
    data = _read_json(path, DEFAULT_SETTINGS)
    merged = deepcopy(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update(data)
    if not exists:
        merged = _apply_legacy_env(merged)
    return merged


def save_settings(settings: dict) -> None:
    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update(settings or {})
    _write_json(settings_path(), merged)


def load_accounts() -> list[dict]:
    load_dotenv(source_dir() / ".env")
    path = accounts_path()
    data = _read_json(path, [])
    if path.exists() and isinstance(data, list):
        return data
    return _legacy_accounts_from_env()


def save_accounts(accounts: list[dict]) -> None:
    normalized = []
    for item in accounts or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        try:
            admin_id = int(item.get("admin_id", 0))
        except (TypeError, ValueError):
            admin_id = 0
        normalized.append({
            "name": name,
            "admin_id": admin_id,
            "session": str(item.get("session") or f"neurocomment_{name}").strip(),
            "autopilot": bool(item.get("autopilot", False)),
        })
    _write_json(accounts_path(), normalized)