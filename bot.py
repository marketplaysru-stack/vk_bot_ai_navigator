#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор. Группа -240273450. Посты в 09:00 и 15:00.
Генерация текста: Groq → Pollinations → HF → умный шаблон.
Управление через Telegram: /test, /status, /help.
"""

import os, sys, time, random, hashlib, logging, requests, schedule, re, threading
from pathlib import Path
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import feedparser, vk_api
from vk_api.upload import VkUpload
from vk_api.exceptions import ApiError

# ================== ЧТЕНИЕ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==================
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
HUGGINGFACE_KEY = _env("HUGGINGFACE_API_KEY", "HF_API_KEY", "HF_TOKEN", "HUGGINGFACE_TOKEN")
PEXELS_KEY      = _env("PEXELS_API_KEY", "PEXELS_TOKEN")
GROQ_API_KEY    = _env("GROQ_API_KEY", "GROQ_KEY")

TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN", "AI_TELEGRAM_TOKEN", "TG_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "AI_CHAT_ID")

# Русские RSS — первыми, чтобы заголовки были релевантнее
RSS_ENABLED = True
RSS_SOURCES = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru",
    "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru",
    "https://www.computerworld.com/index.rss",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
]
DATA_DIR = "./data"
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
    if not TELEGRAM_TOKEN:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
        r = requests.post(url, json=params, timeout=20)
        return r.json()
    except Exception as e:
        logger.debug(f"TG {method} ошибка: {e}")
        return None

def tg_init():
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN:
        logger.info("📨 Telegram: токен не задан")
        return None
    me = tg_api("getMe")
    if not me or not me.get("ok"):
        logger.warning("📨 Telegram: токен невалиден")
        return None
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
    if not TELEGRAM_TOKEN:
        return
    cid = chat_id or TELEGRAM_CHAT_ID
    if not cid:
        return
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
    if not TELEGRAM_TOKEN:
        return
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
                    TELEGRAM_CHAT_ID = str(cid)
                    logger.info(f"📨 chat_id определён: {TELEGRAM_CHAT_ID}")
                    break

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
                    logger.info(f"📨 chat_id из сообщения: {TELEGRAM_CHAT_ID}")
                    tg_send("✅ chat_id сохранён. Команды: /test, /status, /help",
                            chat_id=chat_id)
                    continue

                if chat_id != str(TELEGRAM_CHAT_ID):
                    tg_send("⛔ Нет доступа.", chat_id=chat_id); continue

                cmd = text.split()[0].lower().split("@")[0]
                logger.info(f"📨 Команда: {cmd}")

                if cmd in ("/start", "/help"):
                    tg_send(
                        f"🤖 <b>{BOT_NAME}</b>\n\n"
                        f"/test — пост прямо сейчас\n"
                        f"/status — состояние\n"
                        f"/help — справка\n\n"
                        f"Группа: {GROUP_ID}\n"
                        f"Расписание: {', '.join(POST_TIMES)}"
                    )
                elif cmd == "/status":
                    with STATE_LOCK: s = dict(STATE)
                    tg_send(
                        f"📊 <b>[{BOT_NAME}]</b>\n\n"
                        f"Тем: {s['topics_count']}\n"
                        f"Последний: {s['last_post_time'] or '—'}\n"
                        f"Тема: {s['last_post_topic'] or '—'}\n"
                        f"Источник: {s['last_source'] or '—'}\n"
                        f"Генератор: {s['last_gen'] or '—'}\n"
                        f"Результат: {'✅' if s['last_post_ok'] else ('❌' if s['last_post_ok'] is False else '—')}"
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
    if not text:
        return text
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я')
    if cyr > len(text) * 0.3:
        return text
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:500], "langpair": "en|ru"}
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 200:
            d = r.json()
            translated = d.get("responseData", {}).get("translatedText", "")
            if translated and len(translated) > 5:
                return translated
    except Exception as e:
        logger.warning(f"Перевод: {e}")
    return text

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

# ================== ГЕНЕРАЦИЯ ТЕКСТА ==================
def gen_text_groq(topic, summary=""):
    """Groq — основной генератор. Модель llama-3.3-70b-versatile."""
    if not GROQ_API_KEY:
        return None
    system = (
        "Ты — редактор русскоязычного Telegram/VK-канала про искусственный интеллект. "
        "Пишешь живо, дружелюбно, от второго лица, без канцелярита и штампов. "
        "Структура поста: короткий хук → суть новости/темы (2-3 предложения) → "
        "почему это важно для читателя (1-2 предложения) → вопрос в конце. "
        "5-7 предложений. Без markdown, без хештегов, без заголовка — только тело поста. "
        "Не начинай с 'В этом материале', 'Сегодня мы разберём' и подобных фраз. "
        "Пиши только на русском языке, даже если тема на английском."
    )
    user = (
        f"Тема: {topic}\n"
        f"Контекст статьи: {summary[:700] if summary else '(нет)'}\n\n"
        f"Напиши пост по структуре из системного сообщения."
    )
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.8,
            "max_tokens": 600,
        }
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code == 200:
            d = r.json()
            body = d.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if body and len(body) > 60:
                return body
        logger.warning(f"Groq: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Groq: {e}")
    return None

def gen_text_pollinations(topic, summary=""):
    prompt = (
        f"Ты — редактор русскоязычного канала про AI. Напиши пост (5-7 предложений) "
        f"на РУССКОМ языке на тему: {topic}. Контекст: {summary[:400]}. "
        f"Структура: хук → суть → польза → вопрос читателю. "
        f"Без markdown, без хештегов, без штампов вроде 'в этом материале'."
    )
    try:
        url = f"https://text.pollinations.ai/{requests.utils.quote(prompt)}"
        r = requests.get(url, timeout=90)
        if r.status_code == 200 and r.text:
            text = r.text.strip()
            bad_markers = ["budget", "API key", "raise the key", "Topping up",
                           "Please", "Pollinations account", "contact whoever"]
            low = text.lower()
            if any(m.lower() in low for m in bad_markers):
                logger.warning("Pollinations вернул ошибку бюджета — отбрасываю")
                return None
            cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я')
            if len(text) > 80 and cyr > 30:
                return text
    except Exception as e:
        logger.warning(f"Pollinations текст: {e}")
    return None

def gen_text_hf(topic, summary=""):
    if not HUGGINGFACE_KEY: return None
    prompt = (
        f"Напиши пост на русском про AI на тему: «{topic}». "
        f"Контекст: {summary[:400]}. 5-7 предложений, с хуком и вопросом в конце."
    )
    try:
        url = "https://api-inference.huggingface.co/models/cointegrated/rut5-base-absum"
        r = requests.post(url,
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": prompt, "options": {"wait_for_model": True}},
            timeout=60)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and d: return d[0].get("generated_text", "").strip()
            if isinstance(d, dict): return d.get("generated_text", "").strip()
    except Exception as e:
        logger.debug(f"HF текст: {e}")
    return None

def smart_fallback(topic, summary=""):
    topic_ru = translate_to_ru(topic)
    summary_ru = translate_to_ru(summary[:400]) if summary else ""

    hooks = [
        f"Что стоит знать про «{topic_ru}»?",
        f"Разбираем: {topic_ru}",
        f"{topic_ru} — коротко о главном",
        f"О чём говорят в мире AI: {topic_ru}",
    ]
    hook = random.choice(hooks)

    if summary_ru and len(summary_ru) > 40:
        body = (
            f"{hook}\n\n"
            f"Суть: {summary_ru}\n\n"
            f"Почему это важно: тема напрямую касается того, как AI меняет работу, "
            f"творчество и повседневные задачи. Понимание таких новостей помогает "
            f"раньше замечать тренды и использовать их в свою пользу.\n\n"
            f"А что вы об этом думаете? Пишите в комментариях 👇"
        )
    else:
        body = (
            f"{hook}\n\n"
            f"Если коротко: это ещё один шаг в том, как нейросети встраиваются в реальные "
            f"задачи — от текста и кода до анализа данных. Такие новости важно отслеживать, "
            f"чтобы понимать, куда движется индустрия и какие инструменты появятся завтра.\n\n"
            f"А вы уже пробовали что-то подобное? Поделитесь в комментариях 👇"
        )
    return body

def format_post(topic, body):
    emojis = ["🧠", "🤖", "💡", "⚡", "🚀", "🧩", "🌐", "📌", "🔍"]
    e = random.choice(emojis)
    hashtags = "#AI #нейросети #искусственныйинтеллект #технологии #навигатор"
    return f"{e} {topic}\n\n{body}\n\n{hashtags}"

def generate_text(topic, summary=""):
    """Groq → Pollinations → HF → умный шаблон. Возвращает (текст, имя_генератора)."""
    body = gen_text_groq(topic, summary)
    if body:
        logger.info("📝 Текст: Groq (llama-3.3-70b)")
        return format_post(topic, body), "groq"

    body = gen_text_pollinations(topic, summary)
    if body:
        logger.info("📝 Текст: Pollinations")
        return format_post(topic, body), "pollinations"

    body = gen_text_hf(topic, summary)
    if body:
        logger.info("📝 Текст: HF")
        return format_post(topic, body), "hf"

    logger.warning("⚠️ Все LLM недоступны — умный шаблон с переводом")
    return format_post(topic, smart_fallback(topic, summary)), "fallback"

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

def get_topic():
    candidates = []
    if RSS_ENABLED and RSS_SOURCES:
        for url in RSS_SOURCES:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:5]:
                    title = getattr(e, "title", "").strip()
                    summary = getattr(e, "summary", "") or getattr(e, "description", "")
                    summary = re.sub(r"<[^>]+>", " ", summary or "").strip()
                    if title and 15 < len(title) < 220:
                        candidates.append((title, summary, url))
            except Exception as e:
                logger.warning(f"RSS {url}: {e}")

    if not candidates and os.path.exists("topics.txt"):
        with open("topics.txt", "r", encoding="utf-8") as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#"):
                    candidates.append((l, "", "topics.txt"))

    if not candidates:
        logger.warning("Нет тем из RSS/topics.txt — запасной список")
        candidates = [(t, "", "DEFAULT") for t in DEFAULT_TOPICS]

    logger.info(f"📚 Доступно тем: {len(candidates)}")
    with STATE_LOCK:
        STATE["topics_count"] = len(candidates)

    topic, summary, source = random.choice(candidates)
    # Переводим заголовок, если английский. Тело поста Groq сам сделает на русском.
    topic_ru = translate_to_ru(topic)
    return topic_ru, summary, source

# ================== КАРТИНКИ ==================
def gen_img_hf(prompt):
    if not HUGGINGFACE_KEY: return None
    try:
        url = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1"
        r = requests.post(url,
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": prompt, "options": {"wait_for_model": True}},
            timeout=90)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except Exception as e:
        logger.debug(f"HF img: {e}")
    return None

def gen_img_poll(prompt):
    try:
        seed = random.randint(1, 999999)
        url = (f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
               f"?width=1024&height=576&nologo=true&seed={seed}")
        r = requests.get(url, timeout=90)
        if r.status_code == 200 and r.content and len(r.content) > 10000:
            return r.content
    except Exception as e:
        logger.warning(f"Pollinations img: {e}")
    return None

def gen_img_pexels(query):
    if not PEXELS_KEY: return None
    try:
        r = requests.get("https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 10, "orientation": "landscape"},
            timeout=20)
        if r.status_code == 200:
            ph = r.json().get("photos", [])
            if ph:
                img = requests.get(random.choice(ph)["src"]["large"], timeout=30)
                if img.status_code == 200: return img.content
    except Exception as e:
        logger.warning(f"Pexels: {e}")
    return None

def gen_banner(topic):
    try:
        bg = random.choice([(15,20,45),(30,15,50),(10,25,40),(25,10,40)])
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
    except Exception as e:
        logger.warning(f"Баннер: {e}"); return None

def generate_image(topic):
    p = build_prompt(topic)
    logger.info(f"🖼 Промпт: {p[:100]}...")
    img = gen_img_poll(p)
    if img: logger.info(f"✅ Картинка: Pollinations ({len(img)} байт)"); return img
    img = gen_img_hf(p)
    if img: logger.info(f"✅ Картинка: HF ({len(img)} байт)"); return img
    img = gen_img_pexels(" ".join(topic.split()[:4]) + " technology")
    if img: logger.info(f"✅ Картинка: Pexels ({len(img)} байт)"); return img
    logger.warning("⚠️ Все API картинок упали — баннер")
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
                logger.error("Требуется капча"); return None
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
                logger.info(f"📷 Фото загружено: {att}")
                time.sleep(3)
        except Exception as e:
            logger.error(f"Загрузка фото: {e}")

    res = safe_vk(vk.wall.post, owner_id=GROUP_ID, from_group=1,
                  message=text, attachments=att or "")
    if res:
        logger.info(f"✅ Пост в группе {GROUP_ID} (post_id={res.get('post_id')})")
        return True
    logger.error("❌ Не удалось опубликовать")
    return False

# ================== ЦИКЛ ==================
def post_now():
    logger.info(f"⏰ [{BOT_NAME}] Запуск постинга")
    topic, summary, source = get_topic()
    logger.info(f"📝 Тема: {topic}")
    logger.info(f"📎 Источник: {source}")

    text, gen_name = generate_text(topic, summary)
    logger.info(f"📄 Текст ({len(text)} симв.) от {gen_name}: {text[:180]}...")

    img = generate_image(topic)
    if img:
        h = img_hash(img)
        if is_cached(h):
            logger.info("🔁 Дубль — перегенерация")
            img = generate_image(topic + " в другом стиле")
            if img: h = img_hash(img)
        if img: save_cache(h)
    else:
        logger.warning("🖼 Картинка не получена — публикую без фото")

    ok = publish(text, img)

    with STATE_LOCK:
        STATE["last_post_topic"] = topic
        STATE["last_post_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        STATE["last_post_ok"] = ok
        STATE["last_source"] = source
        STATE["last_gen"] = gen_name

    if ok:
        tg_send(f"🚀 <b>[{BOT_NAME}]</b> Пост опубликован\n\n"
                f"<b>Тема:</b> {topic}\n"
                f"<b>Генератор:</b> {gen_name}\n"
                f"<b>Группа:</b> {GROUP_ID}\n\n{text[:800]}")
    else:
        tg_send(f"❌ <b>[{BOT_NAME}]</b> Не удалось опубликовать пост")

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
                    f"Расписание: {', '.join(POST_TIMES)}\n"
                    f"Groq: {'✅' if GROQ_API_KEY else '❌'}\n"
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
        logger.error("❌ Нет VK_TOKEN_AI")
        sys.exit(1)
    try: main()
    except KeyboardInterrupt: logger.info("Остановлено")