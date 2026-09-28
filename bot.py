#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор. Группа -240273450. Посты в 09:00 и 15:00.
Типы: новость, лайфхак, кейс, викторина, инструмент, промпт, миф/факт, вопрос.
Генерация: Groq (основной) → Pollinations → HF → умный фолбэк.
Картинки: Pollinations → HF → Pexels → баннер.
"""

import os, sys, time, json, random, hashlib, logging, requests, schedule, re, threading
import urllib.request, urllib.parse, urllib.error
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
VK_API_VER = "5.199"

VK_TOKEN        = _env("VK_TOKEN_AI", "AI_VK_TOKEN", "VK_TOKEN")
VK_TOKEN_USER   = _env("VK_TOKEN_USER", "USER_VK_TOKEN")
HUGGINGFACE_KEY = _env("HUGGINGFACE_API_KEY", "HF_API_KEY", "HF_TOKEN")
PEXELS_KEY      = _env("PEXELS_API_KEY", "PEXELS_TOKEN")
GROQ_API_KEY    = _env("GROQ_API_KEY", "GROQ_KEY")

TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN", "AI_TELEGRAM_TOKEN", "TG_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "AI_CHAT_ID")

# ================== ТИПЫ ПОСТОВ ==================
POST_TYPE_WEIGHTS = {
    "tip":      22,   # 💡 лайфхак
    "case":     18,   # 🧑‍💻 реальный кейс
    "news":     20,   # 📰 новость простыми словами
    "prompt":   12,   # ✍️ готовый промпт
    "tool":     10,   # 🧰 инструмент дня
    "myth":     10,   # 🔍 миф vs факт
    "question":  4,   # ❓ вопрос подписчикам
    "quiz":      4,   # 🎯 викторина
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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

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

# ================== TELEGRAM ==================
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

# ================== GROQ ==================
GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

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
                logger.error("Groq 401 — ключ неверный")
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
    logger.warning(f"❌ Все Groq-модели отказали. Последняя: {last_err}")
    return None

# ================== ДИАГНОСТИКА ==================
def diag_check_groq():
    if not GROQ_API_KEY:
        return "❌ Groq: ключ НЕ задан"
    for model in GROQ_MODELS:
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": "Скажи одно слово: ok"}],
                      "max_tokens": 10}, timeout=30)
            if r.status_code == 200:
                answer = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return f"✅ Groq: OK ({model}, ответ: {answer[:40]})"
            elif r.status_code == 401:
                return "❌ Groq: 401 — ключ неверный"
            elif r.status_code in (429, 404):
                continue
        except Exception as e:
            return f"❌ Groq: {type(e).__name__}: {str(e)[:150]}"
    return "❌ Groq: ни одна модель недоступна"

def diag_vk_group():
    if not VK_TOKEN:
        return "❌ VK group: токен НЕ задан"
    try:
        r = requests.get("https://api.vk.com/method/groups.getById",
                         params={"access_token": VK_TOKEN, "v": VK_API_VER,
                                 "group_id": abs(GROUP_ID)}, timeout=15).json()
        if "response" in r and r["response"].get("groups"):
            g = r["response"]["groups"][0]
            return f"✅ VK group: {g.get('name')} (id={g.get('id')})"
        return f"❌ VK group: {r}"
    except Exception as e:
        return f"❌ VK group: {str(e)[:100]}"

def diag_vk_user():
    if not VK_TOKEN_USER:
        return "❌ VK user: токен НЕ задан — фото не прикрепится"
    try:
        r = requests.get("https://api.vk.com/method/users.get",
                         params={"access_token": VK_TOKEN_USER, "v": VK_API_VER}, timeout=15).json()
        if "error" in r:
            return f"❌ VK user: {r['error'].get('error_code')} — {r['error'].get('error_msg')}"
        u = r["response"][0]
        uid = u.get("id")
        name = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        is_admin = "?"
        try:
            gm = requests.get("https://api.vk.com/method/groups.getById",
                params={"access_token": VK_TOKEN_USER, "v": VK_API_VER,
                        "group_id": abs(GROUP_ID), "fields": "is_admin,is_member"},
                timeout=15).json()
            if "response" in gm and gm["response"].get("groups"):
                g = gm["response"]["groups"][0]
                is_admin = "✅ админ" if g.get("is_admin") else ("❌ не админ" if g.get("is_member") else "❌ не в группе")
        except Exception as e:
            is_admin = f"? ({e})"
        return f"✅ VK user: id={uid} ({name}) — {is_admin}"
    except Exception as e:
        return f"❌ VK user: {str(e)[:100]}"

def diag_check_hf():
    if not HUGGINGFACE_KEY:
        return "⚠️ HF: ключ не задан"
    try:
        r = requests.get("https://huggingface.co/api/whoami-v2",
                         headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"}, timeout=15)
        if r.status_code == 200:
            return f"✅ HF: OK ({r.json().get('name', '?')})"
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
        return "✅ Pexels: OK" if r.status_code == 200 else f"❌ Pexels: HTTP {r.status_code}"
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
    ok = 0; total = len(RSS_SOURCES); lines = []
    for url in RSS_SOURCES:
        try:
            f = feedparser.parse(url); n = len(f.entries)
            if n > 0:
                ok += 1; lines.append(f"  ✅ {url.split('/')[2]} — {n}")
            else:
                lines.append(f"  ⚠️ {url.split('/')[2]} — 0")
        except Exception as e:
            lines.append(f"  ❌ {url.split('/')[2]} — {str(e)[:60]}")
    return f"📡 RSS: {ok}/{total}\n" + "\n".join(lines)

def diag_full():
    return "\n".join([
        f"🔧 <b>Диагностика [{BOT_NAME}]</b>", "",
        "<b>Переменные окружения:</b>",
        f"  GROQ_API_KEY: {'✅ есть' if GROQ_API_KEY else '❌ НЕТ'}",
        f"  VK_TOKEN_AI: {'✅ есть' if VK_TOKEN else '❌ НЕТ'}",
        f"  VK_TOKEN_USER: {'✅ есть' if VK_TOKEN_USER else '❌ НЕТ'}",
        f"  HUGGINGFACE_API_KEY: {'✅ есть' if HUGGINGFACE_KEY else '⚠️ нет'}",
        f"  PEXELS_API_KEY: {'✅ есть' if PEXELS_KEY else '⚠️ нет'}", "",
        "<b>Проверка сервисов:</b>",
        diag_check_groq(),
        diag_vk_group(),
        diag_vk_user(),
        diag_check_hf(),
        diag_check_pexels(),
        diag_check_pollinations(), "",
        diag_check_rss(),
    ])

# ================== TELEGRAM LOOP ==================
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
                if cid: TELEGRAM_CHAT_ID = str(cid); break
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
                parts = text.split(); cmd = parts[0].lower().split("@")[0]
                logger.info(f"📨 Команда: {cmd}")
                if cmd in ("/start", "/help"):
                    tg_send(f"🤖 <b>{BOT_NAME}</b>\n\n"
                            f"/test — пост (случайный тип)\n"
                            f"/test news|tip|quiz|tool|prompt|myth|question|case\n"
                            f"/diag — диагностика\n/groq — проверка Groq\n"
                            f"/status — состояние\n/types — счётчики")
                elif cmd == "/diag":
                    tg_send("🔧 Диагностика, подожди ~15 сек...")
                    threading.Thread(target=lambda: tg_send(diag_full()), daemon=True).start()
                elif cmd == "/groq":
                    tg_send("🔎 Проверяю Groq...")
                    def _gr():
                        st = diag_check_groq()
                        ex = ""
                        if st.startswith("✅"):
                            b = groq_chat("Только русский, кратко.",
                                          "Одно предложение про нейросети.", max_tokens=80)
                            if b: ex = f"\n\n<b>Текст:</b>\n{b[:300]}"
                        tg_send(f"<b>Groq:</b> {st}{ex}")
                    threading.Thread(target=_gr, daemon=True).start()
                elif cmd == "/status":
                    with STATE_LOCK: s = dict(STATE)
                    counters = ", ".join(f"{k}:{v}" for k, v in s["type_counters"].items())
                    tg_send(f"📊 <b>[{BOT_NAME}]</b>\n\n"
                            f"Тем: {s['topics_count']}\n"
                            f"Последний: {s['last_post_time'] or '—'}\n"
                            f"Тип: {s['last_type'] or '—'}\n"
                            f"Тема: {s['last_post_topic'] or '—'}\n"
                            f"Текст: {s['last_gen'] or '—'}\n"
                            f"Картинка: {s['last_img_source'] or '—'}\n"
                            f"Результат: {'✅' if s['last_post_ok'] else '❌' if s['last_post_ok'] is False else '—'}\n\n"
                            f"Счётчики: {counters}")
                elif cmd == "/types":
                    with STATE_LOCK: c = dict(STATE["type_counters"])
                    tg_send("📈 <b>Счётчики</b>\n\n" + "\n".join(f"  {k}: {v}" for k, v in c.items()))
                elif cmd == "/test":
                    forced = parts[1].lower() if len(parts) > 1 else None
                    if forced and forced not in POST_TYPE_WEIGHTS:
                        tg_send(f"❓ Неизвестный тип. Доступные: {', '.join(POST_TYPE_WEIGHTS.keys())}"); continue
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
                         params={"q": text[:500], "langpair": f"{source_lang}|{target_lang}"}, timeout=20)
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
    t = (title or "").lower(); s = (summary or "").lower()[:300]
    for bad in TOPIC_BLACKLIST:
        if bad in t: return False
    promo = ["скидка", "промокод", "записаться", "регистрация",
             "бесплатно", "вебинар", "подпишись", "подписывайтесь"]
    if sum(1 for m in promo if m in s) >= 3: return False
    if t.endswith("?") and len(t) < 30: return False
    return True

# ================== ТЕМЫ ==================
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
    "Как попросить AI написать поздравление маме на день рождения",
    "Как за 2 минуты составить план питания на неделю с помощью AI",
    "Как перевести инструкцию к лекарству с иностранного языка",
    "Как придумать подарок другу, если нет идей",
    "Как попросить AI помочь ребёнку с домашкой, а не сделать её за него",
    "Как составить резюме с нуля с помощью нейросети",
    "Как найти рецепт из продуктов, которые есть дома",
    "Как спланировать отпуск: маршрут, отель, бюджет",
    "Как написать жалобу в управляющую компанию или магазин",
    "Как попросить AI составить план тренировок дома",
    "Как быстро разобраться в незнакомой теме за 10 минут",
    "Как попросить AI переписать текст вежливее",
]
TOPICS_CASE = [
    "Мама попросила AI составить меню на неделю для семьи из 4 человек",
    "Студент подготовился к экзамену с помощью AI за один вечер",
    "Мужчина нашёл подарок жене, о котором она мечтала, через нейросеть",
    "Женщина перевела инструкцию к лекарству бабушки с немецкого",
    "Школьник разобрался со сложной темой по математике с AI-помощником",
    "Фрилансер написал резюме за 15 минут и получил работу",
    "Молодая семья спланировала отпуск в Турции на 100 тысяч рублей",
    "Пенсионерка научилась писать письма внукам через ChatGPT",
    "Офисный работник автоматизировал подготовку отчётов",
    "Блогер придумал 30 идей для постов за час с помощью AI",
    "Мама с двумя детьми составила расписание дня и наконец выспалась",
    "Учитель подготовил интересный урок за 20 минут вместо 3 часов",
]
TOPICS_TOOL = [
    "ChatGPT — как пользоваться и с чего начать",
    "Perplexity — поисковик, который сам ищет ответы",
    "Сбербанк GigaChat — что умеет российский чат-бот",
    "YandexGPT — помощник от Яндекса для повседневных задач",
    "Kandinsky — как бесплатно нарисовать картинку",
    "Suno — как сделать песню-поздравление за 3 минуты",
    "Notion AI — заметки и планы с помощником",
    "Grammarly — проверка текста на ошибки",
    "Figma AI — простой дизайн для соцсетей",
    "Looka — как сделать логотип для маленького бизнеса",
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
    "flat illustration of everyday life scene, warm colors",
    "friendly cartoon illustration of person using smartphone, pastel",
    "cozy home scene with laptop and coffee, warm lighting",
    "realistic photo of happy person using laptop at home",
    "minimalist illustration of common objects, clean design",
    "cheerful illustration of family using phone, cartoon style",
    "soft pastel illustration of mother and child at home",
    "watercolor illustration of everyday objects, light tones",
]
IMG_COLORS_EN = [
    "warm pastel palette, soft orange and cream",
    "bright cheerful colors, sunny yellow and sky blue",
    "soft beige and warm peach tones",
    "clean white with one bright accent color",
]
IMG_LIGHT_EN = ["warm natural light from window", "soft golden hour glow",
                "bright daylight", "cozy warm lamp light"]
IMG_COMP_EN = ["close-up on hands and screen", "wide cozy scene",
               "top-down view of desk", "friendly flat lay composition"]
NEGATIVE_SUFFIX = (
    "high quality, detailed, 4k, sharp focus, warm atmosphere, "
    "no text, no watermark, no logo, no letters, no captions, "
    "no dark tones, no creepy"
)

def build_image_prompt(topic_en):
    return (f"{topic_en}, {random.choice(IMG_STYLES_EN)}, "
            f"{random.choice(IMG_COLORS_EN)}, {random.choice(IMG_LIGHT_EN)}, "
            f"{random.choice(IMG_COMP_EN)}, {NEGATIVE_SUFFIX}")

PEXELS_STOPWORDS = {
    "the","a","an","and","or","of","in","to","for","on","with","is","are",
    "was","were","be","been","has","have","had","new","how","why","what",
    "when","where","who","which","whose","whom","that","this","these","those",
    "ai","artificial","intelligence","machine","learning","neural","network",
    "your","you","my","me","we","us","our","their","his","her","its","it",
    "still","not","no","yes","can","will","would","could","should","may",
    "task","tasks","view","opinion","way","ways","thing","things",
    "good","best","better","much","many","more","most","some","any","all",
    "just","only","also","even","very","really","quite","too","so","such",
    "make","makes","made","get","gets","got","use","uses","used","using",
}

def pexels_query_from_topic(topic_en):
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", topic_en)
    useful = [w.lower() for w in words if w.lower() not in PEXELS_STOPWORDS]
    seen = set(); uniq = []
    for w in useful:
        if w not in seen: seen.add(w); uniq.append(w)
    if not uniq: uniq = ["lifestyle", "technology"]
    return " ".join(uniq[:2])

def gen_img_poll(prompt):
    try:
        seed = random.randint(1, 999999)
        url = (f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
               f"?width=1024&height=576&nologo=true&enhance=true&seed={seed}")
        r = requests.get(url, timeout=60)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and r.content and "image" in ct and len(r.content) > 10000:
            return r.content
        logger.info(f"Pollinations img: ct={ct}, len={len(r.content) if r.content else 0}")
    except Exception as e:
        logger.warning(f"Pollinations img: {e}")
    return None

def gen_img_hf(prompt):
    if not HUGGINGFACE_KEY: return None
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1",
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": prompt, "options": {"wait_for_model": True}}, timeout=60)
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
            params={"query": query_en, "per_page": 15, "orientation": "landscape"}, timeout=20)
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
        bg = random.choice([(250, 240, 220), (240, 250, 245), (250, 245, 235)])
        img = Image.new("RGB", (1200, 630), color=bg)
        d = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("DejaVuSans-Bold.ttf", 46)
        except: font = ImageFont.load_default()
        words = topic_ru.split(); lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 36: cur = (cur + " " + w).strip()
            else: lines.append(cur); cur = w
        if cur: lines.append(cur)
        y = 180
        for ln in lines[:6]:
            d.text((80, y), ln, fill=(50, 90, 140), font=font); y += 60
        d.text((80, 550), f"🧠 {BOT_NAME}", fill=(70, 110, 160), font=font)
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

# ================== ПРОМПТЫ ДЛЯ ГЕНЕРАЦИИ ==================
BASE_RULES = (
    "Ты — автор постов для русскоязычного канала про AI. "
    "Твои читатели — ОБЫЧНЫЕ ЛЮДИ: родители, офисные работники, студенты, "
    "предприниматели без технического бэкграунда. НЕ программисты.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. Пиши ТАК ПРОСТО, чтобы понял даже школьник 12 лет.\n"
    "2. ЗАПРЕЩЕНЫ термины: LLM, трансформеры, мультимодальный, токены, "
    "инференс, датасет, API, GPU, алгоритм, интеграция.\n"
    "3. Одна мысль = одно предложение. Максимум 12-15 слов.\n"
    "4. Обязательно конкретный бытовой пример.\n"
    "5. В конце — простой вопрос читателю.\n"
    "6. Только русский. Без markdown, без хештегов, без эмодзи в тексте.\n"
    "7. Не начинай с 'В этом материале', 'Сегодня', 'Разбираем'."
)

def gen_news(topic, summary=""):
    system = BASE_RULES + (
        "\n\nФормат «Новость простыми словами»: 5-6 предложений. "
        "Перескажи новость так, будто объясняешь другу в кафе. "
        "1 предложение — что произошло. 2-3 — что это значит для обычного человека. "
        "1-2 — что теперь можно сделать по-новому."
    )
    return groq_chat(system, f"Новость: {topic}\nКонтекст: {summary[:700] if summary else '(нет)'}\nНапиши простой пост.")

def gen_tip(topic, summary=""):
    system = BASE_RULES + (
        "\n\nФормат «Лайфхак»: 4-5 предложений. Начни с проблемы, которую человек знает: "
        "«Знакомо, когда…». Дай ОДИН конкретный приём, по шагам: что сказать нейросети, "
        "что получится. Пример: «Открой ChatGPT, напиши: „Помоги придумать подарок жене, "
        "она любит книги“. Получишь 5 идей за минуту». В конце — вопрос."
    )
    return groq_chat(system, f"Тема лайфхака: {topic}\nНапиши простой пост.")

def gen_case(topic, summary=""):
    system = BASE_RULES + (
        "\n\nФормат «Реальный кейс»: 5-6 предложений. Расскажи историю от третьего лица: "
        "«Один папа попросил ChatGPT…». Что было нужно → что человек сделал → "
        "какой результат → сколько времени сэкономил. В конце — вывод и вопрос."
    )
    return groq_chat(system, f"Кейс: {topic}\nНапиши пост по формату.")

def gen_quiz(topic, summary=""):
    system = (
        "Ты — автор простой викторины для обычных людей (не программистов). "
        "Вопрос должен быть про бытовое использование AI.\n\n"
        "ФОРМАТ (строго, без markdown):\n"
        "Вопрос: <простой вопрос>\n"
        "A) <вариант>\nB) <вариант>\nC) <вариант>\nD) <вариант>\n"
        "Пиши ответ в комментариях 👇\n\n"
        "Затем с новой строки строго:\n"
        "ОТВЕТ: <буква> — <пояснение, 1-2 предложения>\n\n"
        "Только русский."
    )
    body = groq_chat(system, f"Тема: {topic}\nСоставь простую викторину.", max_tokens=500, temperature=0.8)
    if not body: return None, None
    m = re.split(r"\n*\s*ОТВЕТ\s*:\s*", body, flags=re.IGNORECASE)
    if len(m) == 2:
        return m[0].strip(), "✅ Ответ: " + m[1].strip()
    return body.strip(), None

def gen_tool(topic, summary=""):
    system = BASE_RULES + (
        "\n\nФормат «Инструмент простыми словами»: 5-6 предложений. "
        "Что это за штука — 1 предложение. Что можно сделать — 2-3 примера из жизни. "
        "Для кого полезно. Один неочевидный приём. В конце — вопрос."
    )
    return groq_chat(system, f"Инструмент: {topic}\nНапиши простой пост.")

def gen_prompt(topic, summary=""):
    system = (
        "Ты — автор простых постов про AI для обычных людей.\n\n"
        "Формат «Промпт дня» — структура (без markdown):\n"
        "1. Одно предложение — зачем этот промпт.\n"
        "2. Пустая строка.\n"
        "3. Сам промпт в «ёлочках» — готовый копировать в ChatGPT.\n"
        "4. Пустая строка.\n"
        "5. Одно предложение — что подставить + вопрос.\n\n"
        "Только русский. Пример ситуации — бытовой."
    )
    return groq_chat(system, f"Задача: {topic}\nСоставь простой промпт-пост.")

def gen_myth(topic, summary=""):
    system = BASE_RULES + (
        "\n\nФормат «Миф vs Факт»: 5-6 предложений. Начни «Миф: ...». "
        "Далее «На самом деле: ...» — 3-4 предложения с бытовым примером. "
        "В конце — вопрос «А вы как думали раньше?»."
    )
    return groq_chat(system, f"Миф для разбора: {topic}\nНапиши пост.")

def gen_question(topic, summary=""):
    system = BASE_RULES + (
        "\n\nФормат «Вопрос подписчикам»: 3-4 предложения. Короткий контекст + вопрос."
    )
    return groq_chat(system, f"Тема вопроса: {topic}\nНапиши пост.")

# ================== РОТАЦИЯ ТИПОВ ==================
def choose_post_type():
    return random.choices(list(POST_TYPE_WEIGHTS.keys()),
                          weights=list(POST_TYPE_WEIGHTS.values()), k=1)[0]

def get_topic_for_type(ptype):
    if ptype == "news": return get_news_topic()
    if ptype == "tip":
        t = random.choice(TOPICS_TIP); return t, "", "TIPS", to_en(t)
    if ptype == "case":
        t = random.choice(TOPICS_CASE); return t, "", "CASES", to_en(t)
    if ptype == "tool":
        t = random.choice(TOPICS_TOOL); return t, "", "TOOLS", to_en(t)
    if ptype == "prompt":
        t = random.choice(TOPICS_PROMPT); return t, "", "PROMPTS", to_en(t)
    if ptype == "myth":
        t = random.choice(TOPICS_MYTH); return t, "", "MYTHS", to_en(t)
    if ptype == "question":
        t = random.choice(TOPICS_QUESTION); return t, "", "QUESTIONS", to_en(t)
    if ptype == "quiz":
        t = random.choice(random.choice([DEFAULT_TOPICS, TOPICS_MYTH, TOPICS_TIP]))
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
    "tip": ["Знакомо, когда не знаешь, что подарить близкому?\n\n"
            "Открой ChatGPT и напиши: «Помоги придумать подарок жене, она любит книги и кофе». "
            "За минуту получишь 5-7 идей.\n\nА вы бы попробовали? 👇"],
    "case": ["Один папа попросил ChatGPT помочь сыну с домашкой по математике.\n\n"
             "Не «сделай за него», а «объясни, как решать такие задачи». "
             "Сын разобрался за вечер, а папа сэкономил на репетиторе.\n\nА вы бы так попробовали? 👇"],
    "tool": ["GigaChat — российский чат-бот, работает без VPN и бесплатно.\n\n"
             "Можно попросить написать поздравление, придумать меню, объяснить тему. "
             "Просто напиши ему как другу.\n\nА вы уже пробовали? 👇"],
    "prompt": ["Промпт, чтобы объяснить ребёнку сложную тему:\n\n"
               "«Объясни, что такое [тема], так, будто мне 10 лет. Приведи 2 примера из жизни».\n\n"
               "Какую тему хотели бы так объяснить? 👇"],
    "myth": ["Миф: чтобы пользоваться нейросетями, нужно уметь программировать.\n\n"
             "На самом деле: вы просто пишете вопрос на русском — как другу. "
             "Никакого кода. Открываете ChatGPT или GigaChat и спрашиваете.\n\nА вы так думали? 👇"],
    "question": ["Поделитесь: какую бытовую задачу вы хотели бы переложить на нейросеть? "
                 "Может, написание писем, планирование отпуска или что-то ещё. "
                 "Напишите в комментариях 👇"],
    "quiz": ["Вопрос: сколько стоит пользоваться ChatGPT в России?\n"
             "A) 2000 руб/мес\nB) Бесплатно через браузер\nC) 500 руб/мес\nD) Только через VPN\n"
             "Пиши ответ в комментариях 👇"],
    "news": ["Свежая новость из мира нейросетей. Расскажу простыми словами, "
             "что это значит для обычного человека.\n\nЧто думаете? 👇"],
}
QUIZ_FALLBACK_ANSWER = "✅ Ответ: B) Бесплатно через браузер — базовая версия ChatGPT доступна всем."

def smart_fallback(topic, summary="", ptype="news"):
    if summary:
        summary_ru = to_ru(summary[:500])
        sents = re.split(r"(?<=[.!?])\s+", summary_ru)
        useful = [s.strip() for s in sents if 40 < len(s.strip()) < 300][:3]
        facts = " ".join(useful)[:500]
        if facts:
            return f"{facts}\n\nЧто думаете по теме? 👇"
    return random.choice(FALLBACK_BY_TYPE.get(ptype, FALLBACK_BY_TYPE["news"]))

# ================== VK: ЗАГРУЗКА ФОТО С RETRY ==================
def vk_api_call(method, token, **params):
    params.update({"access_token": token, "v": VK_API_VER})
    try:
        r = requests.post(f"https://api.vk.com/method/{method}", data=params, timeout=30).json()
        if "error" in r:
            err = r["error"]
            return None, err
        return r.get("response"), None
    except Exception as e:
        return None, {"error_code": -1, "error_msg": str(e)}

def upload_photo(image_data):
    """
    Загрузка фото в VK с retry при Flood control.
    Возвращает attachment "photo-XXX_YYY" или None.
    """
    if not VK_TOKEN_USER:
        logger.warning("⚠️ VK_TOKEN_USER не задан")
        return None
    if not image_data:
        return None

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        logger.info(f"📤 Фото, попытка {attempt}/{max_attempts} ({len(image_data)} б)")

        # Шаг 1: получить upload_url
        resp, err = vk_api_call("photos.getWallUploadServer", VK_TOKEN_USER,
                                group_id=abs(GROUP_ID))
        if err:
            code = err.get("error_code")
            if code == 9:
                wait = 30 * attempt
                logger.warning(f"🚫 Flood control на getWallUploadServer. Жду {wait} сек")
                time.sleep(wait)
                continue
            logger.error(f"VK upload_url: {code} — {err.get('error_msg')}")
            return None
        if not resp or "upload_url" not in resp:
            logger.error("VK не вернул upload_url")
            return None
        upload_url = resp["upload_url"]

        # Шаг 2: загрузка файла
        try:
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            body = b"".join([
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="photo"; filename="post.jpg"\r\n',
                b"Content-Type: image/jpeg\r\n\r\n",
                image_data,
                f"\r\n--{boundary}--\r\n".encode(),
            ])
            req = urllib.request.Request(
                upload_url, data=body, method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                up = json.loads(r.read())
        except Exception as e:
            logger.error(f"Загрузка файла: {type(e).__name__}: {e}")
            if attempt < max_attempts:
                time.sleep(15 * attempt); continue
            return None

        if not up.get("photo") or up["photo"] == "[]":
            logger.error(f"VK вернул пустой photo: {up}")
            return None

        # Шаг 3: сохранить фото
        params = {
            "group_id": abs(GROUP_ID),
            "photo": up["photo"],
            "hash": up.get("hash", ""),
            "server": up.get("server", ""),
        }
        saved, err = vk_api_call("photos.saveWallPhoto", VK_TOKEN_USER, **params)
        if err:
            code = err.get("error_code")
            if code == 9:
                wait = 30 * attempt
                logger.warning(f"🚫 Flood control на saveWallPhoto. Жду {wait} сек")
                time.sleep(wait)
                continue
            logger.error(f"VK saveWallPhoto: {code} — {err.get('error_msg')}")
            return None

        if not saved or not isinstance(saved, list) or not saved:
            logger.error(f"VK saveWallPhoto вернул: {saved}")
            return None

        p = saved[0]
        att = f"photo{p['owner_id']}_{p['id']}"
        logger.info(f"  3/3 ✅ Фото сохранено: {att}")
        return att

    logger.error("❌ Не удалось загрузить фото после всех попыток")
    return None

def publish(text, image_data):
    att = None
    if image_data:
        att = upload_photo(image_data)
        if att: time.sleep(2)
        else: logger.warning("⚠️ Пост уйдёт без фото")

    resp, err = vk_api_call("wall.post", VK_TOKEN,
                            owner_id=GROUP_ID, from_group=1,
                            message=text, attachments=att or "")
    if err:
        logger.error(f"VK wall.post: {err.get('error_code')} — {err.get('error_msg')}")
        return False, None
    if resp and "post_id" in resp:
        pid = resp["post_id"]
        logger.info(f"✅ Пост (post_id={pid}, фото={'✅' if att else '❌'})")
        return True, pid
    return False, None

def post_comment(post_id, text):
    resp, err = vk_api_call("wall.createComment", VK_TOKEN,
                            owner_id=GROUP_ID, post_id=post_id,
                            from_group=1, message=text)
    if err:
        logger.error(f"VK comment: {err.get('error_code')} — {err.get('error_msg')}")
        return False
    logger.info(f"💬 Комментарий к посту {post_id}")
    return True

# ================== ФОРМАТ ==================
TYPE_EMOJI = {
    "news":     ["📰", "🔍", "📢", "🌐"],
    "tip":      ["💡", "🧠", "✨", "🎯"],
    "case":     ["🧑‍💻", "📖", "🌟", "💬"],
    "quiz":     ["🎯", "❓", "🧩", "🏆"],
    "tool":     ["🧰", "🛠", "⚙️", "📱"],
    "prompt":   ["✍️", "📝", "🎨", "📋"],
    "myth":     ["🔍", "❌", "✅", "🧠"],
    "question": ["❓", "💬", "🗣", "👇"],
}
TYPE_HASHTAGS = {
    "news":     "#AI #нейросети #новости",
    "tip":      "#AI #лайфхак #полезное",
    "case":     "#AI #кейс #жизнь",
    "quiz":     "#AI #викторина",
    "tool":     "#AI #инструменты",
    "prompt":   "#AI #промпты #chatgpt",
    "myth":     "#AI #мифы #факты",
    "question": "#AI #обсуждение",
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
    elif ptype == "case": body = gen_case(topic_ru)
    elif ptype == "quiz": body, quiz_answer = gen_quiz(topic_ru)
    elif ptype == "tool": body = gen_tool(topic_ru)
    elif ptype == "prompt": body = gen_prompt(topic_ru)
    elif ptype == "myth": body = gen_myth(topic_ru)
    elif ptype == "question": body = gen_question(topic_ru)
    else: body = gen_news(topic_ru, summary)

    if not body:
        logger.warning(f"⚠️ Groq не ответил — фолбэк для {ptype}")
        gen_name = "fallback"
        body = smart_fallback(topic_ru, summary, ptype)
        if ptype == "quiz" and not quiz_answer:
            quiz_answer = QUIZ_FALLBACK_ANSWER

    text = format_post(topic_ru, body, ptype)
    logger.info(f"📄 Текст ({len(text)} симв.) от {gen_name}: {text[:150]}...")

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
    logger.info(f"🔑 VK user: {'есть' if VK_TOKEN_USER else 'НЕТ'}")
    logger.info(f"🔑 Groq: {'есть' if GROQ_API_KEY else 'НЕТ'}")
    logger.info(f"🎲 Типы постов: {POST_TYPE_WEIGHTS}")

    tg_init()
    if TELEGRAM_TOKEN:
        threading.Thread(target=tg_command_loop, daemon=True).start()
        if TELEGRAM_CHAT_ID:
            tg_send(f"🟢 <b>[{BOT_NAME}]</b> запущен\n"
                    f"Groq: {'✅' if GROQ_API_KEY else '❌'}\n"
                    f"VK user: {'✅' if VK_TOKEN_USER else '❌'}\n"
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