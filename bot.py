#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простой бот для публикации постов с картинками (без Pillow)
"""

import os
import json
import time
import logging
import random
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VK_TOKEN_AI = os.getenv("VK_TOKEN_AI")
GROUP_ID_AI = int(os.getenv("GROUP_ID_AI", "0"))
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

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

# ========== IMGBB ==========
def upload_to_imgbb(image_bytes):
    if not IMGBB_API_KEY:
        return None
    import base64
    b64 = base64.b64encode(image_bytes).decode('utf-8')
    url = "https://api.imgbb.com/1/upload"
    resp = requests.post(url, data={"key": IMGBB_API_KEY, "image": b64}, timeout=30)
    if resp.status_code == 200 and resp.json().get("success"):
        return resp.json()["data"]["url"]
    return None

# ========== PEXELS (ФОТО) ==========
def search_pexels_photos(query, per_page=1):
    if not PEXELS_API_KEY:
        return []
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": per_page}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("photos", [])
    except Exception as e:
        logger.error(f"Pexels фото ошибка: {e}")
    return []

# ========== VK ПУБЛИКАЦИЯ ==========
def publish_to_vk(text, image_bytes=None):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"

    # Загружаем фото (если есть)
    image_url = None
    if image_bytes:
        image_url = upload_to_imgbb(image_bytes)
        if not image_url:
            logger.warning("Не удалось загрузить фото на imgbb")

    # Публикуем пост
    url = "https://api.vk.com/method/wall.post"
    params = {
        "access_token": VK_TOKEN_AI,
        "owner_id": GROUP_ID_AI,
        "message": text,
        "v": "5.131"
    }
    if GROUP_ID_AI < 0:
        params["from_group"] = 1

    attachments = []
    if image_url:
        attachments.append(image_url)
    if attachments:
        params["attachments"] = ",".join(attachments)

    try:
        resp = requests.get(url, params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK: {resp['error']['error_msg']}"
        return f"✅ Пост опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        return f"❌ Ошибка: {e}"

# ========== ГЕНЕРАЦИЯ ПОСТА ==========
def generate_post(topic):
    """Генерирует текст и картинку (из Pexels)"""
    text = f"📌 {topic}\n\nЭтот пост подготовлен автоматически. Подписывайтесь, чтобы не пропустить новости!"

    # Пробуем найти фото на Pexels
    photos = search_pexels_photos(topic, per_page=1)
    if photos:
        photo_url = photos[0]["src"]["large2x"]
        try:
            img_resp = requests.get(photo_url, timeout=30)
            if img_resp.status_code == 200:
                return text, img_resp.content
        except:
            pass

    # Если фото нет — возвращаем только текст
    return text, None

# ========== ОБРАБОТЧИК КОМАНД ==========
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "👋 Простой бот для постов\n\n"
            "📌 Команды:\n"
            "/post <тема> — сгенерировать и опубликовать пост\n"
            "/photo <запрос> — найти фото на Pexels\n"
            "/ping — проверить работу бота"
        )
        return

    if text == "/ping":
        send_message(chat_id, "🏓 Pong! Бот работает")
        return

    if text.startswith("/photo"):
        query = text.replace("/photo", "").strip()
        if not query:
            send_message(chat_id, "❌ Укажите запрос. Пример: /photo нейросети")
            return
        send_message(chat_id, f"🔍 Ищу фото: {query}")
        photos = search_pexels_photos(query, per_page=1)
        if photos:
            photo_url = photos[0]["src"]["large2x"]
            try:
                img_resp = requests.get(photo_url, timeout=30)
                if img_resp.status_code == 200:
                    send_photo(chat_id, img_resp.content, caption=f"📸 Фото: {query}")
                    return
            except:
                pass
        send_message(chat_id, "❌ Не удалось найти фото")
        return

    if text.startswith("/post"):
        topic = text.replace("/post", "").strip()
        if not topic:
            send_message(chat_id, "❌ Укажите тему. Пример: /post Нейросети в бизнесе")
            return
        send_message(chat_id, f"⏳ Генерирую пост на тему: {topic}...")
        post_text, image_bytes = generate_post(topic)
        result = publish_to_vk(post_text, image_bytes)
        send_message(chat_id, result)
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