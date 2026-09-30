from settings_store import load_accounts, load_settings

_SETTINGS = load_settings()

# Telegram user API
API_ID = int(_SETTINGS.get("telegram_api_id") or 0)
API_HASH = str(_SETTINGS.get("telegram_api_hash") or "")

# Telegram Bot (optional remote control panel)
BOT_TOKEN = str(_SETTINGS.get("bot_token") or "")
BOT_PANEL_ENABLED = bool(_SETTINGS.get("bot_panel_enabled", True))

# ---- Мультиаккаунт ---------------------------------------------------
ACCOUNTS = load_accounts()

# ---- Источник каналов -----------------------------------------------
AUTO_DISCOVER = bool(_SETTINGS.get("auto_discover", True))
CHANNELS = list(_SETTINGS.get("channels") or [])
EXCLUDE_CHANNELS = list(_SETTINGS.get("exclude_channels") or [])
SKIP_PRIVATE_CHANNELS = bool(_SETTINGS.get("skip_private_channels", True))
KEYWORDS = list(_SETTINGS.get("keywords") or [])

# ---- Фильтр трагических тем -----------------------------------------
DEATH_KEYWORDS = [
    "200", "300", "груз 200",
    "умер", "умерла", "умерли",
    "скончался", "скончалась", "скончались",
    "смерть", "смерти", "смертельный", "смертельно",
    "мертв", "мертва", "мертвы", "мёртв", "мёртвый",
    "жертвы", "жертв",
    "похороны", "похоронен", "похоронена", "захоронение",
    "траур", "вечная память",
    "труп", "трупы",
    "самоубийство", "суицид",
    "казнён", "казнена",
]

DEATH_STEM_KEYWORDS = [
    "погиб",
    "пострада",
    "ранен",
    "ранение", "ранения",
    "травмир",
    "обстрел",
    "обстреля",
    "минирован",
    "взрыв",
    "теракт",
    "катастроф",
    "авария со смерт",
]

# AI provider
AI_PROVIDER = str(_SETTINGS.get("ai_provider") or "ollama").strip().lower()

# Ollama
OLLAMA_BASE_URL = str(_SETTINGS.get("ollama_base_url") or "http://127.0.0.1:11434")
OLLAMA_MODEL = str(_SETTINGS.get("ollama_model") or "qwen3:8b")
OLLAMA_TIMEOUT = int(_SETTINGS.get("ollama_timeout") or 180)

# OpenAI API
OPENAI_API_KEY = str(_SETTINGS.get("openai_api_key") or "")
OPENAI_BASE_URL = str(_SETTINGS.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = str(_SETTINGS.get("openai_model") or "gpt-5.6-luna")
OPENAI_TIMEOUT = int(_SETTINGS.get("openai_timeout") or 120)
MAX_POST_CHARS = int(_SETTINGS.get("max_post_chars") or 6000)
MAX_COMMENT_LENGTH = int(_SETTINGS.get("max_comment_length") or 400)

# Настраиваемый стиль AI
AI_PERSONA = str(_SETTINGS.get("ai_persona") or "Обычный пользователь Telegram")
AI_TONE = str(_SETTINGS.get("ai_tone") or "Дружелюбный")
AI_EMOJI_MODE = str(_SETTINGS.get("ai_emoji_mode") or "Иногда")
AI_CUSTOM_PROMPT = str(_SETTINGS.get("ai_custom_prompt") or "")

# Safety / rate limiting
MAX_PENDING_ITEMS = int(_SETTINGS.get("max_pending_items") or 50)
AI_REQUEST_INTERVAL = int(_SETTINGS.get("ai_request_interval") or 10)
AI_MAX_POST_CHARS = int(_SETTINGS.get("ai_max_post_chars") or 1500)
MAX_QUEUE_SIZE = int(_SETTINGS.get("ai_queue_size") or 75)

# Ротация каналов
BATCH_SIZE = int(_SETTINGS.get("batch_size") or 50)
ROTATE_INTERVAL = int(_SETTINGS.get("rotate_interval") or 1800)
DISCOVER_INTERVAL = int(_SETTINGS.get("discover_interval") or 21600)

# Догоняющий обход
BACKFILL_LIMIT = int(_SETTINGS.get("backfill_limit") or 3)
MAX_BACKFILL_AGE_HOURS = int(_SETTINGS.get("max_backfill_age_hours") or 48)

# Автовыход из забаненных/неактивных каналов
AUTO_LEAVE_BANNED_CHANNELS = bool(_SETTINGS.get("auto_leave_banned_channels", True))
AUTO_LEAVE_INACTIVE_DAYS = int(_SETTINGS.get("auto_leave_inactive_days") or 0)
INACTIVE_CHECK_INTERVAL = int(_SETTINGS.get("inactive_check_interval") or 86400)

# Способ входа
LOGIN_MODE = str(_SETTINGS.get("telegram_login_mode") or "qr").strip().lower()

# Автопилот
AUTOPILOT_DELAY = int(_SETTINGS.get("autopilot_delay") or 90)