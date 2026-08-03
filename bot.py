#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Профессиональный бот: генерация картинок по детальным промтам + анонс
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
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT",
    "ugly, deformed, blurry, low quality, same face, boring, plain, cartoon, doll, mannequin, "
    "3d render, smooth skin, unrealistic, extra limbs, bad anatomy, distorted, people, human, woman, girl, "
    "beach, sea, sand, swimsuit, nude, naked, portrait, selfie, smile, face, eyes, hair, "
    "meadow, field, hay, grass, farm, cow, horse, rural, village, landscape, "
    "trees, forest, nature, road, mountains, countryside, plants, outdoor"
)

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

# ========== ГЕНЕРАЦИЯ ДЕТАЛЬНОГО ПРОМТА ==========
def build_image_prompt(topic: str) -> str:
    """Создаёт детальный кинематографичный промт на основе темы."""
    return (
        f"Hyperrealistic cinematic photograph, vertical 9:16, minimalist wide shot, centred composition. "
        f"A deep, rich midnight-blue background with subtle, out-of-focus neural network patterns and "
        f"data streams drifting vertically like digital rain. "
        f"In the centre, a stylized glowing compass made of brushed gold and silver, with a neural network "
        f"pattern inside the compass rose — symbolizing '{topic}'. "
        f"Around the compass, floating holographic icons representing modern technology: AI chips, data charts, "
        f"gears, light bulbs, and abstract circuit boards. "
        f"Below, a subtle glowing tagline: 'AI Навигатор — твой проводник в мир ИИ'. "
        f"Lighting: The compass emits a warm golden glow that illuminates the surrounding darkness. "
        f"Soft cyan and violet accents from the data streams. "
        f"Sensory details: Brushed metal texture, fine scratches, luminous lines, tiny golden particles "
        f"drifting slowly. "
        f"Mood: Premium, trustworthy, inspiring. "
        f"Color palette: Deep midnight blue, rich gold, soft silver, subtle cyan and violet accents. "
        f"Style: Premium branding, Apple keynote aesthetic, shallow depth of field (f/2.8) focused on the compass, "
        f"8K, fine grain, no people, no faces, no nature. "
        f"--ar 9:16 --style raw --s 700 --v 6.0"
    )

# ========== AGNES (ГЕНЕРАЦИЯ ПО ПРОМТУ) ==========
def generate_image_agnes(prompt: str) -> bytes:
    if not AGNES_API_KEY:
        raise ValueError("AGNES_API_KEY не задан")
    headers = {
        "Authorization": f"Bearer {AGNES_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "prompt": prompt,
        "negative_prompt": IMAGE_NEGATIVE_PROMPT,
        "width": 1024,
        "height": 1024,
        "num_inference_steps": 30,
        "guidance_scale": 7.0
    }
    logger.info(f"Отправка запроса в Agnes (длина промта: {len(prompt)})")
    resp = requests.post("https://api.agnes.ai/v1/images/generations", json=data, headers=headers, timeout=120)
    resp.raise_for_status()
    result = resp.json()
    image_url = result.get("data", [{}])[0].get("url")
    if not image_url:
        raise Exception("Agnes не вернул URL")
    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()
    return img_resp.content

# ========== PEXELS (РЕЗЕРВ) ==========
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
        logger.error(f"Pexels ошибка: {e}")
    return []

def download_pexels_photo(photo_url):
    try:
        resp = requests.get(photo_url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except:
        pass
    return None

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

# ========== ЗАГРУЗКА ФОТО В VK ==========
def upload_photo_to_vk(image_bytes, owner_id, token):
    if not token:
        raise ValueError("Нет VK токена")
    owner_id_abs = abs(owner_id)
    # 1) Получаем URL для загрузки
    upload_url_api = "https://api.vk.com/method/photos.getWallUploadServer"
    params = {"access_token": token, "group_id": owner_id_abs, "v": "5.131"}
    resp = requests.get(upload_url_api, params=params).json()
    if "error" in resp:
        raise Exception(f"Ошибка получения upload_url: {resp['error']['error_msg']}")
    upload_url = resp["response"]["upload_url"]
    # 2) Загружаем фото
    temp_path = f"/tmp/temp_{random.randint(1, 1000000)}.jpg"
    with open(temp_path, "wb") as f:
        f.write(image_bytes)
    files = {"photo": open(temp_path, "rb")}
    resp_upload = requests.post(upload_url, files=files).json()
    os.remove(temp_path)
    if not all(k in resp_upload for k in ("photo", "server", "hash")):
        raise Exception(f"Неполный ответ от сервера загрузки: {resp_upload}")
    # 3) Сохраняем фото на стену
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

    # 1) Строим детальный промт и пробуем Agnes
    try:
        prompt = build_image_prompt(topic)
        logger.info(f"Генерация через Agnes с промтом (длина {len(prompt)})")
        img_bytes = generate_image_agnes(prompt)
        if img_bytes:
            image_bytes = img_bytes
            logger.info("Agnes успешно сгенерировал картинку")
    except Exception as e:
        logger.warning(f"Agnes не сработал: {e}")

    # 2) Если Agnes не дал картинку — Pexels
    if not image_bytes:
        try:
            logger.info(f"Поиск фото на Pexels: {topic}")
            photos = search_pexels_photos(topic, per_page=1)
            if photos:
                photo_url = photos[0]["src"]["large2x"]
                img_bytes = download_pexels_photo(photo_url)
                if img_bytes:
                    image_bytes = img_bytes
                    logger.info("Pexels дал фото")
        except Exception as e:
            logger.warning(f"Pexels не сработал: {e}")

    # 3) Если ничего нет — баннер
    if not image_bytes:
        logger.info("Создаём баннер-заглушку")
        image_bytes = create_banner(topic[:20])

    return post_text, announce_text, group_link, image_bytes

# ========== ОБРАБОТЧИК КОМАНД ==========
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "👋 Профессиональный бот\n\n"
            "📌 Команды:\n"
            "/post <тема> — сгенерировать и опубликовать пост с качественной картинкой\n"
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
        post_text, announce_text, group_link, image_bytes = generate_post(topic)

        # Публикуем в группу
        group_result = publish_to_group(post_text, image_bytes)
        send_message(chat_id, f"📌 Группа:\n{group_result}")

        # Публикуем анонс на личную стену (короткий)
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