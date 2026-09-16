#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор. Группа -240273450. Посты в 09:00 и 15:00.
Типы: новость, лайфхак, викторина, инструмент, промпт, миф/факт, вопрос.
Два VK-токена: VK_TOKEN_AI (групповой, для wall.post) и VK_TOKEN_USER (пользовательский, для фото).
"""

import os, sys, time, random, hashlib, logging, requests, schedule, re, threading
from pathlib import Path
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import feedparser, vk_api
from vk_api.upload import VkUpload
from vk_api.exceptions import ApiError

def _env(*names, default=""):
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return default

# ================== НАСТРОЙКИ ==================
BOT_NAME   = "AI Навигатор"
GROUP_ID   = int(os.getenv("AI_GROUP_ID", "-240273450"))
POST_TIMES = ["09:00", "15:00"]

# Два токена VK: групповой (для постинга) и пользовательский (для фото)
VK_TOKEN        = _env("VK_TOKEN_AI", "AI_VK_TOKEN", "VK_TOKEN")           # групп. — wall.post
VK_TOKEN_USER   = _env("VK_TOKEN_USER", "USER_VK_TOKEN")                   # польз. — photo_wall

HUGGINGFACE_KEY = _env("HUGGINGFACE_API_KEY", "HF_API_KEY", "HF_TOKEN")
PEXELS_KEY      = _env("PEXELS_API_KEY", "PEXELS_TOKEN")
GROQ_API_KEY    = _env("GROQ_API_KEY", "GROQ_KEY")

TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN", "AI_TELEGRAM_TOKEN", "TG_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "AI_CHAT_ID")

# ================== ТИПЫ ПОСТОВ ==================
POST_TYPE_WEIGHTS = {
    "news":     40,
    "tip":      12,
    "quiz":     12,
    "tool":     10,
    "prompt":   10,
    "myth":      8,
    "question":  8,
}
QUIZ_ANSWER_IN_COMMENT = True
QUIZ_ANSWER_DELAY_SEC = 5

# ================== RSS ==================
RSS_ENABLED = True
RSS_SOURCES = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru",
    "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru",
    "https://www.computerworld.com/index.rss",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
]
DATA_DIR = "./data"

TOPIC_BLACKLIST = [
    "бесплатных уроков", "бесплатный урок", "вебинар", "вебинары",
    "дайджест", "подборка", "подборку", "топ-", "top-",
    "курс", "курсы", "обучение", "школа", "интенсив",
    "скидк", "акци", "распродаж", "промокод",
    "митап", "конференц", "хакатон", "контест",
    "вакансия", "резюме", "ищу работу", "ищем сотрудника",
    "запись трансляции", "запись вебинара",
    "итоги месяца", "итоги недели", "дайджест сентября",
    "новый стек, новые задачи",
]
# ================================================================


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [AI] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("AI")
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

STATE = {
    "last_post_topic": None,
    "last_post_time": None,
    "last_post_ok": None,
    "topics_count": 0,
    "last_source": None,
    "last_gen": None,
    "last_img_source": None,
    "last_type": None,
    "type_counters": {k: 0 for k in POST_TYPE_WEIGHTS},
}
STATE_LOCK = threading.Lock()

# ================== ТЕЛЕГРАМ ==================
def tg_api(method, **params):
    if not TELEGRAM_TOKEN: return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
        return requests.post(url, json=params, timeout=20).json()
    except Exception as e:
        logger.debug(f"TG {method}: {e}"); return None

def tg_init():
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN:
        logger.info("📨 Telegram: токен не задан"); return None
    me = tg_api("getMe")
    if not me or not me.get("ok"):
        logger.warning("📨 Telegram: токен невалиден"); return None
    bot_username = me["result"].get("username", "?")
    logger.info(f"📨 Telegram: бот @{bot_username}")
    tg_api("deleteWebhook")
    if TELEGRAM_CHAT_ID:
        logger.info(f"📨 Telegram chat_id из env: {TELEGRAM_CHAT_ID}")
        return TELEGRAM_CHAT_ID
    upd = tg_api("getUpdates", timeout=1)
    if upd and upd.get("ok") and upd.get("result"):
        for u in reversed(upd["result"]):
            msg = u.get("message") or u.get("edited_message") or {}
            cid = msg.get("chat", {}).get("id")
            if cid:
                TELEGRAM_CHAT_ID = str(cid)
                logger.info(f"📨 Telegram chat_id найден: {TELEGRAM_CHAT_ID}")
                return TELEGRAM_CHAT_ID
    logger.warning(f"📨 Telegram: chat_id неизвестен — напиши /start боту @{bot_username}")
    return None

def tg_send(text, chat_id=None):
    if not TELEGRAM_TOKEN: return
    cid = chat_id or TELEGRAM_CHAT_ID
    if not cid: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": cid, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
    except Exception as e:
        logger.debug(f"TG send: {e}")

# ================== ДИАГНОСТИКА ==================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

def diag_check_groq():
    if not GROQ_API_KEY:
        return "❌ Groq: ключ НЕ задан в переменных окружения"
    for model in GROQ_MODELS:
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": "Скажи одно слово: ok"}],
                      "max_tokens": 10},
                timeout=30)
            if r.status_code == 200:
                answer = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return f"✅ Groq: OK (модель {model}, ответ: {answer[:40]})"
            elif r.status_code == 401:
                return "❌ Groq: 401 — ключ неверный или отозван"
            elif r.status_code == 429:
                return "⚠️ Groq: 429 — лимит, пробую следующую"
            elif r.status_code == 404:
                continue
        except requests.exceptions.ConnectionError as e:
            return f"❌ Groq: нет соединения ({str(e)[:100]})"
        except Exception as e:
            return f"❌ Groq: {type(e).__name__}: {str(e)[:150]}"
    return "❌ Groq: ни одна модель недоступна"

def diag_check_vk(token, label):
    if not token:
        return f"❌ {label}: токен НЕ задан"
    try:
        r = requests.get("https://api.vk.com/method/users.get",
                         params={"access_token": token, "v": "5.199"}, timeout=15)
        d = r.json()
        if "response" in d:
            return f"✅ {label}: OK (id={d['response'][0].get('id')})"
        err = d.get("error", {})
        return f"❌ {label}: {err.get('error_code')} — {err.get('error_msg')}"
    except Exception as e:
        return f"❌ {label}: {type(e).__name__}: {str(e)[:100]}"

def diag_check_hf():
    if not HUGGINGFACE_KEY:
        return "⚠️ HF: ключ не задан"
    try:
        r = requests.get("https://huggingface.co/api/whoami-v2",
                         headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"}, timeout=15)
        if r.status_code == 200:
            name = r.json().get("name", "?")
            return f"✅ HF: OK ({name})"
        return f"❌ HF: HTTP {r.status_code}"
    except requests.exceptions.ConnectionError:
        return "⚠️ HF: недоступен (DNS/сеть)"
    except Exception as e:
        return f"❌ HF: {str(e)[:80]}"

def diag_check_pexels():
    if not PEXELS_KEY:
        return "⚠️ Pexels: ключ не задан"
    try:
        r = requests.get("https://api.pexels.com/v1/search",
                         headers={"Authorization": PEXELS_KEY},
                         params={"query": "ai", "per_page": 1}, timeout=15)
        if r.status_code == 200:
            return "✅ Pexels: OK"
        return f"❌ Pexels: HTTP {r.status_code}"
    except Exception as e:
        return f"❌ Pexels: {str(e)[:80]}"

def diag_check_pollinations():
    try:
        r = requests.get("https://image.pollinations.ai/prompt/test?width=256&height=256&nologo=true",
                         timeout=30)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return "✅ Pollinations (img): OK"
        return f"⚠️ Pollinations: HTTP {r.status_code}, ct={r.headers.get('content-type')}"
    except Exception as e:
        return f"❌ Pollinations: {str(e)[:80]}"

def diag_check_rss():
    ok = 0
    total = len(RSS_SOURCES)
    lines = []
    for url in RSS_SOURCES:
        try:
            f = feedparser.parse(url)
            n = len(f.entries)
            if n > 0:
                ok += 1
                lines.append(f"  ✅ {url.split('/')[2]} — {n}")
            else:
                lines.append(f"  ⚠️ {url.split('/')[2]} — 0")
        except Exception as e:
            lines.append(f"  ❌ {url.split('/')[2]} — {str(e)[:60]}")
    return f"📡 RSS: {ok}/{total}\n" + "\n".join(lines)

def diag_full():
    lines = [
        f"🔧 <b>Диагностика [{BOT_NAME}]</b>", "",
        "<b>Переменные окружения:</b>",
        f"  GROQ_API_KEY: {'✅ есть' if GROQ_API_KEY else '❌ НЕТ'}",
        f"  VK_TOKEN_AI (групповой): {'✅ есть' if VK_TOKEN else '❌ НЕТ'}",
        f"  VK_TOKEN_USER (пользоват.): {'✅ есть' if VK_TOKEN_USER else '❌ НЕТ — фото не прикрепится'}",
        f"  HUGGINGFACE_API_KEY: {'✅ есть' if HUGGINGFACE_KEY else '⚠️ нет'}",
        f"  PEXELS_API_KEY: {'✅ есть' if PEXELS_KEY else '⚠️ нет'}",
        f"  TELEGRAM_TOKEN: {'✅ есть' if TELEGRAM_TOKEN else '❌ НЕТ'}", "",
        "<b>Проверка сервисов:</b>",
        diag_check_groq(),
        diag_check_vk(VK_TOKEN, "VK group"),
        diag_check_vk(VK_TOKEN_USER, "VK user"),
        diag_check_hf(),
        diag_check_pexels(),
        diag_check_pollinations(), "",
        diag_check_rss(),
    ]
    return "\n".join(lines)

def tg_command_loop():
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN: return
    logger.info("📨 Слушаю Telegram: /test, /status, /diag, /groq, /types, /help")
    offset = 0
    upd = tg_api("getUpdates", timeout=1)
    if upd and upd.get("ok") and upd.get("result"):
        offset = upd["result"][-1]["update_id"] + 1
        if not TELEGRAM_CHAT_ID:
            for u in reversed(upd["result"]):
                msg = u.get("message") or u.get("edited_message") or {}
                cid = msg.get("chat", {}).get("id")
                if cid:
                    TELEGRAM_CHAT_ID = str(cid); break
    while True:
        try:
            upd = tg_api("getUpdates", timeout=25, offset=offset)
            if not upd or not upd.get("ok"):
                time.sleep(5); continue
            for u in upd.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if not text: continue
                if not TELEGRAM_CHAT_ID and chat_id:
                    TELEGRAM_CHAT_ID = chat_id
                    tg_send("✅ chat_id сохранён.", chat_id=chat_id); continue
                if chat_id != str(TELEGRAM_CHAT_ID):
                    tg_send("⛔ Нет доступа.", chat_id=chat_id); continue

                parts = text.split()
                cmd = parts[0].lower().split("@")[0]
                logger.info(f"📨 Команда: {cmd}")

                if cmd in ("/start", "/help"):
                    tg_send(
                        f"🤖 <b>{BOT_NAME}</b>\n\n"
                        f"/test — пост (случайный тип)\n"
                        f"/test news — конкретный тип\n"
                        f"/diag — диагностика\n"
                        f"/groq — проверка Groq\n"
                        f"/status — состояние\n"
                        f"/types — счётчики\n\n"
                        f"Типы: {', '.join(POST_TYPE_WEIGHTS.keys())}"
                    )
                elif cmd == "/diag":
                    tg_send("🔧 Запускаю диагностику, подожди ~15 сек...")
                    threading.Thread(target=lambda: tg_send(diag_full()), daemon=True).start()
                elif cmd == "/groq":
                    tg_send("🔎 Проверяю Groq...")
                    def _groq_report():
                        status = diag_check_groq()
                        extra = ""
                        if status.startswith("✅"):
                            body = groq_chat(
                                "Отвечай кратко. Только русский.",
                                "Напиши одно короткое предложение про нейросети.", max_tokens=80)
                            if body:
                                extra = f"\n\n<b>Тестовый текст:</b>\n{body[:300]}"
                        tg_send(f"<b>Groq:</b> {status}{extra}")
                    threading.Thread(target=_groq_report, daemon=True).start()
                elif cmd == "/status":
                    with STATE_LOCK: s = dict(STATE)
                    counters = ", ".join(f"{k}:{v}" for k, v in s["type_counters"].items())
                    tg_send(
                        f"📊 <b>[{BOT_NAME}]</b>\n\n"
                        f"Тем: {s['topics_count']}\n"
                        f"Последний: {s['last_post_time'] or '—'}\n"
                        f"Тип: {s['last_type'] or '—'}\n"
                        f"Тема: {s['last_post_topic'] or '—'}\n"
                        f"Текст: {s['last_gen'] or '—'}\n"
                        f"Картинка: {s['last_img_source'] or '—'}\n"
                        f"Результат: {'✅' if s['last_post_ok'] else '❌' if s['last_post_ok'] is False else '—'}\n\n"
                        f"Счётчики: {counters}"
                    )
                elif cmd == "/types":
                    with STATE_LOCK: c = dict(STATE["type_counters"])
                    lines = "\n".join(f"  {k}: {v} раз" for k, v in c.items())
                    tg_send(f"📈 <b>Счётчики по типам</b>\n\n{lines}")
                elif cmd == "/test":
                    forced = parts[1].lower() if len(parts) > 1 else None
                    if forced and forced not in POST_TYPE_WEIGHTS:
                        tg_send(f"❓ Неизвестный тип. Доступные: {', '.join(POST_TYPE_WEIGHTS.keys())}")
                        continue
                    tg_send(f"🧪 Запускаю пост{' (' + forced + ')' if forced else ''}...")
                    threading.Thread(target=post_now, args=(forced,), daemon=True).start()
                else:
                    tg_send(f"❓ Не знаю <code>{cmd}</code>. Напиши /help")
        except Exception as e:
            logger.error(f"TG loop: {e}"); time.sleep(5)

# ================== ПЕРЕВОД ==================
def _is_ru(text):
    if not text: return False
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я')
    return cyr > len(text) * 0.3

def translate(text, source_lang, target_lang):
    if not text: return text
    try:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": text[:500], "langpair": f"{source_lang}|{target_lang}"},
                         timeout=20)
        if r.status_code == 200:
            t = r.json().get("responseData", {}).get("translatedText", "")
            if t and len(t) > 3: return t
    except Exception as e:
        logger.warning(f"Перевод: {e}")
    return text

def to_ru(t): return t if _is_ru(t) else translate(t, "en", "ru")
def to_en(t): return t if not _is_ru(t) else translate(t, "ru", "en")

# ================== ФИЛЬТР ТЕМ ==================
def is_topic_ok(title, summary):
    t = (title or "").lower()
    s = (summary or "").lower()[:300]
    for bad in TOPIC_BLACKLIST:
        if bad in t: return False
    promo = ["скидка", "промокод", "записаться", "регистрация",
             "бесплатно", "вебинар", "подпишись", "подписывайтесь"]
    if sum(1 for m in promo if m in s) >= 3: return False
    if t.endswith("?") and len(t) < 30: return False
    return True

# ================== ЗАПАСНЫЕ ТЕМЫ ==================
DEFAULT_TOPICS = [
    "Как использовать нейросети в повседневной жизни",
    "Что такое промпт-инжиниринг и зачем он нужен",
    "Как AI рисует картинки: простое объяснение",
    "Что такое мультимодальные модели",
    "Как обучают большие языковые модели",
    "Локальные нейросети: кому и зачем они нужны",
    "5 бесплатных AI-инструментов для работы и учёбы",
    "ChatGPT vs YandexGPT vs GigaChat: сравнение",
    "AI-переводчики: насколько они точны",
    "Голосовые ассистенты: обзор и сравнение",
    "Нейросети для создания презентаций и документов",
    "AI для дизайнеров: 7 полезных сервисов",
    "AI для программистов: что реально экономит время",
    "AI для маркетологов: генерация текстов и креативов",
    "Как AI экономит время в бизнесе: примеры",
    "Автоматизация рутины с помощью нейросетей",
    "Как AI помогает в поиске работы",
    "Как проверить, не сгенерирован ли текст нейросетью",
    "AI в образовании: польза и риски",
    "AI в медицине: что уже работает",
    "AI в финансах: риски и возможности",
    "AI для создания музыки: как нейросети сочиняют треки",
    "Топ-10 ошибок новичков при работе с AI",
    "Этика использования AI: что важно знать",
    "Как защитить свои данные при работе с чат-ботами",
    "Стоит ли бояться, что нейросети заменят профессии",
    "Что такое галлюцинации нейросетей",
    "Тренды AI на ближайший год",
    "Как объяснить ребёнку, что такое нейросети",
    "Как выбрать первый курс по ИИ",
]

TOPICS_TIP = [
    "Как формулировать промпты, чтобы получать точные ответы",
    "Как экономить токены в ChatGPT и не терять качество",
    "Как использовать AI для написания писем и сообщений",
    "Как проверять факты, которые выдаёт нейросеть",
    "Как перестать бояться AI и начать пользоваться им каждый день",
    "Как ускорить работу в 2 раза с помощью AI-помощника",
    "Как использовать AI для изучения иностранного языка",
    "Как подготовить презентацию за 15 минут с помощью AI",
    "Как делегировать рутину AI без потери качества",
    "Как собрать свой личный AI-стек инструментов",
]
TOPICS_TOOL = [
    "ChatGPT", "Claude", "Perplexity", "Midjourney", "Suno",
    "Notion AI", "Gamma", "Tome", "Otter.ai", "Runway",
    "ElevenLabs", "HeyGen", "CapCut AI", "Figma AI", "Napkin AI",
]
TOPICS_PROMPT = [
    "Как написать цепляющий заголовок для поста",
    "Как объяснить сложную тему простыми словами",
    "Как составить план статьи или выступления",
    "Как сделать резюме для конкретной вакансии",
    "Как придумать 10 идей для контента за 5 минут",
    "Как разобрать чужой текст и найти слабые места",
    "Как написать вежливый отказ или сложное письмо",
    "Как придумать название для проекта или продукта",
]
TOPICS_MYTH = [
    "AI заменит все профессии",
    "Нейросети всегда говорят правду",
    "AI думает как человек",
    "ChatGPT знает всё, что есть в интернете",
    "Нейросети крадут ваши данные, если вы им что-то пишете",
    "AI уже достиг уровня человеческого интеллекта",
    "Чтобы пользоваться AI, нужно программировать",
]
TOPICS_QUESTION = [
    "Каким AI-инструментом вы пользуетесь каждый день?",
    "Что бы вы делегировали нейросети, если бы могли?",
    "Какой AI-сервис вас разочаровал и почему?",
    "Что вас больше всего удивило в возможностях AI за последний год?",
    "Какая задача до сих пор не поддаётся AI, на ваш взгляд?",
    "О чём вы хотели бы узнать больше в нашем канале?",
]

# ================== КАРТИНКИ ==================
IMG_STYLES_EN = [
    "futuristic digital illustration of neural network, glowing connections",
    "minimalist tech design, clean gradients, abstract circuit",
    "abstract data visualization, glowing nodes and graphs",
    "realistic photo of humanoid AI assistant, soft light",
    "flat illustration of person interacting with AI, pastel colors",
    "cyberpunk style, neon lights, futuristic city",
    "isometric 3D illustration of tech objects",
    "watercolor abstraction on neural network theme",
    "low-poly 3D, geometric shapes, tech palette",
    "surreal collage of brain and microchips",
    "retro-futurism 80s synthwave palette",
    "minimalist: single glowing line of data",
]
IMG_COLORS_EN = [
    "cold blue-purple palette",
    "bright neon accents on dark background",
    "clean blue and white tones",
    "dark navy with cyan glow",
    "violet and pink gradient",
    "monochrome with one bright accent",
]
IMG_LIGHT_EN = ["soft diffuse glow", "bright contrast light",
                "neon lighting", "natural warm light"]
IMG_COMP_EN = ["close-up shot", "wide shot", "top-down view",
               "symmetrical composition", "rule of thirds"]
NEGATIVE_SUFFIX = (
    "high quality, detailed, 4k, sharp focus, "
    "no text, no watermark, no logo, no signature, no letters, no captions"
)

def build_image_prompt(topic_en):
    return (
        f"{topic_en}, {random.choice(IMG_STYLES_EN)}, "
        f"{random.choice(IMG_COLORS_EN)}, {random.choice(IMG_LIGHT_EN)}, "
        f"{random.choice(IMG_COMP_EN)}, {NEGATIVE_SUFFIX}"
    )

# Расширенный стоп-лист для Pexels
PEXELS_STOPWORDS = {
    "the","a","an","and","or","of","in","to","for","on","with","is","are",
    "was","were","be","been","has","have","had","new","how","why","what",
    "when","where","who","which","whose","whom","that","this","these","those",
    "ai","artificial","intelligence","machine","learning","neural","network",
    "your","you","my","me","we","us","our","their","his","her","its","it",
    "still","not","no","yes","can","will","would","could","should","may","might",
    "task","tasks","view","opinion","amenable","way","ways","thing","things",
    "good","best","better","much","many","more","most","some","any","all",
    "one","two","three","first","second","third","last","next","other","another",
    "just","only","also","even","very","really","quite","too","so","such",
    "make","makes","made","get","gets","got","use","uses","used","using",
    "need","needs","want","wants","like","likes","know","knows","think","thinks",
    "see","sees","look","looks","come","comes","go","goes","take","takes",
    "time","day","days","year","years","now","then","today","tomorrow","yesterday",
}

def pexels_query_from_topic(topic_en):
    """Извлекает значимые английские слова для запроса к Pexels."""
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", topic_en)  # минимум 4 буквы
    useful = [w.lower() for w in words if w.lower() not in PEXELS_STOPWORDS]
    # Убираем дубли, сохраняя порядок
    seen = set()
    uniq = []
    for w in useful:
        if w not in seen:
            seen.add(w); uniq.append(w)
    if not uniq:
        uniq = ["technology", "innovation"]
    return " ".join(uniq[:2])  # только 2 слова — точнее попадание

def gen_img_poll(prompt):
    try:
        seed = random.randint(1, 999999)
        url = (f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
               f"?width=1024&height=576&nologo=true&enhance=true&seed={seed}")
        r = requests.get(url, timeout=90)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and r.content and "image" in ct and len(r.content) > 10000:
            return r.content
        logger.warning(f"Pollinations img: ct={ct}, len={len(r.content) if r.content else 0}")
    except Exception as e:
        logger.warning(f"Pollinations img: {e}")
    return None

def gen_img_hf(prompt):
    if not HUGGINGFACE_KEY: return None
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1",
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": prompt, "options": {"wait_for_model": True}}, timeout=90)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return r.content
    except Exception as e:
        logger.debug(f"HF img: {e}")
    return None

def gen_img_pexels(query_en):
    if not PEXELS_KEY: return None
    try:
        logger.info(f"🖼 Pexels запрос: '{query_en}'")
        r = requests.get("https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query_en, "per_page": 15, "orientation": "landscape"},
            timeout=20)
        if r.status_code == 200:
            ph = r.json().get("photos", [])
            if ph:
                img = requests.get(random.choice(ph[:10])["src"]["large"], timeout=30)
                if img.status_code == 200: return img.content
    except Exception as e:
        logger.warning(f"Pexels: {e}")
    return None

def gen_banner(topic_ru):
    try:
        bg = random.choice([(15,20,45),(30,15,50),(10,25,40)])
        img = Image.new("RGB", (1200, 630), color=bg)
        d = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
        except: font = ImageFont.load_default()
        words = topic_ru.split(); lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 38: cur = (cur + " " + w).strip()
            else: lines.append(cur); cur = w
        if cur: lines.append(cur)
        y = 170
        for ln in lines[:6]:
            d.text((80, y), ln, fill=(120, 200, 255), font=font); y += 60
        d.text((80, 550), f"🧠 {BOT_NAME}", fill=(180, 220, 255), font=font)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=90); return buf.getvalue()
    except Exception as e:
        logger.warning(f"Баннер: {e}"); return None

def generate_image(topic_ru, topic_en):
    if not topic_en: topic_en = to_en(topic_ru)
    prompt = build_image_prompt(topic_en)
    logger.info(f"🖼 Промпт: {prompt[:140]}...")
    img = gen_img_poll(prompt)
    if img: logger.info(f"✅ Картинка: Pollinations ({len(img)} б)"); return img, "pollinations"
    img = gen_img_hf(prompt)
    if img: logger.info(f"✅ Картинка: HF ({len(img)} б)"); return img, "hf"
    img = gen_img_pexels(pexels_query_from_topic(topic_en))
    if img: logger.info(f"✅ Картинка: Pexels ({len(img)} б)"); return img, "pexels"
    logger.warning("⚠️ Баннер")
    return gen_banner(topic_ru), "banner"

# ================== GROQ ==================
def groq_chat(system, user, max_tokens=700, temperature=0.7):
    if not GROQ_API_KEY: return None
    last_err = None
    for model in GROQ_MODELS:
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature, "max_tokens": max_tokens,
                }, timeout=60)
            if r.status_code == 200:
                body = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if body and len(body) > 40:
                    logger.info(f"✅ Groq ответил ({model})")
                    return body
            elif r.status_code == 404:
                logger.warning(f"Groq {model}: 404, пробую следующую")
                last_err = f"404 {model}"; continue
            elif r.status_code == 401:
                logger.error("Groq 401 — ключ неверный, дальше нет смысла")
                return None
            elif r.status_code == 429:
                logger.warning(f"Groq 429 — лимит, пробую следующую")
                last_err = "429"; continue
            else:
                logger.warning(f"Groq {model}: {r.status_code} {r.text[:150]}")
                last_err = f"{r.status_code} {model}"; continue
        except Exception as e:
            logger.warning(f"Groq {model}: {e}")
            last_err = f"{model}: {e}"; continue
    logger.warning(f"❌ Все Groq-модели отказали. Последняя ошибка: {last_err}")
    return None

BASE_RULES = (
    "Ты — редактор русскоязычного канала про AI. Пишешь живо, дружелюбно, "
    "на «ты», без канцелярита, без штампов. Только русский язык. "
    "Без markdown-разметки, без хештегов, без эмодзи в тексте. Заголовок не пиши."
)

def gen_news(topic, summary=""):
    system = BASE_RULES + (
        " Формат: 5-7 предложений. Начни с конкретного факта, цифры или события. "
        "Далее 2-3 предложения — что именно произошло. "
        "1-2 предложения — что это значит для читателя. Вопрос в конце. "
        "ЗАПРЕЩЕНО: 'AI меняет мир', 'тренд, за которым стоит следить' и общий филлер."
    )
    user = f"Тема: {topic}\nКонтекст: {summary[:900] if summary else '(нет)'}\nНапиши пост по правилам."
    return groq_chat(system, user)

def gen_tip(topic, summary=""):
    system = BASE_RULES + (
        " Формат лайфхака: 4-6 предложений. Начни с конкретной проблемы. "
        "Дай одно чёткое решение — по шагам если нужно. В конце — вопрос. "
        "Только конкретный приём, без общих слов."
    )
    return groq_chat(system, f"Тема лайфхака: {topic}\nНапиши пост по правилам.")

def gen_quiz(topic, summary=""):
    system = (
        "Ты — редактор канала про AI. Составь викторину.\n\n"
        "ФОРМАТ (строго, без markdown):\n"
        "Вопрос: <цепляющий вопрос про AI>\n"
        "A) <вариант>\nB) <вариант>\nC) <вариант>\nD) <вариант>\n"
        "Пиши ответ в комментариях 👇\n\n"
        "Затем с новой строки строго:\n"
        "ОТВЕТ: <буква> — <краткое пояснение, 1-2 предложения>\n\n"
        "Только русский. Вопрос интересный. Неправильные варианты правдоподобные."
    )
    body = groq_chat(system, f"Тема для викторины: {topic}\nСоставь викторину.", max_tokens=500, temperature=0.8)
    if not body: return None, None
    m = re.split(r"\n*\s*ОТВЕТ\s*:\s*", body, flags=re.IGNORECASE)
    if len(m) == 2:
        return m[0].strip(), "✅ Ответ: " + m[1].strip()
    return body.strip(), None

def gen_tool(topic, summary=""):
    system = BASE_RULES + (
        " Формат «Инструмент дня»: 5-6 предложений. Название и что делает. "
        "2-3 предложения — конкретные задачи и для кого. Один неочевидный приём. "
        "В конце — вопрос «а вы пробовали?». Без рекламы."
    )
    return groq_chat(system, f"Инструмент: {topic}\nНапиши пост по правилам.")

def gen_prompt(topic, summary=""):
    system = (
        "Ты — редактор канала про AI. Формат «Промпт дня».\n\n"
        "Структура (без markdown):\n"
        "1. Одно предложение — зачем этот промпт.\n"
        "2. Пустая строка.\n"
        "3. Сам промпт в «ёлочках» (2-4 предложения, готовый к копированию).\n"
        "4. Пустая строка.\n"
        "5. Одно предложение — как подстроить под себя + вопрос.\n\n"
        "Только русский. Промпт конкретный и рабочий."
    )
    return groq_chat(system, f"Задача: {topic}\nСоставь пост-промпт.")

def gen_myth(topic, summary=""):
    system = BASE_RULES + (
        " Формат «Миф vs Факт»: 5-6 предложений. Начни «Миф: ...». "
        "Далее «На самом деле: ...» — 3-4 предложения с конкретикой. "
        "В конце — вопрос «а как вы думали раньше?»."
    )
    return groq_chat(system, f"Миф для разбора: {topic}\nНапиши пост по правилам.")

def gen_question(topic, summary=""):
    system = BASE_RULES + (
        " Формат «Вопрос подписчикам»: 3-4 предложения. Короткий контекст (1-2 предложения), "
        "затем сам вопрос. Только конкретный вопрос."
    )
    return groq_chat(system, f"Тема вопроса: {topic}\nНапиши пост по правилам.")

# ================== РОТАЦИЯ ТИПОВ ==================
def choose_post_type():
    return random.choices(list(POST_TYPE_WEIGHTS.keys()),
                          weights=list(POST_TYPE_WEIGHTS.values()), k=1)[0]

def get_topic_for_type(ptype):
    if ptype == "news": return get_news_topic()
    if ptype == "tip":
        t = random.choice(TOPICS_TIP); return t, "", "TIPS", to_en(t)
    if ptype == "tool":
        t = random.choice(TOPICS_TOOL); return t, "", "TOOLS", t
    if ptype == "prompt":
        t = random.choice(TOPICS_PROMPT); return t, "", "PROMPTS", to_en(t)
    if ptype == "myth":
        t = random.choice(TOPICS_MYTH); return t, "", "MYTHS", to_en(t)
    if ptype == "question":
        t = random.choice(TOPICS_QUESTION); return t, "", "QUESTIONS", to_en(t)
    if ptype == "quiz":
        t = random.choice(random.choice([DEFAULT_TOPICS, TOPICS_TIP, TOPICS_MYTH]))
        return t, "", "QUIZ", to_en(t)
    return get_news_topic()

# ================== RSS ==================
def check_rss_sources():
    if not RSS_ENABLED or not RSS_SOURCES:
        logger.info("📡 RSS отключён"); return 0
    logger.info(f"📡 Проверяю RSS ({len(RSS_SOURCES)} шт.)...")
    total = 0
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            n = len(feed.entries); total += n
            logger.info(f"  {'✅' if n else '⚠️'} {url} — тем: {n}")
        except Exception as e:
            logger.error(f"  ❌ {url} — {e}")
    logger.info(f"📡 Всего тем из RSS: {total}")
    return total

def get_news_topic():
    raw = []
    if RSS_ENABLED and RSS_SOURCES:
        for url in RSS_SOURCES:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:8]:
                    title = getattr(e, "title", "").strip()
                    summary = getattr(e, "summary", "") or getattr(e, "description", "")
                    summary = re.sub(r"<[^>]+>", " ", summary or "").strip()
                    if title and 15 < len(title) < 220:
                        raw.append((title, summary, url))
            except Exception as e:
                logger.warning(f"RSS {url}: {e}")
    candidates = [c for c in raw if is_topic_ok(c[0], c[1])]
    logger.info(f"📚 Из RSS: {len(raw)}, отфильтровано: {len(raw)-len(candidates)}, осталось: {len(candidates)}")
    if not candidates and os.path.exists("topics.txt"):
        with open("topics.txt", "r", encoding="utf-8") as f:
            candidates = [(l.strip(), "", "topics.txt") for l in f if l.strip() and not l.startswith("#")]
    if not candidates:
        logger.warning("Нет тем — запасной список")
        candidates = [(t, "", "DEFAULT") for t in DEFAULT_TOPICS]
    with STATE_LOCK:
        STATE["topics_count"] = len(candidates)
    topic_orig, summary, source = random.choice(candidates)
    return to_ru(topic_orig), summary, source, (topic_orig if not _is_ru(topic_orig) else to_en(topic_orig))

# ================== ФОЛБЭК ==================
FALLBACK_BY_TYPE = {
    "tip": [
        "Приём, который экономит время: перед тем как просить нейросеть что-то сделать, "
        "попроси её сначала задать тебе 2-3 уточняющих вопроса по задаче. "
        "Так ответ получается в разы точнее. Попробуй на следующем запросе — удивишься разнице.\n\n"
        "А какие приёмы используешь ты? 👇",
    ],
    "tool": [
        "Perplexity — это AI-поисковик, который сам находит источники и даёт ответы со ссылками. "
        "Идеален для быстрых исследований: не нужно открывать 20 вкладок.\n\n"
        "А вы уже пробовали? Какие впечатления? 👇",
    ],
    "prompt": [
        "Промпт для тех, кому нужно объяснить сложное простыми словами:\n\n"
        "«Объясни тему так, будто мне 12 лет. Используй 3 аналогии из повседневной жизни, "
        "без научных терминов. В конце задай мне 2 вопроса, чтобы проверить, понял ли я».\n\n"
        "Какой промпт нужен тебе? 👇",
    ],
    "myth": [
        "Миф: чтобы пользоваться AI, нужно уметь программировать.\n\n"
        "На самом деле: современные нейросети работают на обычном языке. "
        "Ты пишешь запрос так, как говоришь с человеком — и получаешь ответ.\n\n"
        "А как думал ты раньше? 👇",
    ],
    "question": [
        "Интересно ваше мнение: каким AI-инструментом вы пользуетесь каждый день "
        "и почему именно им? Поделитесь в комментариях 👇",
    ],
    "quiz": [
        "Вопрос: сколько параметров было у первой публичной версии GPT-3?\n"
        "A) 175 млрд\nB) 10 млрд\nC) 1 трлн\nD) 500 млн\n"
        "Пиши ответ в комментариях 👇",
    ],
    "news": [
        "Свежая новость из мира AI, которую стоит держать в поле зрения. "
        "Детали и первоисточник — в первом комментарии.\n\nЧто думаете? 👇",
    ],
}
QUIZ_FALLBACK_ANSWER = "✅ Ответ: A) 175 млрд параметров — именно столько было у GPT-3 в 2020 году."

def smart_fallback(topic, summary="", ptype="news"):
    if summary:
        summary_ru = to_ru(summary[:500])
        sents = re.split(r"(?<=[.!?])\s+", summary_ru)
        useful = [s.strip() for s in sents if 40 < len(s.strip()) < 300][:3]
        facts = " ".join(useful)[:500]
        if facts:
            return f"{facts}\n\nЧто думаете по теме? 👇"
    return random.choice(FALLBACK_BY_TYPE.get(ptype, FALLBACK_BY_TYPE["news"]))

# ================== VK ==================
def safe_vk(fn, *a, max_retries=3, **kw):
    for i in range(max_retries):
        try: return fn(*a, **kw)
        except ApiError as e:
            if e.code == 9:
                w = 600 * (i + 1)
                logger.warning(f"🚫 Flood control, жду {w//60} мин")
                time.sleep(w)
            elif e.code == 14:
                logger.error("Капча"); return None
            else:
                logger.error(f"VK {e.code}: {e}"); return None
    return None

def upload_photo(image_data):
    """
    Загружает фото на стену группы.
    Требует ПОЛЬЗОВАТЕЛЬСКИЙ токен (VK_TOKEN_USER) — с групповым VK отдаёт ошибку 27.
    """
    if not VK_TOKEN_USER:
        logger.warning("⚠️ VK_TOKEN_USER не задан — фото не прикрепится")
        return None
    try:
        vk_user = vk_api.VkApi(token=VK_TOKEN_USER)
        upload = VkUpload(vk_user)
        bio = BytesIO(image_data)
        bio.name = "post.jpg"
        ph = upload.photo_wall(photos=bio, group_id=abs(GROUP_ID))
        if ph:
            att = f"photo{ph[0]['owner_id']}_{ph[0]['id']}"
            logger.info(f"📷 Фото: {att}")
            return att
        logger.warning("photo_wall вернул пустой результат")
    except ApiError as e:
        logger.error(f"Фото: VK {e.code} — {e}")
    except Exception as e:
        logger.error(f"Фото: {type(e).__name__} — {e}")
    return None

def publish(text, image_data):
    """Публикует пост в группу. Фото — через пользовательский токен, пост — через групповой."""
    att = None
    if image_data:
        att = upload_photo(image_data)
        if att:
            time.sleep(2)

    try:
        vk_session = vk_api.VkApi(token=VK_TOKEN)
        vk = vk_session.get_api()
    except Exception as e:
        logger.error(f"VK auth (group): {e}"); return False, None

    res = safe_vk(vk.wall.post, owner_id=GROUP_ID, from_group=1,
                  message=text, attachments=att or "")
    if res:
        pid = res.get("post_id")
        logger.info(f"✅ Пост (post_id={pid}, фото={'да' if att else 'нет'})")
        return True, pid
    logger.error("❌ Не опубликовано")
    return False, None

def post_comment(post_id, text):
    try:
        vk_session = vk_api.VkApi(token=VK_TOKEN)
        vk = vk_session.get_api()
        res = safe_vk(vk.wall.createComment, owner_id=GROUP_ID, post_id=post_id,
                      from_group=1, message=text)
        if res:
            logger.info(f"💬 Комментарий к посту {post_id} опубликован")
            return True
    except Exception as e:
        logger.error(f"Комментарий: {e}")
    return False

# ================== ФОРМАТ ==================
TYPE_EMOJI = {
    "news":     ["📰", "🔍", "⚡", "🚀", "🌐"],
    "tip":      ["💡", "🧠", "🛠", "🎯"],
    "quiz":     ["🎯", "❓", "🧩", "🏆"],
    "tool":     ["🧰", "🛠", "⚙️", "🚀"],
    "prompt":   ["✍️", "📝", "🎨", "🧩"],
    "myth":     ["🔍", "❌", "✅", "🧠"],
    "question": ["❓", "💬", "🗣", "👇"],
}
TYPE_HASHTAGS = {
    "news":     "#AI #нейросети #новости",
    "tip":      "#AI #лайфхак #советы",
    "quiz":     "#AI #викторина #квиз",
    "tool":     "#AI #инструменты #обзор",
    "prompt":   "#AI #промпты #chatgpt",
    "myth":     "#AI #мифы #факты",
    "question": "#AI #обсуждение #вопрос",
}

def format_post(topic, body, ptype):
    e = random.choice(TYPE_EMOJI.get(ptype, ["🧠"]))
    tags = TYPE_HASHTAGS.get(ptype, "#AI #нейросети")
    return f"{e} {topic}\n\n{body}\n\n{tags}"

# ================== КЭШ ==================
def img_hash(img): return hashlib.md5(img).hexdigest()
def is_cached(h):
    f = os.path.join(DATA_DIR, "image_cache.txt")
    if not os.path.exists(f): return False
    with open(f) as fp: return h in {l.strip() for l in fp}
def save_cache(h):
    with open(os.path.join(DATA_DIR, "image_cache.txt"), "a") as fp: fp.write(h + "\n")

# ================== ЦИКЛ ==================
def post_now(forced_type=None):
    ptype = forced_type or choose_post_type()
    logger.info(f"⏰ [{BOT_NAME}] Запуск постинга (тип: {ptype})")

    topic_ru, summary, source, topic_en = get_topic_for_type(ptype)
    logger.info(f"📝 Тема RU: {topic_ru}")
    logger.info(f"📎 Источник: {source}")

    quiz_answer = None
    gen_name = "groq"

    if ptype == "news":
        body = gen_news(topic_ru, summary)
        if not body:
            logger.info("↪️ News упал, пробую tip")
            ptype = "tip"; body = gen_tip(topic_ru)
    elif ptype == "tip": body = gen_tip(topic_ru)
    elif ptype == "quiz": body, quiz_answer = gen_quiz(topic_ru)
    elif ptype == "tool": body = gen_tool(topic_ru)
    elif ptype == "prompt": body = gen_prompt(topic_ru)
    elif ptype == "myth": body = gen_myth(topic_ru)
    elif ptype == "question": body = gen_question(topic_ru)
    else: body = gen_news(topic_ru, summary)

    if not body:
        logger.warning(f"⚠️ Groq не ответил — фолбэк для типа {ptype}")
        gen_name = "fallback"
        body = smart_fallback(topic_ru, summary, ptype)
        if ptype == "quiz" and not quiz_answer:
            quiz_answer = QUIZ_FALLBACK_ANSWER

    text = format_post(topic_ru, body, ptype)
    logger.info(f"📄 Текст ({len(text)} симв.) от {gen_name}: {text[:200]}...")

    img, img_source = generate_image(topic_ru, topic_en)
    if img:
        h = img_hash(img)
        if is_cached(h):
            logger.info("🔁 Дубль — перегенерация")
            img, img_source = generate_image(topic_ru, topic_en + " alt style")
            if img: h = img_hash(img)
        if img: save_cache(h)

    ok, post_id = publish(text, img)

    if ok and ptype == "quiz" and quiz_answer and QUIZ_ANSWER_IN_COMMENT and post_id:
        time.sleep(QUIZ_ANSWER_DELAY_SEC)
        post_comment(post_id, quiz_answer)

    with STATE_LOCK:
        STATE["last_post_topic"] = topic_ru
        STATE["last_post_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        STATE["last_post_ok"] = ok
        STATE["last_source"] = source
        STATE["last_gen"] = gen_name
        STATE["last_img_source"] = img_source
        STATE["last_type"] = ptype
        STATE["type_counters"][ptype] = STATE["type_counters"].get(ptype, 0) + 1

    if ok:
        tg_send(f"🚀 <b>[{BOT_NAME}]</b> Пост ({ptype})\n"
                f"<b>Тема:</b> {topic_ru}\n"
                f"<b>Текст:</b> {gen_name}\n"
                f"<b>Картинка:</b> {img_source}\n"
                f"{'<b>Ответ в комментах:</b> да' if quiz_answer else ''}\n\n"
                f"{text[:800]}")
    else:
        tg_send(f"❌ <b>[{BOT_NAME}]</b> Не опубликовано ({ptype})")

    logger.info(f"🏁 [{BOT_NAME}] Цикл завершён ({ptype})\n")

def main():
    logger.info(f"🚀 {BOT_NAME} запущен")
    logger.info(f"📡 Группа: {GROUP_ID}")
    logger.info(f"⏰ Расписание: {', '.join(POST_TIMES)}")
    logger.info(f"🔑 VK group: {'есть' if VK_TOKEN else 'НЕТ'}")
    logger.info(f"🔑 VK user: {'есть' if VK_TOKEN_USER else 'НЕТ — фото не прикрепится'}")
    logger.info(f"🔑 Groq: {'есть' if GROQ_API_KEY else 'НЕТ'}")
    logger.info(f"🔑 HF: {'есть' if HUGGINGFACE_KEY else 'НЕТ'}")
    logger.info(f"🔑 Pexels: {'есть' if PEXELS_KEY else 'НЕТ'}")
    logger.info(f"🎲 Типы постов: {POST_TYPE_WEIGHTS}")

    tg_init()
    if TELEGRAM_TOKEN:
        threading.Thread(target=tg_command_loop, daemon=True).start()
        if TELEGRAM_CHAT_ID:
            tg_send(f"🟢 <b>[{BOT_NAME}]</b> запущен\n"
                    f"Groq: {'✅' if GROQ_API_KEY else '❌'}\n"
                    f"VK user: {'✅' if VK_TOKEN_USER else '❌ (фото не прикрепится)'}\n"
                    f"Расписание: {', '.join(POST_TIMES)}\n"
                    f"/test · /diag · /groq · /types · /status")

    check_rss_sources()
    for t in POST_TIMES:
        try:
            schedule.every().day.at(t).do(post_now)
            logger.info(f"  → задача на {t}")
        except Exception as e:
            logger.error(f"Время {t}: {e}")
    while True:
        schedule.run_pending(); time.sleep(30)

if __name__ == "__main__":
    if not VK_TOKEN:
        logger.error("❌ Нет VK_TOKEN_AI"); sys.exit(1)
    try: main()
    except KeyboardInterrupt: logger.info("Остановлено")