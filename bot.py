#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор (пульт) – публикует готовые посты из файла posts.txt,
перерабатывает их через Agnes (если ключ есть), иначе генерирует по темам.
Автопостинг каждые 6 часов.
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
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import vk_api
from vk_api.upload import VkUpload

load_dotenv()

# ---------- НАСТРОЙКИ ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VK_TOKEN_AI = os.getenv("VK_TOKEN_AI")
GROUP_ID_AI = int(os.getenv("GROUP_ID_AI", "-240273450"))
VK_TOKEN_USER = os.getenv("VK_TOKEN_USER")
VK_USER_ID = int(os.getenv("VK_USER_ID", "317272476"))

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL")
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT", "ugly, deformed, blurry...")
RSS_DEFAULT_GROUP = os.getenv("RSS_DEFAULT_GROUP", "AI Навигатор")
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "post_state.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ---------- ЗАГРУЗКА ПОСТОВ ИЗ ФАЙЛА ----------
POSTS_FILE = "posts.txt"

def load_posts_from_file():
    """
    Читает файл posts.txt, возвращает список словарей: [{'title': '...', 'text': '...'}, ...]
    Ожидается формат:
    === Заголовок ===
    Текст поста (может быть несколько абзацев)
    ...
    (пустая строка или следующий ===)
    """
    posts = []
    if not os.path.exists(POSTS_FILE):
        return None
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        # Разбиваем по маркеру ===
        blocks = re.split(r'\n===', content)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            # Убираем первый маркер, если есть
            if block.startswith('==='):
                block = block[3:].strip()
            lines = block.split('\n')
            if not lines:
                continue
            # Первая строка — заголовок
            title = lines[0].strip()
            # Остальное — текст
            text = '\n'.join(lines[1:]).strip()
            if title and text:
                posts.append({'title': title, 'text': text})
        if posts:
            logger.info(f"✅ Загружено {len(posts)} готовых постов из {POSTS_FILE}")
            return posts
        else:
            logger.warning(f"Файл {POSTS_FILE} есть, но не удалось разобрать посты. Использую запасной список тем.")
            return None
    except Exception as e:
        logger.error(f"Ошибка чтения {POSTS_FILE}: {e}")
        return None

# ---------- ТЕМЫ (если нет файла постов) ----------
def load_topics():
    try:
        with open("topics.txt", "r", encoding="utf-8") as f:
            topics = [line.strip() for line in f if line.strip()]
        if topics:
            return topics
    except FileNotFoundError:
        pass
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
        "ИИ в творчестве"
    ]

# ---------- TELEGRAM ----------
def send_message(chat_id, text):
    requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": text})

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    return requests.get(f"{BASE_URL}/getUpdates", params=params).json().get("result", [])

# ---------- ГЕНЕРАЦИЯ ТЕКСТА (если нет готового) ----------
def generate_text(topic: str) -> str:
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            prompt = f"Напиши развернутый пост (около 200 слов) на тему: {topic}. Используй факты, примеры, выводы. Добавь эмодзи и абзацы."
            data = {
                "model": "agnes-v1",
                "messages": [{"role": "user", "content": prompt}],
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
            logger.warning(f"Agnes не сработал: {e}")

    return generate_ai_template(topic)

def generate_ai_template(topic: str) -> str:
    intro = f"🧠 **{topic}** — важная тема в мире ИИ.\n\n"
    body = [
        "📊 Исследования показывают рост эффективности на 30–40%.",
        "💡 Нейросети автоматизируют рутинные задачи.",
        "🔍 Специалисты по данным — самые востребованные.",
        "⚡️ Этические вопросы и безопасность — вызовы.",
        "🌍 Перспективы: от медицины до умных городов."
    ]
    conclusion = "🚀 Следите за трендами!"
    return intro + "\n".join(random.sample(body, k=4)) + "\n\n" + conclusion

# ---------- КАРТИНКИ ----------
def random_seed():
    return random.randint(1, 1000000)

def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    queries = [f"artificial intelligence {topic}", f"technology {topic}", f"innovation {topic}"]
    random.shuffle(queries)
    for query in queries[:3]:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        params = {"query": query, "per_page": 3, "orientation": "landscape"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    return random.choice(photos)["src"]["large2x"]
        except:
            pass
    return None

def download_photo(url):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except:
        pass
    return None

def generate_image(topic):
    if PEXELS_API_KEY:
        photo_url = search_pexels_relevant_photo(topic)
        if photo_url:
            img = download_photo(photo_url)
            if img:
                return img, "Pexels"
    # Баннер
    img = Image.new('RGB', (1024, 1024), color='#0a0a2e')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
    except:
        font = ImageFont.load_default()
    draw.text((50, 400), topic[:20], fill='#FFD700', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue(), "баннер"

# ---------- VK ПУБЛИКАЦИЯ ----------
def upload_photo_to_vk_via_vkapi(image_bytes, owner_id, token):
    temp_path = None
    try:
        temp_path = f"/tmp/temp_{random.randint(1, 1000000)}.jpg"
        with open(temp_path, "wb") as f:
            f.write(image_bytes)
        vk = vk_api.VkApi(token=token)
        upload = VkUpload(vk)
        if owner_id < 0:
            photo = upload.photo_wall(temp_path, group_id=abs(owner_id))
        else:
            photo = upload.photo_wall(temp_path)
        attachment = f"photo{photo[0]['owner_id']}_{photo[0]['id']}"
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
        resp = requests.get("https://api.vk.com/method/wall.post", params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK: {resp['error']['error_msg']}"
        return f"✅ Пост в группе опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации в группу: {e}")
        return f"❌ Ошибка: {e}"

def publish_announce_to_user(announce_text, image_bytes, group_link):
    if not VK_TOKEN_USER or not VK_USER_ID:
        return "❌ Нет токена для личной стены"
    try:
        attachments = []
        if image_bytes:
            attachment = upload_photo_to_vk_via_vkapi(image_bytes, VK_USER_ID, VK_TOKEN_USER)
            attachments.append(attachment)
        full_text = announce_text + f"\n\n👉 {group_link}" if group_link else announce_text
        params = {
            "access_token": VK_TOKEN_USER,
            "owner_id": VK_USER_ID,
            "message": full_text,
            "v": "5.131"
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        resp = requests.get("https://api.vk.com/method/wall.post", params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK (личная): {resp['error']['error_msg']}"
        return f"✅ Анонс опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        return f"❌ Ошибка: {e}"

# ---------- СОЗДАНИЕ ПОСТА (с рерайтом через Agnes) ----------
def create_post_content(title, text=None):
    """
    Если text передан — используем его как готовый пост.
    Если есть Agnes, пытаемся переформулировать текст (рерайт), чтобы сделать его более увлекательным.
    Иначе генерируем текст по заголовку.
    """
    post_text = None
    if text and len(text) > 50:
        # Пробуем рерайт через Agnes, если ключ есть
        if AGNES_API_KEY:
            try:
                headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
                prompt = f"Перепиши следующий текст, сохранив смысл, но сделай его более увлекательным, добавь эмодзи, разбей на абзацы. Текст:\n\n{text}"
                data = {
                    "model": "agnes-v1",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.7
                }
                resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
                if resp.status_code == 200:
                    rewritten = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if rewritten and len(rewritten) > 50:
                        post_text = rewritten.strip()
                        logger.info("✅ Рерайт текста через Agnes выполнен")
            except Exception as e:
                logger.warning(f"Рерайт не удался: {e}")
        # Если рерайт не удался или Agnes нет, используем оригинальный текст
        if not post_text:
            post_text = text
    else:
        # Если готового текста нет, генерируем по заголовку
        post_text = generate_text(title)
    
    announce_text = f"🔥 Новый пост в группе {RSS_DEFAULT_GROUP}: {title}"
    group_link = f"https://vk.com/club{abs(GROUP_ID_AI)}" if GROUP_ID_AI < 0 else f"https://vk.com/public{GROUP_ID_AI}"
    image_bytes, source = generate_image(title)
    return post_text, announce_text, group_link, image_bytes, source

def publish_post_item(title, text=None):
    post_text, announce_text, group_link, image_bytes, source = create_post_content(title, text)
    group_res = publish_to_group(post_text, image_bytes)
    user_res = publish_announce_to_user(announce_text, image_bytes, group_link)
    return {"group": group_res, "user": user_res, "source": source}

# ---------- ЗАГРУЗКА ПОСТОВ И УПРАВЛЕНИЕ СОСТОЯНИЕМ ----------
# Глобальный список постов (загружается один раз при старте)
POSTS_POOL = None

def get_posts_pool():
    global POSTS_POOL
    if POSTS_POOL is None:
        # Пробуем загрузить готовые посты из файла
        posts = load_posts_from_file()
        if posts:
            POSTS_POOL = posts
        else:
            # Если файла нет, создаём список тем (сгенерируем текст позже)
            topics = load_topics()
            POSTS_POOL = [{'title': t, 'text': None} for t in topics]
        logger.info(f"📚 Всего доступно постов/тем: {len(POSTS_POOL)}")
    return POSTS_POOL

def get_next_post():
    """Возвращает следующий пост из пула, избегая повторов за день"""
    pool = get_posts_pool()
    if not pool:
        return None, None
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    used_indices = state.get(today, [])
    # ищем неиспользованный индекс
    available = [i for i in range(len(pool)) if i not in used_indices]
    if not available:
        # если все использованы, сбрасываем и используем все заново
        available = list(range(len(pool)))
        used_indices = []
    idx = random.choice(available)
    used_indices.append(idx)
    state[today] = used_indices
    save_state(state)
    post = pool[idx]
    return post['title'], post['text']

# ---------- СОСТОЯНИЕ ----------
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ---------- ПЛАНИРОВЩИК ----------
def scheduled_post():
    logger.info("⏰ Автопостинг (каждые 6 часов)")
    try:
        title, text = get_next_post()
        if not title:
            logger.warning("Нет доступных постов для публикации")
            return
        result = publish_post_item(title, text)
        logger.info(f"Результат: {result}")
    except Exception as e:
        logger.error(f"Ошибка автопостинга: {e}")

def scheduler_worker():
    logger.info("📡 Планировщик запущен (4 поста в сутки)")
    scheduled_post()  # первый пост сразу
    schedule.every(6).hours.do(scheduled_post)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ---------- КОМАНДЫ ----------
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "🤖 Бот AI Навигатор (посты из файла или генерация).\n"
            "/post <заголовок> — опубликовать с генерацией текста\n"
            "/post <текст длиннее 50 символов> — опубликовать готовый текст с картинкой (рерайт через Agnes, если доступен)\n"
            "/ping — проверка\n"
            "/status — статистика"
        )
        return
    if text == "/ping":
        send_message(chat_id, "🏓 Pong!")
        return
    if text == "/status":
        state = load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        used = state.get(today, [])
        total = len(get_posts_pool())
        send_message(chat_id, f"📊 Сегодня опубликовано {len(used)} постов из {total}.")
        return
    if text.startswith("/post"):
        content = text.replace("/post", "").strip()
        if not content:
            send_message(chat_id, "❌ Укажите тему или готовый текст.")
            return
        # Определяем, что ввели: если длиннее 50 символов — считаем готовым текстом, иначе — заголовок
        if len(content) > 50:
            title = content[:50] + "..."
            result = publish_post_item(title, content)  # публикуем готовый текст с рерайтом
        else:
            result = publish_post_item(content)  # генерируем текст по заголовку
        send_message(chat_id, f"📌 Группа: {result['group']}\n👤 Анонс: {result['user']}")
        return

# ---------- ЗАПУСК ----------
def main():
    logger.info("🚀 Бот запущен")
    threading.Thread(target=scheduler_worker, daemon=True).start()
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