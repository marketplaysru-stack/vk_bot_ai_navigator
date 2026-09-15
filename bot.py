#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор. Группа -240273450. Посты в 09:00 и 15:00.
Типы контента: новость, викторина, лайфхак, инструмент, промпт, миф/факт, вопрос.
Генерация текста: Groq → Pollinations → HF → фолбэк.
Картинка: Pollinations → HF → Pexels (en) → баннер.
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

# ================== ТИПЫ ПОСТОВ ==================
# Распределение: сумма = 100. Если хочется больше новостей — подними "news".
# Если хочешь только новости — поставь у остальных 0.
POST_TYPE_WEIGHTS = {
    "news":     40,   # 📰 новость из RSS
    "tip":      12,   # 💡 лайфхак / совет
    "quiz":     12,   # 🎯 викторина с ответом в комментах
    "tool":     10,   # 🧰 инструмент дня
    "prompt":   10,   # ✍️ готовый промпт
    "myth":      8,   # 🔍 миф vs факт
    "question":  8,   # ❓ вопрос подписчикам
}

# Публиковать ли ответ викторины отдельным комментарием (True/False)
QUIZ_ANSWER_IN_COMMENT = True
QUIZ_ANSWER_DELAY_SEC = 5  # через сколько секунд после поста

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

def tg_command_loop():
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN: return
    logger.info("📨 Слушаю Telegram: /test, /status, /help, /type")
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
                    types_list = ", ".join(f"{k}:{v}%" for k, v in POST_TYPE_WEIGHTS.items())
                    tg_send(
                        f"🤖 <b>{BOT_NAME}</b>\n\n"
                        f"/test — пост (случайный тип)\n"
                        f"/test news — пост конкретного типа\n"
                        f"/status — состояние\n"
                        f"/types — счётчики типов\n"
                        f"/help — справка\n\n"
                        f"Типы: {types_list}"
                    )
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

# ================== ТЕМЫ ДЛЯ НЕ-НОВОСТНЫХ ПОСТОВ ==================
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

PEXELS_STOPWORDS = {
    "the","a","an","and","or","of","in","to","for","on","with","is","are",
    "was","were","be","been","has","have","had","new","how","why","what",
    "when","where","who","which","ai","artificial","intelligence","machine","learning",
}

def pexels_query_from_topic(topic_en):
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", topic_en)
    useful = [w for w in words if w.lower() not in PEXELS_STOPWORDS]
    if not useful:
        useful = ["technology", "artificial", "intelligence"]
    return " ".join(useful[:3])

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

# ================== GROQ: базовый вызов ==================
def groq_chat(system, user, max_tokens=700, temperature=0.7):
    if not GROQ_API_KEY: return None
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature, "max_tokens": max_tokens,
            }, timeout=60)
        if r.status_code == 200:
            body = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if body and len(body) > 40: return body
        logger.warning(f"Groq: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Groq: {e}")
    return None

# ================== ГЕНЕРАЦИЯ ПО ТИПАМ ==================
BASE_RULES = (
    "Ты — редактор русскоязычного канала про AI. Пишешь живо, дружелюбно, "
    "на «ты», без канцелярита, без штампов. Только русский язык. "
    "Без markdown-разметки, без хештегов, без эмодзи в тексте. Заголовок не пиши."
)

def gen_news(topic, summary=""):
    system = BASE_RULES + (
        " Формат: 5-7 предложений. Начни с конкретного факта, цифры или события. "
        "Далее 2-3 предложения — что именно произошло (кто, что, где, детали). "
        "1-2 предложения — что это значит для читателя на практике. "
        "Вопрос в конце. ЗАПРЕЩЕНО: 'AI меняет мир', 'тренд, за которым стоит следить', "
        "'нейросети встраиваются в повседневные задачи' и любой общий филлер."
    )
    user = (f"Тема: {topic}\nКонтекст: {summary[:900] if summary else '(нет)'}\n"
            f"Напиши пост по правилам.")
    return groq_chat(system, user)

def gen_tip(topic, summary=""):
    system = BASE_RULES + (
        " Формат лайфхака: 4-6 предложений. "
        "Начни с конкретной проблемы, с которой сталкивается пользователь. "
        "Дай одно чёткое решение — как именно это делать, по шагам если нужно. "
        "В конце — короткий вопрос «а как у тебя?». "
        "Никаких общих слов, только конкретный приём."
    )
    user = f"Тема лайфхака: {topic}\nНапиши пост по правилам."
    return groq_chat(system, user)

def gen_quiz(topic, summary=""):
    """Возвращает (текст_поста, ответ_для_комментария) или (None, None)."""
    system = (
        "Ты — редактор русскоязычного канала про AI. Составь викторину.\n\n"
        "ФОРМАТ (строго, без markdown):\n"
        "Вопрос: <цепляющий вопрос про AI>\n"
        "A) <вариант>\n"
        "B) <вариант>\n"
        "C) <вариант>\n"
        "D) <вариант>\n"
        "Пиши ответ в комментариях 👇\n\n"
        "Затем с новой строки строго:\n"
        "ОТВЕТ: <буква> — <краткое пояснение, 1-2 предложения>\n\n"
        "Правила: только русский. Вопрос должен быть интересным, не банальным. "
        "Неправильные варианты — правдоподобные, не абсурдные."
    )
    user = f"Тема для викторины: {topic}\nСоставь викторину по формату."
    body = groq_chat(system, user, max_tokens=500, temperature=0.8)
    if not body: return None, None
    # Разделяем текст поста и ответ
    m = re.split(r"\n*\s*ОТВЕТ\s*:\s*", body, flags=re.IGNORECASE)
    if len(m) == 2:
        return m[0].strip(), "✅ Ответ: " + m[1].strip()
    return body.strip(), None

def gen_tool(topic, summary=""):
    system = BASE_RULES + (
        " Формат «Инструмент дня»: 5-6 предложений. "
        "Название инструмента и что он делает (одно предложение). "
        "2-3 предложения — конкретные задачи, которые с ним решаются, "
        "для кого он особенно полезен. Один неочевидный приём использования. "
        "В конце — вопрос «а вы пробовали?». "
        "Без рекламы, только факты."
    )
    user = f"Инструмент: {topic}\nНапиши пост по правилам."
    return groq_chat(system, user)

def gen_prompt(topic, summary=""):
    system = (
        "Ты — редактор русскоязычного канала про AI. Формат «Промпт дня».\n\n"
        "Структура (без markdown, без звёздочек, без решёток):\n"
        "1. Одно предложение — зачем этот промпт, что он решает.\n"
        "2. Пустая строка.\n"
        "3. Сам промпт в кавычках-ёлочках «…» (2-4 предложения, готовый к копированию).\n"
        "4. Пустая строка.\n"
        "5. Одно предложение — как подстроить под себя + вопрос читателю.\n\n"
        "Только русский. Промпт должен быть конкретным и рабочим."
    )
    user = f"Задача: {topic}\nСоставь пост-промпт по структуре."
    return groq_chat(system, user)

def gen_myth(topic, summary=""):
    system = BASE_RULES + (
        " Формат «Миф vs Факт»: 5-6 предложений. "
        "Начни с фразы: «Миф: <распространённое утверждение>». "
        "Далее: «На самом деле: <объяснение, почему это не так>» — 3-4 предложения "
        "с конкретикой (пример, цифра, кейс). "
        "В конце — вопрос «а как вы думали раньше?»."
    )
    user = f"Миф для разбора: {topic}\nНапиши пост по правилам."
    return groq_chat(system, user)

def gen_question(topic, summary=""):
    system = BASE_RULES + (
        " Формат «Вопрос подписчикам»: 3-4 предложения. "
        "Начни с короткого контекста (1-2 предложения), почему этот вопрос актуален. "
        "Затем сам вопрос в конце. "
        "Никаких длинных вступлений. Только конкретный вопрос."
    )
    user = f"Тема вопроса: {topic}\nНапиши пост по правилам."
    return groq_chat(system, user)

# ================== РОТАЦИЯ ТИПОВ ==================
def choose_post_type():
    """Взвешенный случайный выбор типа поста."""
    types = list(POST_TYPE_WEIGHTS.keys())
    weights = list(POST_TYPE_WEIGHTS.values())
    return random.choices(types, weights=weights, k=1)[0]

def get_topic_for_type(ptype):
    """Возвращает (topic_ru, summary, source, topic_en)."""
    if ptype == "news":
        return get_news_topic()
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
        # Викторина — на AI-тему, случайно из списка тем или новостей
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
            candidates = [(l.strip(), "", "topics.txt") for l in f
                          if l.strip() and not l.startswith("#")]

    if not candidates:
        logger.warning("Нет тем — запасной список")
        candidates = [(t, "", "DEFAULT") for t in DEFAULT_TOPICS]

    with STATE_LOCK:
        STATE["topics_count"] = len(candidates)

    topic_orig, summary, source = random.choice(candidates)
    topic_ru = to_ru(topic_orig)
    topic_en = topic_orig if not _is_ru(topic_orig) else to_en(topic_orig)
    return topic_ru, summary, source, topic_en

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
    """Возвращает (ok, post_id)."""
    try:
        vk_session = vk_api.VkApi(token=VK_TOKEN)
        vk = vk_session.get_api()
        upload = VkUpload(vk_session)
    except Exception as e:
        logger.error(f"VK auth: {e}"); return False, None
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
        pid = res.get("post_id")
        logger.info(f"✅ Пост (post_id={pid})")
        return True, pid
    logger.error("❌ Не опубликовано")
    return False, None

def post_comment(post_id, text):
    """Публикует комментарий к посту от имени группы."""
    try:
        vk_session = vk_api.VkApi(token=VK_TOKEN)
        vk = vk_session.get_api()
        res = safe_vk(vk.wall.createComment,
                      owner_id=GROUP_ID, post_id=post_id,
                      from_group=1, message=text)
        if res:
            logger.info(f"💬 Комментарий к посту {post_id} опубликован")
            return True
    except Exception as e:
        logger.error(f"Комментарий: {e}")
    return False

# ================== ЭМОДЗИ-ПРЕФИКСЫ ПО ТИПУ ==================
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
    elif ptype == "tip":
        body = gen_tip(topic_ru)
    elif ptype == "quiz":
        body, quiz_answer = gen_quiz(topic_ru)
    elif ptype == "tool":
        body = gen_tool(topic_ru)
    elif ptype == "prompt":
        body = gen_prompt(topic_ru)
    elif ptype == "myth":
        body = gen_myth(topic_ru)
    elif ptype == "question":
        body = gen_question(topic_ru)
    else:
        body = gen_news(topic_ru, summary)

    if not body:
        logger.warning("⚠️ Groq не ответил — умный фолбэк")
        gen_name = "fallback"
        body = smart_fallback(topic_ru, summary)

    text = format_post(topic_ru, body, ptype)
    logger.info(f"📄 Текст ({len(text)} симв.) от {gen_name}: {text[:200]}...")

    # Картинка
    img, img_source = generate_image(topic_ru, topic_en)
    if img:
        h = img_hash(img)
        if is_cached(h):
            logger.info("🔁 Дубль — перегенерация")
            img, img_source = generate_image(topic_ru, topic_en + " alt style")
            if img: h = img_hash(img)
        if img: save_cache(h)

    ok, post_id = publish(text, img)

    # Комментарий с ответом викторины
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

def smart_fallback(topic, summary=""):
    topic_ru = to_ru(topic)
    summary_ru = to_ru(summary[:500]) if summary else ""
    if summary_ru:
        sents = re.split(r"(?<=[.!?])\s+", summary_ru)
        useful = [s.strip() for s in sents if 40 < len(s.strip()) < 300][:3]
        facts = " ".join(useful)[:500]
        return f"{topic_ru}\n\n{facts}\n\nЧто думаете? Пишите в комментариях 👇"
    return f"{topic_ru}\n\nРазбираем тему по существу. Пишите ваши мысли в комментариях 👇"

def main():
    logger.info(f"🚀 {BOT_NAME} запущен")
    logger.info(f"📡 Группа: {GROUP_ID}")
    logger.info(f"⏰ Расписание: {', '.join(POST_TIMES)}")
    logger.info(f"🔑 VK: {'есть' if VK_TOKEN else 'НЕТ'}")
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
                    f"Расписание: {', '.join(POST_TIMES)}\n"
                    f"/test · /test quiz · /types · /status")

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