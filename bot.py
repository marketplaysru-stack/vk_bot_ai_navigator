#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот для Telegram-канала о нейросетях и искусственном интеллекте.
Генерирует посты с эмодзи и отступами.
"""

import os
import io
import json
import time
import logging
import random
import re
import requests
import threading
import feedparser
import schedule
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai")
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT", "ugly, deformed, blurry, low quality, same face, boring, plain, cartoon, doll, mannequin, 3d render, smooth skin, unrealistic, extra limbs, bad anatomy, distorted, people, human, woman, girl, beach, sea, sand, swimsuit, nude, naked, portrait, selfie, smile, face, eyes, hair, meadow, field, hay, grass, farm, cow, horse, rural, village, landscape, trees, forest, nature, road, mountains, countryside, plants, outdoor")

RSS_SOURCES_JSON = os.getenv("RSS_SOURCES", '[]')
POST_TIMES_JSON = os.getenv("POST_TIMES", '["07:00","11:00","13:00","18:00"]')
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not TELEGRAM_TOKEN or not CHANNEL_ID:
    raise ValueError("TELEGRAM_TOKEN и CHANNEL_ID обязательны!")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "rss_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ========== TELEGRAM ==========
def send_message(chat_id, text, photo_bytes=None):
    if photo_bytes:
        files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}
        data = {"chat_id": chat_id, "caption": text}
        resp = requests.post(f"{BASE_URL}/sendPhoto", data=data, files=files)
    else:
        resp = requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": text})
    return resp.json()

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    resp = requests.get(url, params=params)
    return resp.json().get("result", [])

# ========== ГЕНЕРАЦИЯ ТЕКСТА (С ЭМОДЗИ) ==========
def generate_text(topic: str) -> str:
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            data = {
                "model": "agnes-v1",
                "messages": [{"role": "user", "content": f"Напиши пост для Telegram-канала о нейросетях и искусственном интеллекте на тему: {topic}. Пост должен быть увлекательным, полезным, с примерами, фактами, прогнозами. Объём около 200 слов. Пиши в деловом, но доступном стиле. Используй эмодзи для выделения ключевых моментов, разбивай текст на абзацы с отступами."}],
                "max_tokens": 400,
                "temperature": 0.7
            }
            resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if text and len(text) > 50:
                    return text.strip()
        except Exception as e:
            logger.warning(f"Agnes (текст) не сработал: {e}")

    return generate_template_text(topic)

def generate_template_text(topic: str) -> str:
    intro_phrases = [
        f"🧠 Нейросети и искусственный интеллект всё глубже проникают в нашу жизнь.\nСегодня мы поговорим о том, как **{topic}** меняет привычный уклад.",
        f"🤖 Искусственный интеллект — это не просто модное слово, а реальный инструмент трансформации.\nРазберёмся, как **{topic}** влияет на бизнес и повседневность.",
        f"🚀 Технологии ИИ развиваются стремительно.\nОдна из ключевых тем сегодня — **{topic}**. Давайте рассмотрим её подробнее."
    ]
    body_phrases = [
        "📊 Согласно последним исследованиям, компании, внедряющие AI, увеличивают производительность на 30–40%.",
        "🧩 Нейросети уже сегодня помогают в анализе данных, прогнозировании, автоматизации рутинных задач.",
        "💼 Специалисты по данным и AI-инженеры становятся самыми востребованными на рынке труда.",
        "🤝 Важно понимать, что ИИ не заменяет человека, а дополняет его компетенции, освобождая время для творчества.",
        "⚠️ Однако есть и вызовы: этические вопросы, необходимость переобучения кадров, кибербезопасность.",
        "🌍 Перспективы огромны: от персонализированной медицины до управления умными городами."
    ]
    conclusion_phrases = [
        "🎯 Подводя итог, можно сказать, что **{topic}** — это не будущее, а уже настоящее. Важно быть в курсе и использовать эти инструменты с умом.",
        "✨ Искусственный интеллект открывает новые горизонты. Будьте готовы меняться вместе с технологиями!",
        "🔔 Следите за обновлениями в нашем канале, чтобы не пропустить самое интересное о мире ИИ."
    ]
    intro = random.choice(intro_phrases)
    body = random.sample(body_phrases, k=3)
    conclusion = random.choice(conclusion_phrases).format(topic=topic)
    return f"{intro}\n\n{' '.join(body)}\n\n{conclusion}"

# ========== ГЕНЕРАТОРЫ КАРТИНОК ==========
def random_seed():
    return random.randint(1, 1000000)

def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    base_queries = [
        f"artificial intelligence {topic}",
        f"AI technology {topic}",
        f"neural network {topic}",
        f"machine learning {topic}",
        f"AI startup {topic}",
        f"digital transformation {topic}",
        f"business technology {topic}",
        f"future technology {topic}",
        f"AI concept {topic}",
        f"technology innovation {topic}"
    ]
    words = topic.split()[:3]
    if words:
        short_query = ' '.join(words)
        base_queries.append(short_query)
    random.shuffle(base_queries)
    for query in base_queries[:5]:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        page = random.randint(1, 3)
        params = {"query": query, "per_page": 5, "page": page, "orientation": "landscape"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    photo = random.choice(photos)
                    photo_url = photo["src"]["large2x"]
                    logger.info(f"Pexels: запрос '{query}', страница {page}")
                    return photo_url
        except Exception as e:
            logger.warning(f"Pexels ошибка: {e}")
    return None

def download_photo(url):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except:
        pass
    return None

def generate_pixazo(prompt):
    if not PIXAZO_API_KEY:
        return None
    try:
        seed = random_seed()
        url = "https://api.pixazo.com/v1/generate"
        headers = {
            "Authorization": f"Bearer {PIXAZO_API_KEY}",
            "Content-Type": "application/json"
        }
        full_prompt = f"Professional illustration about {prompt}, AI, technology, neural networks, futuristic, no people, no nature"
        data = {
            "prompt": full_prompt,
            "model": "flux",
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 30,
            "guidance_scale": 7.0,
            "seed": seed
        }
        resp = requests.post(url, json=data, headers=headers, timeout=60)
        if resp.status_code == 200:
            result = resp.json()
            image_url = result.get("image_url")
            if image_url:
                img_resp = requests.get(image_url, timeout=30)
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as e:
        logger.warning(f"Pixazo не сработал: {e}")
    return None

def generate_pollinations(prompt):
    try:
        seed = random_seed()
        full_prompt = f"{prompt}, AI, technology, futuristic, professional photo, high quality"
        url = f"{POLLINATIONS_BASE_URL}/prompt/{requests.utils.quote(full_prompt)}?width=1024&height=1024&nologo=true&seed={seed}&model=flux"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.warning(f"Pollinations не сработал: {e}")
    return None

def create_banner(text, width=1024, height=1024):
    img = Image.new('RGB', (width, height), color='#0a0a2e')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
    except:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2
    draw.text((x, y), text, fill='#FFD700', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def generate_image(topic):
    # 1. Pexels
    if PEXELS_API_KEY:
        photo_url = search_pexels_relevant_photo(topic)
        if photo_url:
            img = download_photo(photo_url)
            if img:
                logger.info("✅ Картинка от Pexels")
                return img, "Pexels"

    # 2. Pixazo
    if PIXAZO_API_KEY:
        img = generate_pixazo(topic)
        if img:
            logger.info("✅ Картинка от Pixazo")
            return img, "Pixazo"

    # 3. Pollinations
    img = generate_pollinations(topic)
    if img:
        logger.info("✅ Картинка от Pollinations")
        return img, "Pollinations"

    # 4. Баннер
    img = create_banner(topic[:20])
    logger.info("✅ Использован баннер")
    return img, "баннер"

# ========== ПУБЛИКАЦИЯ В КАНАЛ ==========
def publish_to_channel(text, image_bytes):
    try:
        if image_bytes:
            resp = send_message(CHANNEL_ID, text, photo_bytes=image_bytes)
        else:
            resp = send_message(CHANNEL_ID, text)
        if resp.get("ok"):
            logger.info(f"Пост опубликован в канале: {resp}")
            return "✅ Пост опубликован в канале"
        else:
            error = resp.get("description", "неизвестная ошибка")
            logger.error(f"Ошибка публикации: {error}")
            return f"❌ Ошибка: {error}"
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        return f"❌ Ошибка: {e}"

def create_post(topic, custom_text=None):
    if custom_text and len(custom_text) > 50:
        post_text = custom_text
    else:
        post_text = generate_text(topic)
    image_bytes, source = generate_image(topic)
    return post_text, image_bytes, source

def publish_post(topic, custom_text=None):
    post_text, image_bytes, source = create_post(topic, custom_text)
    result = publish_to_channel(post_text, image_bytes)
    return {"channel": result, "image_source": source}

# ========== RSS ПЛАНИРОВЩИК ==========
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def get_rss_entries(sources_json):
    sources = json.loads(sources_json)
    entries = []
    for src in sources:
        url = src.get("url")
        if not url:
            continue
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                if title:
                    entries.append(title)
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS {url}: {e}")
    return entries

def rss_post_job():
    logger.info("⏰ Автоматическая публикация по расписанию")
    try:
        titles = get_rss_entries(RSS_SOURCES_JSON)
        if not titles:
            logger.warning("Нет заголовков из RSS")
            return

        state = load_state()
        published_titles = set(state.get("published_titles", []))
        available = [t for t in titles if t not in published_titles]
        if not available:
            logger.warning("Нет новых заголовков, используем случайный")
            available = titles

        topic = random.choice(available)
        logger.info(f"Публикуем тему: {topic}")
        result = publish_post(topic)
        logger.info(f"Результат: {result}")

        published_titles.add(topic)
        state["published_titles"] = list(published_titles)
        save_state(state)
    except Exception as e:
        logger.error(f"Ошибка в rss_post_job: {e}")

def rss_scheduler():
    logger.info("📡 RSS-планировщик запущен (используется schedule)")
    schedule.clear()
    post_times = json.loads(POST_TIMES_JSON)
    for t in post_times:
        schedule.every().day.at(t).do(rss_post_job)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ========== ОБРАБОТЧИК КОМАНД ==========
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "🤖 Привет! Я бот для канала о нейросетях и ИИ.\n"
            "📌 Команды:\n"
            "/post <тема> — сгенерировать и опубликовать пост\n"
            "/post <текст (от 50 символов)> — опубликовать готовый текст с картинкой\n"
            "/ping — проверить работу"
        )
        return

    if text == "/ping":
        send_message(chat_id, "🏓 Pong! Бот работает")
        return

    if text.startswith("/post"):
        content = text.replace("/post", "").strip()
        if not content:
            send_message(chat_id, "❌ Укажите тему или текст.")
            return

        if len(content) > 50:
            custom_text = content
            topic = content[:50] + "..."
            send_message(chat_id, f"⏳ Публикую готовый пост...")
        else:
            custom_text = None
            topic = content
            send_message(chat_id, f"⏳ Генерирую пост на тему: {topic}...")

        result = publish_post(topic, custom_text)
        send_message(chat_id, f"📌 Канал:\n{result['channel']}")
        send_message(chat_id, f"🖼 Источник картинки: {result['image_source']}")
        return

    send_message(chat_id, "❓ Неизвестная команда. Напишите /help")

# ========== ЗАПУСК ==========
def main():
    logger.info("🚀 Бот запущен")
    scheduler_thread = threading.Thread(target=rss_scheduler, daemon=True)
    scheduler_thread.start()

    last_update_id = 0
    while True:
        try:
            updates = get_updates(offset=last_update_id + 1)
            if updates:
                for update in updates:
                    last_update_id = update["update_id"]
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        if "text" in msg:
                            handle_command(chat_id, msg["text"].strip())
            time.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()