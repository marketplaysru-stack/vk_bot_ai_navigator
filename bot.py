#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот с несколькими генераторами: Agnes → Pexels → Pixazo → Pollinations → баннер
"""

import os
import io
import json
import time
import logging
import random
import re
import requests
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

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

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ========== TELEGRAM ==========
def send_message(chat_id, text):
    url = f"{BASE_URL}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def send_photo(chat_id, photo_bytes, caption=""):
    url = f"{BASE_URL}/sendPhoto"
    files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}
    requests.post(url, data={"chat_id": chat_id, "caption": caption}, files=files)

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    resp = requests.get(url, params=params)
    return resp.json().get("result", [])

# ========== БАННЕР (ЗАГЛУШКА) ==========
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

# ========== AGNES ==========
def generate_agnes(prompt):
    if not AGNES_API_KEY:
        return None
    try:
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        data = {
            "prompt": f"Professional business and technology illustration about {prompt}. Include AI, neural networks, charts, modern office. No people, no nature.",
            "negative_prompt": "ugly, deformed, blurry, nature, trees, forest, landscape, people",
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 30,
            "guidance_scale": 7.0
        }
        resp = requests.post("https://api.agnes.ai/v1/images/generations", json=data, headers=headers, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            image_url = result.get("data", [{}])[0].get("url")
            if image_url:
                img_resp = requests.get(image_url, timeout=30)
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as e:
        logger.warning(f"Agnes не сработал: {e}")
    return None

# ========== PEXELS (УЛУЧШЕННЫЙ) ==========
def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    queries = [
        f"artificial intelligence business {topic}",
        f"AI technology {topic}",
        f"neural network business {topic}",
        f"machine learning {topic}",
        f"AI startup modern office {topic}",
        f"digital transformation technology {topic}",
        f"business technology innovation {topic}"
    ]
    random.shuffle(queries)
    for query in queries:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        params = {"query": query, "per_page": 5, "page": 1}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    # Берём первое фото (без фильтрации, так как запросы уже релевантные)
                    photo_url = photos[0]["src"]["large2x"]
                    logger.info(f"Pexels нашёл фото по запросу '{query}'")
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

# ========== PIXAZO ==========
def generate_pixazo(prompt):
    if not PIXAZO_API_KEY:
        return None
    try:
        url = "https://api.pixazo.com/v1/generate"
        headers = {
            "Authorization": f"Bearer {PIXAZO_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "prompt": f"Professional business and technology illustration about {prompt}. Include AI, neural networks, charts, modern office. No people, no nature.",
            "model": "flux",
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 30,
            "guidance_scale": 7.0
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

# ========== POLLINATIONS ==========
def generate_pollinations(prompt):
    try:
        url = f"{POLLINATIONS_BASE_URL}/prompt/{requests.utils.quote(prompt)}?width=1024&height=1024&nologo=true"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.warning(f"Pollinations не сработал: {e}")
    return None

# ========== ЗАГРУЗКА ФОТО В VK ==========
def upload_photo_to_vk(image_bytes, owner_id, token):
    if not token:
        raise ValueError("Нет VK токена")
    owner_id_abs = abs(owner_id)
    upload_url_api = "https://api.vk.com/method/photos.getWallUploadServer"
    params = {"access_token": token, "group_id": owner_id_abs, "v": "5.131"}
    resp = requests.get(upload_url_api, params=params).json()
    if "error" in resp:
        raise Exception(f"Ошибка получения upload_url: {resp['error']['error_msg']}")
    upload_url = resp["response"]["upload_url"]
    temp_path = f"/tmp/temp_{random.randint(1, 1000000)}.jpg"
    with open(temp_path, "wb") as f:
        f.write(image_bytes)
    files = {"photo": open(temp_path, "rb")}
    resp_upload = requests.post(upload_url, files=files).json()
    os.remove(temp_path)
    if not all(k in resp_upload for k in ("photo", "server", "hash")):
        raise Exception(f"Неполный ответ от сервера загрузки: {resp_upload}")
    save_api = "https://api.vk.com/method/photos.saveWallPhoto"
    params = {
        "access_token": token,
        "group_id": owner_id_abs,
        "photo": resp_upload["photo"],
        "server": resp_upload["server"],
        "hash": resp_upload["hash"],
        "v": "5.131"
    }
    save_resp = requests.post(save_api, data=params).json()
    if "error" in save_resp:
        raise Exception(f"Ошибка сохранения фото: {save_resp['error']['error_msg']}")
    photo_data = save_resp["response"][0]
    return f"photo{photo_data['owner_id']}_{photo_data['id']}"

# ========== ПУБЛИКАЦИЯ В ГРУППУ ==========
def publish_to_group(text, image_bytes):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk(image_bytes, GROUP_ID_AI, VK_TOKEN_AI)
                attachments.append(attachment)
                logger.info("Фото загружено в группу")
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

# ========== ПУБЛИКАЦИЯ АНОНСА НА ЛИЧНУЮ СТРАНИЦУ ==========
def publish_announce_to_user(announce_text, image_bytes, group_link):
    if not VK_TOKEN_USER or not VK_USER_ID:
        return "❌ Нет токена или ID для личной страницы"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk(image_bytes, VK_USER_ID, VK_TOKEN_USER)
                attachments.append(attachment)
                logger.info("Фото загружено на личную стену")
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

# ========== ГЕНЕРАЦИЯ ПОСТА ==========
def generate_post(topic):
    post_text = f"📌 {topic}\n\nЭтот пост подготовлен автоматически. Подписывайтесь, чтобы не пропустить новости!"
    announce_text = f"🔥 Новый пост в группе AI Навигатор: {topic}"
    group_link = f"https://vk.com/club{abs(GROUP_ID_AI)}" if GROUP_ID_AI < 0 else f"https://vk.com/public{GROUP_ID_AI}"

    image_bytes = None
    sources = []

    # 1) Agnes
    if AGNES_API_KEY:
        img = generate_agnes(topic)
        if img:
            image_bytes = img
            sources.append("Agnes")
            logger.info("✅ Картинка от Agnes")

    # 2) Pexels
    if not image_bytes:
        try:
            photo_url = search_pexels_relevant_photo(topic)
            if photo_url:
                img = download_photo(photo_url)
                if img:
                    image_bytes = img
                    sources.append("Pexels")
                    logger.info("✅ Картинка от Pexels")
        except Exception as e:
            logger.warning(f"Pexels не сработал: {e}")

    # 3) Pixazo
    if not image_bytes and PIXAZO_API_KEY:
        try:
            img = generate_pixazo(topic)
            if img:
                image_bytes = img
                sources.append("Pixazo")
                logger.info("✅ Картинка от Pixazo")
        except Exception as e:
            logger.warning(f"Pixazo не сработал: {e}")

    # 4) Pollinations
    if not image_bytes:
        try:
            img = generate_pollinations(topic)
            if img:
                image_bytes = img
                sources.append("Pollinations")
                logger.info("✅ Картинка от Pollinations")
        except Exception as e:
            logger.warning(f"Pollinations не сработал: {e}")

    # 5) Баннер (всегда работает)
    if not image_bytes:
        image_bytes = create_banner(topic[:20])
        sources.append("баннер")
        logger.info("✅ Использован баннер")

    logger.info(f"Источник картинки: {', '.join(sources)}")
    return post_text, announce_text, group_link, image_bytes

# ========== ОБРАБОТЧИК КОМАНД ==========
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "👋 Мульти-генератор бот\n\n"
            "📌 Команды:\n"
            "/post <тема> — опубликовать пост с картинкой\n"
            "/ping — проверить работу бота"
        )
        return

    if text == "/ping":
        send_message(chat_id, "🏓 Pong! Бот работает")
        return

    if text.startswith("/post"):
        topic = text.replace("/post", "").strip()
        if not topic:
            send_message(chat_id, "❌ Укажите тему. Пример: /post Нейросети в бизнесе")
            return
        send_message(chat_id, f"⏳ Генерирую пост на тему: {topic}...")
        post_text, announce_text, group_link, image_bytes = generate_post(topic)

        group_result = publish_to_group(post_text, image_bytes)
        send_message(chat_id, f"📌 Группа:\n{group_result}")

        user_result = publish_announce_to_user(announce_text, image_bytes, group_link)
        send_message(chat_id, f"👤 Анонс на личной странице:\n{user_result}")

        return

    send_message(chat_id, "❓ Неизвестная команда. Напишите /help")

# ========== ЗАПУСК ==========
def main():
    logger.info("🚀 Бот запущен")
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