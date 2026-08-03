#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный ВК-бот с генерацией постов из списка тем.
Публикует 4 поста в сутки с интервалом 6 часов.
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
import schedule
from datetime import datetime, timedelta
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import vk_api
from vk_api.upload import VkUpload

load_dotenv()

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VK_TOKEN_AI = os.getenv("VK_TOKEN_AI")
GROUP_ID_AI = int(os.getenv("GROUP_ID_AI", "0"))
VK_TOKEN_USER = os.getenv("VK_TOKEN_USER")
VK_USER_ID = int(os.getenv("VK_USER_ID", "0"))

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai")
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT", "ugly, deformed, blurry, low quality, same face, boring, plain, cartoon, doll, mannequin, 3d render, smooth skin, unrealistic, extra limbs, bad anatomy, distorted, people, human, woman, girl, beach, sea, sand, swimsuit, nude, naked, portrait, selfie, smile, face, eyes, hair, meadow, field, hay, grass, farm, cow, horse, rural, village, landscape, trees, forest, nature, road, mountains, countryside, plants, outdoor")

RSS_DEFAULT_GROUP = os.getenv("RSS_DEFAULT_GROUP", "AI Навигатор")
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "post_state.json")   # храним список уже использованных тем за день

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ========== ТЕМЫ ==========
def load_topics():
    """Загружает темы из файла topics.txt или возвращает встроенный список"""
    try:
        with open("topics.txt", "r", encoding="utf-8") as f:
            topics = [line.strip() for line in f if line.strip()]
        if topics:
            return topics
    except FileNotFoundError:
        pass
    # Запасной список (можно изменить)
    return [
        "Искусственный интеллект в бизнесе",
        "Нейросети в медицине",
        "Обучение с подкреплением",
        "Этика ИИ",
        "Генеративные модели",
        "Обработка естественного языка",
        "Компьютерное зрение",
        "Робототехника и ИИ",
        "Будущее работы с ИИ",
        "ИИ в творчестве",
        "Автоматизация с помощью AI",
        "Интернет вещей и ИИ"
    ]

# ========== TELEGRAM ==========
def send_message(chat_id, text):
    url = f"{BASE_URL}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

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
                "messages": [{"role": "user", "content": f"Напиши развернутый пост (около 200 слов) на тему: {topic}. Используй факты, примеры, выводы. Пиши в деловом, но доступном стиле. Добавь эмодзи для выделения ключевых моментов, разбивай текст на абзацы."}],
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
    # Здесь можно адаптировать под тематику (AI, родительская, строительная) – но для универсальности оставим общий
    intro = f"🧠 Сегодня поговорим о **{topic}**. Это важная тема, которая заслуживает внимания.\n"
    body = [
        "📊 Современные исследования подтверждают значимость этого направления.",
        "💡 Применение новых технологий открывает широкие возможности.",
        "🔍 Важно понимать ключевые аспекты и вызовы.",
    ]
    conclusion = "🚀 Будьте в курсе последних трендов и не бойтесь внедрять инновации!"
    return intro + "\n\n" + "\n".join(body) + "\n\n" + conclusion

# ========== ГЕНЕРАТОРЫ КАРТИНОК ==========
def random_seed():
    return random.randint(1, 1000000)

def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    base_queries = [
        f"artificial intelligence {topic}",
        f"technology {topic}",
        f"innovation {topic}",
        f"future {topic}"
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
        full_prompt = f"Professional illustration about {prompt}, technology, modern, no people, no nature"
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
        full_prompt = f"{prompt}, technology, futuristic, professional photo, high quality"
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

# ========== VK ПУБЛИКАЦИЯ ==========
def upload_photo_to_vk_via_vkapi(image_bytes, owner_id, token):
    temp_path = None
    try:
        temp_path = f"/tmp/temp_{random.randint(1, 1000000)}.jpg"
        with open(temp_path, "wb") as f:
            f.write(image_bytes)

        vk = vk_api.VkApi(token=token)
        upload = VkUpload(vk)

        if owner_id < 0:
            group_id = abs(owner_id)
            photo = upload.photo_wall(temp_path, group_id=group_id)
        else:
            photo = upload.photo_wall(temp_path)

        attachment = f"photo{photo[0]['owner_id']}_{photo[0]['id']}"
        logger.info(f"Получен attachment: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        raise
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

def publish_to_group(text, image_bytes):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk_via_vkapi(image_bytes, GROUP_ID_AI, VK_TOKEN_AI)
                attachments.append(attachment)
            except Exception as e:
                logger.error(f"Ошибка загрузки фото в группу: {e}")
                return f"❌ Ошибка загрузки фото: {e}"

        wall_api = "https://api.vk.com/method/wall.post"
        params = {
            "access_token": VK_TOKEN_AI,
            "owner_id": GROUP_ID_AI,
            "message": text,
            "v": "5.131"
        }
        if GROUP_ID_AI < 0:
            params["from_group"] = 1
        if attachments:
            params["attachments"] = ",".join(attachments)

        resp = requests.get(wall_api, params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK (группа): {resp['error']['error_msg']}"
        return f"✅ Пост в группе опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации в группу: {e}")
        return f"❌ Ошибка: {e}"

def publish_announce_to_user(announce_text, image_bytes, group_link):
    if not VK_TOKEN_USER or not VK_USER_ID:
        return "❌ Нет токена или ID для личной страницы"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk_via_vkapi(image_bytes, VK_USER_ID, VK_TOKEN_USER)
                attachments.append(attachment)
            except Exception as e:
                logger.error(f"Ошибка загрузки фото на личную стену: {e}")
                return f"❌ Ошибка загрузки фото: {e}"
        full_text = announce_text + f"\n\n👉 {group_link}" if group_link else announce_text
        wall_api = "https://api.vk.com/method/wall.post"
        params = {
            "access_token": VK_TOKEN_USER,
            "owner_id": VK_USER_ID,
            "message": full_text,
            "v": "5.131"
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        resp = requests.get(wall_api, params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK (личная): {resp['error']['error_msg']}"
        return f"✅ Анонс на личной стене опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации на личную стену: {e}")
        return f"❌ Ошибка: {e}"

def create_post(topic, custom_text=None):
    if custom_text and len(custom_text) > 50:
        post_text = custom_text
    else:
        post_text = generate_text(topic)
    announce_text = f"🔥 Новый пост в группе {RSS_DEFAULT_GROUP}: {topic}"
    group_link = f"https://vk.com/club{abs(GROUP_ID_AI)}" if GROUP_ID_AI < 0 else f"https://vk.com/public{GROUP_ID_AI}"
    image_bytes, source = generate_image(topic)
    return post_text, announce_text, group_link, image_bytes, source

def publish_post(topic, custom_text=None):
    post_text, announce_text, group_link, image_bytes, source = create_post(topic, custom_text)
    group_result = publish_to_group(post_text, image_bytes)
    user_result = publish_announce_to_user(announce_text, image_bytes, group_link)
    return {"group": group_result, "user": user_result, "image_source": source}

# ========== ПЛАНИРОВЩИК (4 поста в сутки с интервалом 6 часов) ==========
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

def get_topic():
    """Возвращает тему, которая ещё не использовалась сегодня"""
    all_topics = load_topics()
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    used = state.get(today, [])
    available = [t for t in all_topics if t not in used]
    if not available:
        # Если все темы использованы, сбрасываем список использованных
        available = all_topics
        used = []
    topic = random.choice(available)
    used.append(topic)
    state[today] = used
    save_state(state)
    return topic

def scheduled_post():
    """Задача, выполняемая каждые 6 часов"""
    logger.info("⏰ Автоматическая публикация (каждые 6 часов)")
    try:
        topic = get_topic()
        logger.info(f"Публикуем тему: {topic}")
        result = publish_post(topic)
        logger.info(f"Результат: {result}")
    except Exception as e:
        logger.error(f"Ошибка в scheduled_post: {e}")

def scheduler_worker():
    """Запускает планировщик: первый пост сразу, затем каждые 6 часов"""
    logger.info("📡 Планировщик запущен (4 поста в сутки, интервал 6 часов)")
    # Первый пост сразу при старте
    scheduled_post()
    # Затем каждые 6 часов
    schedule.every(6).hours.do(scheduled_post)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ========== ОБРАБОТЧИК КОМАНД ==========
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "🤖 Бот с генерацией постов из списка тем.\n"
            "📌 Команды:\n"
            "/post <тема> — сгенерировать и опубликовать пост\n"
            "/post <текст (от 50 символов)> — опубликовать готовый текст с картинкой\n"
            "/ping — проверить работу\n"
            "/status — показать статистику публикаций"
        )
        return

    if text == "/ping":
        send_message(chat_id, "🏓 Pong! Бот работает")
        return

    if text == "/status":
        state = load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        used = state.get(today, [])
        total = len(load_topics())
        send_message(chat_id, f"📊 Сегодня опубликовано {len(used)} тем из {total}.")
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
        send_message(chat_id, f"📌 Группа:\n{result['group']}")
        send_message(chat_id, f"👤 Анонс:\n{result['user']}")
        send_message(chat_id, f"🖼 Источник картинки: {result['image_source']}")
        return

    send_message(chat_id, "❓ Неизвестная команда. Напишите /help")

# ========== ЗАПУСК ==========
def main():
    logger.info("🚀 Бот запущен")

    # Запуск планировщика в отдельном потоке
    scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
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