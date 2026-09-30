import difflib
import json
import re
import time
import urllib.error
import urllib.request

from config import (
    OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, MAX_COMMENT_LENGTH,
    AI_MAX_POST_CHARS, DEATH_KEYWORDS, DEATH_STEM_KEYWORDS,
    AI_PERSONA, AI_TONE, AI_EMOJI_MODE, AI_CUSTOM_PROMPT,
    AI_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_TIMEOUT,
)

TONE_INSTRUCTIONS = {
    "Дружелюбный": (
        "Пиши тепло, доброжелательно и естественно. Допустима лёгкая эмоциональная реакция, "
        "но без чрезмерного восторга, фамильярности и пустых комплиментов."
    ),
    "Разговорный": (
        "Пиши как обычный живой пользователь Telegram: просто, непринуждённо и по-человечески. "
        "Допустимы разговорные конструкции, но без грубости, сленгового перегиба и искусственной театральности."
    ),
    "Нейтральный": (
        "Пиши спокойно, сдержанно и по существу. Не фамильярничай, не переигрывай эмоции "
        "и не добавляй оценок, которых нет в исходном посте."
    ),
    "Серьёзный": (
        "Пиши сдержанно и серьёзно. Не шути, не используй игривые формулировки и избегай эмодзи, "
        "кроме случаев, когда они действительно уместны и разрешены общей настройкой."
    ),
    "Ироничный": (
        "Допустима только лёгкая доброжелательная ирония, если тема поста сама по себе несерьёзная. "
        "Не используй сарказм, насмешки над людьми или язвительность."
    ),
}


def _build_system_prompt() -> str:
    emoji_rule = {
        "Никогда": "Не используй эмодзи.",
        "Часто": "Можно использовать один уместный эмодзи почти в каждом комментарии.",
    }.get(AI_EMOJI_MODE, "Эмодзи используй иногда, максимум один в конце.")

    tone_rule = TONE_INSTRUCTIONS.get(
        AI_TONE,
        "Пиши естественно и уместно по отношению к содержанию исходного поста.",
    )
    custom = AI_CUSTOM_PROMPT.strip()
    custom_block = f"\nДополнительные пожелания владельца:\n{custom}\n" if custom else ""

    # Совместимость со старыми настройками, где роль могла называться
    # «Девушка Аня». Имя не должно становиться отдельным персонажем.
    persona = re.sub(
        r"\b(?:аня|анна|анечка|анюта)\b", "", str(AI_PERSONA), flags=re.IGNORECASE
    )
    persona = re.sub(r"\s{2,}", " ", persona).strip(" ,—-") or "Обычный пользователь Telegram"

    return f"""Ты пишешь короткие комментарии к постам в Telegram. Твой ответ — всегда только готовый комментарий, ничего кроме него.

Роль/образ: {persona}.
Тон: {AI_TONE}.
Правило выбранного тона: {tone_rule}
1-2 коротких предложения. {emoji_rule}
{custom_block}
ВАЖНО ПРО РОЛЬ/ОБРАЗ:
— роль задаёт только манеру письма и не является отдельным собеседником;
— автор комментария безымянный: никогда не называй себя Аней, Анной или любым другим именем;
— никогда не обращайся к самой себе и не веди диалог со своим образом;
— если в поле «Роль/образ» указано имя, используй только стиль этой роли, а само имя полностью игнорируй;
— не придумывай собеседника и не начинай комментарий с обращения к своему персонажу.

СТРОГИЙ ЗАПРЕТ ТЕМ — ВСЕГДА SKIP, БЕЗ ИСКЛЮЧЕНИЙ:
— посты о погибших, умерших, раненых, пострадавших;
— военные потери, коды «200», «300», «груз 200»;
— обстрелы, атаки беспилотников, взрывы, теракты, катастрофы, ДТП с жертвами.
Если пост хотя бы косвенно о таких событиях — ответь ровно одним словом: SKIP.
Никогда не пиши лёгких, шутливых или оценочных комментариев к таким постам.
Никогда не сравнивай ценность людей и вещей.

Для остальных постов:
— комментарий должен быть логично связан с одной конкретной деталью поста;
— не пересказывай пост;
— не используй бессмысленные сравнения вроде «как в кино» или «словно в сказке»;
— избегай пустых фраз, которые подходят к любому посту;
— отвечай осмысленным текстом на русском языке;
— не начинай ответы одинаково.

Если по посту совсем нечего сказать, ответь одним словом: SKIP.
"""


SYSTEM_PROMPT = _build_system_prompt()

# Блоки размышлений, которые qwen3 иногда вставляет в ответ.
THINK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)

# Фильтр трагических тем — собирается из двух списков config.py:
# целые слова + основы (ловят любые формы слов).
def _build_topic_re():
    patterns = []
    patterns += [rf"\b{re.escape(kw.lower())}\b" for kw in DEATH_KEYWORDS]
    patterns += [re.escape(s.lower()) for s in DEATH_STEM_KEYWORDS]
    if patterns:
        return re.compile("|".join(patterns), re.IGNORECASE)
    return None

TOPIC_RE = _build_topic_re()

# Паттерны рассуждений и пересказов — ответ с ними бракуется.
BAD_RE = re.compile(
    r"(прочит\w+|читаю|читал\w*|тут про|в этом посте|в посте |пост (о|про|рассказывает)|"
    r"рассказыва\w+|говорится|идёт речь|видимо, что|судя по|"
    r"мне нужно|мне надо|нужно (создать|написать|составить|прочитать)|надо (создать|написать)|"
    r"создам|составлю|напишу|придумал\w*|комментарий (к|для|про))",
    re.IGNORECASE,
)

# Натянутые сравнения — признак бреда маленьких моделей.
NONSENSE_RE = re.compile(
    r"(как будто в \w+|словно в \w+|будто в \w+|как в кино|как в сказке|как в сериале)",
    re.IGNORECASE,
)

# Циничные/несочувственные формулировки о людях — брак всегда.
INSENSITIVE_RE = re.compile(
    r"(хотя бы (?!пост)\S*?(уцелел|цел|остал\w+)|сорян|сочувствую, но|но машины|"
    r"главное, что (машины|имущество|деньги))",
    re.IGNORECASE,
)

# Вводные слова в начале («Хорошо, ...»).
FILLER_RE = re.compile(
    r"^(хорошо|ок(?:ей)?|ладно|давай(?:те)?|ну что ж|итак)[\s,.!-]+",
    re.IGNORECASE,
)

# Защита от старого персонажа «Аня/Анна». В ранних версиях NeuroComment
# имя было частью системного промпта, и локальная модель иногда начинала
# разговаривать со своим же образом: «Анна, почему ты ...?».
# Такие ответы отбрасываются до попадания в очередь/автопилот.
LEGACY_SELF_NAMES = ("аня", "анна", "анечка", "анюта", "ань")
SELF_ADDRESS_RE = re.compile(
    r"(?:^|[.!?]\s+)(?:(?:ну|ой|эх|слушай|смотри)\s*[,—-]?\s*)?"
    r"(?:аня|анна|анечка|анюта|ань)\s*[,!:—-]",
    re.IGNORECASE,
)
SELF_REFERENCE_RE = re.compile(
    r"\b(?:я\s*[,—-]\s*(?:аня|анна|анечка|анюта)|"
    r"(?:меня\s+зовут\s+|я\s*[-—]\s*)(?:аня|анна|анечка|анюта))\b",
    re.IGNORECASE,
)


def _mentions_own_persona(text: str) -> bool:
    """True, если ответ обращается к старому/собственному персонажу.

    Мы намеренно не запрещаем любое упоминание имени «Анна» внутри текста:
    оно может относиться к человеку из исходного поста. Отсекаются именно
    формы обращения к персонажу и явное самоименование.
    """
    return bool(SELF_ADDRESS_RE.search(text) or SELF_REFERENCE_RE.search(text))


def _is_garbage(text: str) -> bool:
    """Мусорные ответы вырожденной модели: «00000000000», «@@@@@@», «....»."""
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True

    letters = [ch for ch in stripped if ch.isalpha()]
    letter_ratio = len(letters) / len(stripped)
    if letter_ratio < 0.3:
        return True

    unique = len(set(stripped.lower()))
    if len(stripped) >= 5 and unique <= 2:
        return True

    parts = stripped.split()
    if len(parts) == 1 and parts[0].isdigit():
        return True

    return False


# ---- Защита от повторяющихся начал и шаблонных комментариев ----------

RECENT_MAX = 15
_RECENT_STARTS: list[str] = []
_RECENT_COMMENTS: list[str] = []


def _first_word(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return ""
    return parts[0].lower().strip(",.!…?\"'«»()")


def _too_repetitive_start(text: str) -> bool:
    word = _first_word(text)
    if not word:
        return False
    return _RECENT_STARTS[-3:].count(word) >= 2


def _too_similar_to_recent(text: str) -> bool:
    """True — комментарий почти повторяет недавний (шаблонный штамп)."""
    low = text.lower().strip()
    for old in _RECENT_COMMENTS[-RECENT_MAX:]:
        ratio = difflib.SequenceMatcher(None, low, old.lower().strip()).ratio()
        if ratio > 0.65:
            return True
    return False


def _remember(text: str):
    word = _first_word(text)
    if word:
        _RECENT_STARTS.append(word)
        if len(_RECENT_STARTS) > RECENT_MAX:
            del _RECENT_STARTS[:len(_RECENT_STARTS) - RECENT_MAX]
    _RECENT_COMMENTS.append(text)
    if len(_RECENT_COMMENTS) > RECENT_MAX:
        del _RECENT_COMMENTS[:len(_RECENT_COMMENTS) - RECENT_MAX]


def _ollama_request(payload: dict) -> dict:
    url = OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(
            f"Ollama вернула ошибку HTTP {exc.code}: {body}. "
            f"Проверь, что модель скачана: ollama list"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Не удалось подключиться к Ollama. Убедись, что Ollama запущена "
            f"и доступна по адресу {OLLAMA_BASE_URL}. Ошибка: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama вернула некорректный ответ.") from exc


def _extract_openai_text(response: dict) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                value = content.get("text", "")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
    return "\n".join(parts).strip()


def _openai_request(user_content: str, shake: int = 0) -> str:
    if not OPENAI_API_KEY.strip():
        raise RuntimeError("OpenAI API Key не задан.")
    if not OPENAI_MODEL.strip():
        raise RuntimeError("Модель OpenAI не задана.")

    if shake:
        user_content += (
            "\n\nЭто повторная попытка. Дай заметно другой вариант комментария, "
            "сохраняя смысл, выбранный тон и все ограничения."
        )

    payload = {
        "model": OPENAI_MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": user_content,
        "max_output_tokens": 180,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_BASE_URL.rstrip("/") + "/responses",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY.strip()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI API вернул HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к OpenAI API: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI API вернул некорректный JSON.") from exc

    text = _extract_openai_text(parsed)
    if not text:
        raise RuntimeError("OpenAI API не вернул текст комментария.")
    return text


def _clean(text: str) -> str:
    """Очистка: think-блоки, кавычки, вводные слова, мусорные повторы."""
    text = THINK_RE.sub("", text).strip()
    if len(text) >= 2 and text[0] in "\"«'" and text[-1] in "\"»'":
        text = text[1:-1].strip()
    text = re.sub(r"^(?:([^\w\s])\1{3,})+", "", text).strip()
    text = FILLER_RE.sub("", text).strip()
    return text


def _ask_model(post_text: str, shake: int = 0) -> str:
    """Один запрос. Возвращает чистый комментарий, 'SKIP' или '' (брак)."""
    user_content = (
        f"Напиши комментарий к посту, как в примерах выше. "
        f"Зацепись за конкретную деталь поста.\n\n"
        f"Пост:\n{post_text}\n\n"
        f"Твой ответ (только комментарий, одной строкой):"
    )

    # Для коротких комментариев слишком высокая температура заметно повышает
    # шанс ролевых сбоев и странного само-диалога. Разнообразие сохраняем,
    # но на повторных попытках повышаем температуру плавно.
    temperatures = (0.75, 0.90, 1.00, 1.05)
    idx = max(0, min(int(shake), len(temperatures) - 1))
    options = {
        "temperature": temperatures[idx],
        "num_predict": 150,
        "num_ctx": 2048,
        "top_k": 50 + idx * 10,
        "repeat_penalty": 1.12 if idx >= 2 else 1.08,
    }

    t0 = time.time()
    if AI_PROVIDER == "openai":
        raw = _openai_request(user_content, shake=shake)
        provider_name = f"OpenAI/{OPENAI_MODEL}"
    else:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "1h",
            "options": options,
        }
        response = _ollama_request(payload)
        raw = response.get("message", {}).get("content", "")
        provider_name = f"Ollama/{OLLAMA_MODEL}"

    print(f"[AI-TIME] {provider_name}: генерация заняла {time.time() - t0:.1f} c")
    result = _clean(raw)

    if not result or result.upper() == "SKIP":
        return "SKIP"

    if _is_garbage(result):
        print(f"[AI] Брак (мусорный ответ модели): {result[:40]!r}")
        return ""

    if BAD_RE.search(result):
        print(f"[AI] Брак (рассуждение/пересказ): {result[:80]!r}")
        return ""

    if NONSENSE_RE.search(result):
        print(f"[AI] Брак (бессмысленное сравнение): {result[:80]!r}")
        return ""

    if INSENSITIVE_RE.search(result):
        print(f"[AI] Брак (бестактная формулировка): {result[:80]!r}")
        return ""

    if _mentions_own_persona(result):
        print(f"[AI] Брак (обращение к собственному образу): {result[:80]!r}")
        return ""

    if len(result) > MAX_COMMENT_LENGTH:
        result = result[:MAX_COMMENT_LENGTH].rsplit(" ", 1)[0] + "…"

    return result


def generate_comment(post_text: str) -> str:
    provider = (AI_PROVIDER or "ollama").strip().lower()
    if provider == "openai":
        if not OPENAI_API_KEY.strip():
            raise RuntimeError("Выбран OpenAI API, но API Key не задан.")
        if not OPENAI_MODEL.strip():
            raise RuntimeError("Выбран OpenAI API, но модель не задана.")
    elif provider == "ollama":
        if not OLLAMA_MODEL:
            raise RuntimeError("OLLAMA_MODEL не задан")
    else:
        raise RuntimeError(f"Неизвестный AI-провайдер: {AI_PROVIDER}")

    # Фильтр трагических тем — ДО запроса к модели (быстро и дёшево).
    if TOPIC_RE is not None and TOPIC_RE.search(post_text):
        print("[AI] Пост о смерти/жертвах/пострадавших — пропущен фильтром тем.")
        return "SKIP"

    post_text = post_text[:AI_MAX_POST_CHARS]

    last_valid = ""
    for attempt in (1, 2, 3, 4):
        result = _ask_model(post_text, shake=attempt - 1)
        if result == "SKIP":
            return "SKIP"
        if not result:
            print(f"[AI] Попытка {attempt}: бракованный ответ — пробую ещё раз.")
            continue
        if _too_repetitive_start(result):
            print(f"[AI] Попытка {attempt}: повторяющееся начало {_first_word(result)!r} — пробую ещё раз.")
            last_valid = result
            continue
        if _too_similar_to_recent(result):
            print(f"[AI] Попытка {attempt}: шаблонный комментарий — пробую ещё раз.")
            last_valid = result
            continue
        _remember(result)
        return result

    if last_valid and not _is_garbage(last_valid):
        _remember(last_valid)
        return last_valid
    return "SKIP"