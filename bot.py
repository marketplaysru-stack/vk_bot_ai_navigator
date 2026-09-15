#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор. Группа -240273450. Посты в 09:00 и 15:00.
Генерация текста: Groq → Pollinations → HF → умный фолбэк с фактами.
Фильтр мусорных тем (промо/подборки/курсы/вебинары).
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

VK_TOKEN        = _env("VK_TOKEN_AI", "AI_VK_TOKEN", "VK_TOKEN")
HUGGINGFACE_KEY = _env("HUGGINGFACE_API_KEY", "HF_API_KEY", "HF_TOKEN")
PEXELS_KEY      = _env("PEXELS_API_KEY", "PEXELS_TOKEN")
GROQ_API_KEY    = _env("GROQ_API_KEY", "GROQ_KEY")

TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN", "AI_TELEGRAM_TOKEN", "TG_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "AI_CHAT_ID")

RSS_ENABLED = True
RSS_SOURCES = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru",
    "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru",
    "https://www.computerworld.com/index.rss",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
]
DATA_DIR = "./data"

# Стоп-слова для тем: если заголовок содержит одно из них — пропускаем
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
}
STATE_LOCK = threading.Lock()

# ================== ТЕЛЕГРАМ ==================
def tg_api(method, **params):
    if not TELEGRAM_TOKEN: return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
        return requests.post(url, json=params, timeout=20).json()
    except Exception as e:
        logger.debug(f"TG {method}: {e}")
        return None

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

def tg_command_loop():
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN: return
    logger.info("📨 Слушаю Telegram: /test, /status, /help")
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
                    tg_send("✅ chat_id сохранён. Команды: /test, /status, /help", chat_id=chat_id)
                    continue
                if chat_id != str(TELEGRAM_CHAT_ID):
                    tg_send("⛔ Нет доступа.", chat_id=chat_id); continue
                cmd = text.split()[0].lower().split("@")[0]
                logger.info(f"📨 Команда: {cmd}")
                if cmd in ("/start", "/help"):
                    tg_send(f"🤖 <b>{BOT_NAME}</b>\n\n/test — пост сейчас\n/status — состояние\n/help — справка")
                elif cmd == "/status":
                    with STATE_LOCK: s = dict(STATE)
                    tg_send(
                        f"📊 <b>[{BOT_NAME}]</b>\n\n"
                        f"Тем: {s['topics_count']}\n"
                        f"Последний: {s['last_post_time'] or '—'}\n"
                        f"Тема: {s['last_post_topic'] or '—'}\n"
                        f"Источник: {s['last_source'] or '—'}\n"
                        f"Генератор: {s['last_gen'] or '—'}\n"
                        f"Результат: {'✅' if s['last_post_ok'] else '❌' if s['last_post_ok'] is False else '—'}"
                    )
                elif cmd == "/test":
                    tg_send("🧪 Запускаю тестовый пост...")
                    threading.Thread(target=post_now, daemon=True).start()
                else:
                    tg_send(f"❓ Не знаю <code>{cmd}</code>. Напиши /help")
        except Exception as e:
            logger.error(f"TG loop: {e}")
            time.sleep(5)

# ================== ПЕРЕВОД ==================
def translate_to_ru(text):
    if not text: return text
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я')
    if cyr > len(text) * 0.3: return text
    try:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": text[:500], "langpair": "en|ru"}, timeout=20)
        if r.status_code == 200:
            t = r.json().get("responseData", {}).get("translatedText", "")
            if t and len(t) > 5: return t
    except Exception as e:
        logger.warning(f"Перевод: {e}")
    return text

# ================== ФИЛЬТР ТЕМ ==================
def is_topic_ok(title, summary):
    """Отсеивает промо, дайджесты, курсы и прочий мусор."""
    t = (title or "").lower()
    s = (summary or "").lower()[:300]
    for bad in TOPIC_BLACKLIST:
        if bad in t:
            return False
    # Если в summary много рекламных маркеров — тоже мусор
    promo_markers = ["скидка", "промокод", "записаться", "регистрация", "бесплатно",
                     "курс", "вебинар", "подпишись", "подписывайтесь"]
    promo_hits = sum(1 for m in promo_markers if m in s)
    if promo_hits >= 3:
        return False
    # Отсеиваем вопросы-заголовки без фактов
    if t.endswith("?") and len(t) < 30:
        return False
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

# ================== СТИЛИ КАРТИНОК ==================
IMG_STYLES = [
    "футуристичная цифровая иллюстрация, светящиеся нейронные связи",
    "минималистичный tech-дизайн, чистые градиенты, схемы",
    "абстрактная визуализация данных, светящиеся узлы и графы",
    "реалистичное фото робота-ассистента, мягкий свет",
    "флэт-иллюстрация: человек и нейросеть, пастельные тона",
    "киберпанк-стиль, неоновые огни, мегаполис будущего",
    "изометрическая 3D-иллюстрация, tech-объекты",
    "акварельная абстракция на тему нейросетей",
    "low-poly 3D, гранёные формы, tech-палитра",
    "сюрреалистичный коллаж: мозг и микросхемы",
    "ретро-футуризм 80-х, синтвейв-палитра",
    "минимализм: одна линия, светящаяся точка данных",
]
IMG_PALETTES = [
    "холодные сине-фиолетовые оттенки",
    "яркие неоновые акценты на тёмном фоне",
    "голубой и белый, чистые тона",
    "тёмно-синий фон с бирюзовым свечением",
    "фиолетово-розовый градиент",
    "монохром с одним ярким акцентом",
]
IMG_LIGHT = ["мягкое рассеянное свечение", "яркий контрастный свет",
             "неоновое освещение", "естественный свет, тёплые блики"]
IMG_COMPOSITION = ["крупный план", "общий план", "вид сверху",
                   "симметричная композиция", "правило третей"]

def build_prompt(topic):
    return (
        f"{topic}, {random.choice(IMG_STYLES)}, {random.choice(IMG_PALETTES)}, "
        f"{random.choice(IMG_LIGHT)}, {random.choice(IMG_COMPOSITION)}, "
        f"высокая детализация, 4k, без текста, без надписей"
    )

# ================== GROQ: жёсткий промпт на конкретику ==================
GROQ_SYSTEM = (
    "Ты — редактор русскоязычного канала про AI. Пишешь короткие ёмкие посты.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. Пиши только на русском. Если тема на английском — переведи и осмысли.\n"
    "2. Пост = 5-7 предложений. Структура:\n"
    "   • Строка 1 — короткий хук: конкретный факт, цифра или событие.\n"
    "   • Далее 2-3 предложения — ЧТО ИМЕННО произошло: кто, что, где, когда, "
    "какие цифры/названия/детали. Только факты из контекста.\n"
    "   • 1-2 предложения — что это значит для читателя НА ПРАКТИКЕ: "
    "какой вывод, что попробовать, за чем следить.\n"
    "   • Последняя строка — вопрос читателю.\n"
    "3. ЗАПРЕЩЕНО: 'это важно, потому что AI меняет мир', 'тренд, за которым стоит следить', "
    "'раньше замечать тренды', 'нейросети встраиваются в повседневные задачи' и любой "
    "подобный общий филлер. Только конкретика.\n"
    "4. ЗАПРЕЩЕНО начинать с 'В этом материале', 'Сегодня', 'Разбираем'. Начинай сразу с факта.\n"
    "5. Без markdown, без хештегов, без эмодзи. Заголовок тоже не пиши — только тело поста."
)

def gen_text_groq(topic, summary=""):
    if not GROQ_API_KEY: return None
    user = (
        f"Тема: {topic}\n\n"
        f"Контекст из статьи: {summary[:900] if summary else '(нет описания)'}\n\n"
        f"Напиши пост по правилам. Если в контексте мало фактов — используй тему "
        f"и общие знания, но всё равно давай конкретику: названия, цифры, примеры."
    )
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": GROQ_SYSTEM},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.7,
                "max_tokens": 700,
            }, timeout=60)
        if r.status_code == 200:
            body = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if body and len(body) > 60:
                return body
        logger.warning(f"Groq: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Groq: {e}")
    return None

def gen_text_pollinations(topic, summary=""):
    prompt = (
        f"Ты — редактор канала про AI. Напиши пост 5-7 предложений на русском. "
        f"Тема: {topic}. Контекст: {summary[:400]}. "
        f"Дай конкретику: что случилось, цифры, названия. Без филлера."
    )
    try:
        r = requests.get(f"https://text.pollinations.ai/{requests.utils.quote(prompt)}", timeout=90)
        if r.status_code == 200 and r.text:
            t = r.text.strip()
            bad = ["budget", "API key", "raise the key", "Pollinations account"]
            if any(b.lower() in t.lower() for b in bad):
                return None
            cyr = sum(1 for c in t if 'а' <= c.lower() <= 'я')
            if len(t) > 80 and cyr > 30: return t
    except Exception as e:
        logger.warning(f"Poll: {e}")
    return None

def gen_text_hf(topic, summary=""):
    if not HUGGINGFACE_KEY: return None
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/cointegrated/rut5-base-absum",
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": f"Пост про AI на тему: {topic}. Контекст: {summary[:300]}",
                  "options": {"wait_for_model": True}}, timeout=60)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and d: return d[0].get("generated_text", "").strip()
            if isinstance(d, dict): return d.get("generated_text", "").strip()
    except Exception as e:
        logger.debug(f"HF: {e}")
    return None

def smart_fallback(topic, summary=""):
    """Фолбэк, который ПЫТАЕТСЯ вытащить конкретику из summary."""
    topic_ru = translate_to_ru(topic)
    summary_ru = translate_to_ru(summary[:500]) if summary else ""

    # Режем summary на предложения и берём 2-3 с конкретикой
    facts = ""
    if summary_ru:
        sentences = re.split(r"(?<=[.!?])\s+", summary_ru)
        useful = [s.strip() for s in sentences if 40 < len(s.strip()) < 300][:3]
        if useful:
            facts = " ".join(useful)
            # Обрезаем слишком длинное
            if len(facts) > 500:
                facts = facts[:500].rsplit(" ", 1)[0] + "..."

    if facts:
        body = (
            f"{topic_ru}\n\n"
            f"{facts}\n\n"
            f"Что это значит на практике: тему стоит держать в поле зрения — "
            f"такие изменения влияют на то, какие AI-инструменты и подходы появятся "
            f"в ближайшие месяцы. Если тема близка к вашей работе — присмотритесь "
            f"к деталям, они важнее общих трендов.\n\n"
            f"Как вам — уже сталкивались с этим? Напишите в комментариях 👇"
        )
    else:
        body = (
            f"{topic_ru}\n\n"
            f"Коротко: это новость из мира AI, которая стоит внимания. "
            f"Детали и первоисточник — по ссылке в первом комментарии.\n\n"
            f"Что думаете? Обсудим в комментариях 👇"
        )
    return body

def format_post(topic, body):
    emojis = ["🧠", "🤖", "💡", "⚡", "🚀", "🧩", "🌐", "📌", "🔍"]
    return f"{random.choice(emojis)} {topic}\n\n{body}\n\n#AI #нейросети #искусственныйинтеллект #технологии"

def generate_text(topic, summary=""):
    """Возвращает (текст, имя генератора)."""
    body = gen_text_groq(topic, summary)
    if body:
        logger.info("📝 Текст: Groq")
        return format_post(topic, body), "groq"
    body = gen_text_pollinations(topic, summary)
    if body:
        logger.info("📝 Текст: Pollinations")
        return format_post(topic, body), "pollinations"
    body = gen_text_hf(topic, summary)
    if body:
        logger.info("📝 Текст: HF")
        return format_post(topic, body), "hf"
    logger.warning("⚠️ Все LLM недоступны — фолбэк с фактами из summary")
    return format_post(topic, smart_fallback(topic, summary)), "fallback"

# ================== RSS + фильтр ==================
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

def get_topic():
    """Собирает кандидатов, фильтрует мусор, выбирает случайного."""
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

    # Фильтр мусора
    candidates = [c for c in raw if is_topic_ok(c[0], c[1])]
    filtered = len(raw) - len(candidates)
    logger.info(f"📚 Из RSS: {len(raw)} тем, отфильтровано мусора: {filtered}, осталось: {len(candidates)}")

    if not candidates and os.path.exists("topics.txt"):
        with open("topics.txt", "r", encoding="utf-8") as f:
            candidates = [(l.strip(), "", "topics.txt") for l in f
                          if l.strip() and not l.startswith("#")]

    if not candidates:
        logger.warning("Нет тем из RSS/topics.txt — запасной список")
        candidates = [(t, "", "DEFAULT") for t in DEFAULT_TOPICS]

    with STATE_LOCK:
        STATE["topics_count"] = len(candidates)

    topic, summary, source = random.choice(candidates)
    topic_ru = translate_to_ru(topic)
    return topic_ru, summary, source

# ================== КАРТИНКИ ==================
def gen_img_hf(p):
    if not HUGGINGFACE_KEY: return None
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1",
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": p, "options": {"wait_for_model": True}}, timeout=90)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except: pass
    return None

def gen_img_poll(p):
    try:
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(p)}?width=1024&height=576&nologo=true&seed={seed}"
        r = requests.get(url, timeout=90)
        if r.status_code == 200 and r.content and len(r.content) > 10000:
            return r.content
    except Exception as e:
        logger.warning(f"Poll img: {e}")
    return None

def gen_img_pexels(q):
    if not PEXELS_KEY: return None
    try:
        r = requests.get("https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": q, "per_page": 10, "orientation": "landscape"}, timeout=20)
        if r.status_code == 200:
            ph = r.json().get("photos", [])
            if ph:
                img = requests.get(random.choice(ph)["src"]["large"], timeout=30)
                if img.status_code == 200: return img.content
    except: pass
    return None

def gen_banner(topic):
    try:
        bg = random.choice([(15,20,45),(30,15,50),(10,25,40)])
        img = Image.new("RGB", (1200, 630), color=bg)
        d = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
        except: font = ImageFont.load_default()
        words = topic.split(); lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 38: cur = (cur + " " + w).strip()
            else: lines.append(cur); cur = w
        if cur: lines.append(cur)
        y = 170
        for ln in lines[:6]:
            d.text((80, y), ln, fill=(120, 200, 255), font=font); y += 60
        d.text((80, 550), f"🧠 {BOT_NAME}", fill=(180, 220, 255), font=font)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=90); return buf.getvalue()
    except: return None

def generate_image(topic):
    p = build_prompt(topic)
    logger.info(f"🖼 Промпт: {p[:100]}...")
    img = gen_img_poll(p)
    if img: logger.info(f"✅ Картинка: Pollinations ({len(img)} б)"); return img
    img = gen_img_hf(p)
    if img: logger.info(f"✅ Картинка: HF ({len(img)} б)"); return img
    img = gen_img_pexels(" ".join(topic.split()[:4]) + " technology")
    if img: logger.info(f"✅ Картинка: Pexels"); return img
    logger.warning("⚠️ Баннер")
    return gen_banner(topic)

# ================== КЭШ ==================
def img_hash(img): return hashlib.md5(img).hexdigest()
def is_cached(h):
    f = os.path.join(DATA_DIR, "image_cache.txt")
    if not os.path.exists(f): return False
    with open(f) as fp: return h in {l.strip() for l in fp}
def save_cache(h):
    with open(os.path.join(DATA_DIR, "image_cache.txt"), "a") as fp: fp.write(h + "\n")

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

def publish(text, image_data):
    try:
        vk_session = vk_api.VkApi(token=VK_TOKEN)
        vk = vk_session.get_api()
        upload = VkUpload(vk_session)
    except Exception as e:
        logger.error(f"VK auth: {e}"); return False
    att = None
    if image_data:
        try:
            ph = safe_vk(upload.photo_wall, photo=image_data, group_id=abs(GROUP_ID))
            if ph:
                att = f"photo{ph[0]['owner_id']}_{ph[0]['id']}"
                logger.info(f"📷 Фото: {att}"); time.sleep(3)
        except Exception as e:
            logger.error(f"Фото: {e}")
    res = safe_vk(vk.wall.post, owner_id=GROUP_ID, from_group=1,
                  message=text, attachments=att or "")
    if res:
        logger.info(f"✅ Пост (post_id={res.get('post_id')})")
        return True
    logger.error("❌ Не опубликовано")
    return False

# ================== ЦИКЛ ==================
def post_now():
    logger.info(f"⏰ [{BOT_NAME}] Запуск постинга")
    topic, summary, source = get_topic()
    logger.info(f"📝 Тема: {topic}")
    logger.info(f"📎 Источник: {source}")

    text, gen_name = generate_text(topic, summary)
    logger.info(f"📄 Текст ({len(text)} симв.) от {gen_name}: {text[:200]}...")

    img = generate_image(topic)
    if img:
        h = img_hash(img)
        if is_cached(h):
            logger.info("🔁 Дубль — перегенерация")
            img = generate_image(topic + " в другом стиле")
            if img: h = img_hash(img)
        if img: save_cache(h)

    ok = publish(text, img)

    with STATE_LOCK:
        STATE["last_post_topic"] = topic
        STATE["last_post_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        STATE["last_post_ok"] = ok
        STATE["last_source"] = source
        STATE["last_gen"] = gen_name

    if ok:
        tg_send(f"🚀 <b>[{BOT_NAME}]</b> Пост\n"
                f"<b>Тема:</b> {topic}\n"
                f"<b>Генератор:</b> {gen_name}\n"
                f"<b>Источник:</b> {source}\n\n{text[:800]}")
    else:
        tg_send(f"❌ <b>[{BOT_NAME}]</b> Не удалось опубликовать")

    logger.info(f"🏁 [{BOT_NAME}] Цикл завершён\n")

def main():
    logger.info(f"🚀 {BOT_NAME} запущен")
    logger.info(f"📡 Группа: {GROUP_ID}")
    logger.info(f"⏰ Расписание: {', '.join(POST_TIMES)}")
    logger.info(f"🔑 VK: {'есть' if VK_TOKEN else 'НЕТ'}")
    logger.info(f"🔑 Groq: {'есть' if GROQ_API_KEY else 'НЕТ'}")
    logger.info(f"🔑 HF: {'есть' if HUGGINGFACE_KEY else 'НЕТ'}")

    tg_init()
    if TELEGRAM_TOKEN:
        threading.Thread(target=tg_command_loop, daemon=True).start()
        if TELEGRAM_CHAT_ID:
            tg_send(f"🟢 <b>[{BOT_NAME}]</b> запущен\n"
                    f"Groq: {'✅' if GROQ_API_KEY else '❌'}\n"
                    f"Расписание: {', '.join(POST_TIMES)}\n"
                    f"Команды: /test, /status, /help")

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