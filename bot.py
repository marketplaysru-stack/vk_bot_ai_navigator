#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор (пульт) – бот с автопостингом 4 раза в сутки.
Темы из topics.txt, генерация через Agnes, резерв – тематический шаблон.
"""

import os, io, json, time, logging, random, re, requests, threading, schedule
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

# ---------- ТЕМЫ ----------
def load_topics():
    try:
        with open("topics.txt", "r", encoding="utf-8") as f:
            topics = [line.strip() for line in f if line.strip()]
        if topics:
            return topics
    except FileNotFoundError:
        pass
    # Запасной список (AI-тематика)
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

# ---------- ГЕНЕРАЦИЯ ТЕКСТА ----------
def generate_text(topic: str) -> str:
    # Пытаемся через Agnes
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            prompt = f"Напиши развернутый пост (около 200 слов) на тему: {topic}. Используй факты, примеры, выводы. Пиши в деловом, но доступном стиле. Добавь эмодзи для выделения ключевых моментов, разбивай текст на абзацы."
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

    # Резервный шаблон (тематический, развёрнутый)
    return generate_ai_template(topic)

def generate_ai_template(topic: str) -> str:
    intro = f"🧠 **{topic}** — одна из ключевых тем современного мира ИИ.\n\n"
    body = [
        "📊 Исследования показывают, что внедрение AI в этой области повышает эффективность на 30–40%.",
        "💡 Уже сегодня нейросети помогают автоматизировать рутинные задачи и находить неочевидные решения.",
        "🔍 Специалисты по данным становятся самыми востребованными на рынке труда.",
        "⚡️ Однако важно помнить о вызовах: этика, безопасность и необходимость постоянного обучения.",
        "🌍 Перспективы — от персонализированной медицины до управления умными городами."
    ]
    conclusion = "🚀 Следите за развитием технологий, чтобы не отставать от будущего!"
    return intro + "\n".join(random.sample(body, k=4)) + "\n\n" + conclusion

# ---------- КАРТИНКИ (сокращённо, но работоспособно) ----------
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
    group_res = publish_to_group(post_text, image_bytes)
    user_res = publish_announce_to_user(announce_text, image_bytes, group_link)
    return {"group": group_res, "user": user_res, "source": source}

# ---------- ПЛАНИРОВЩИК ----------
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def get_topic():
    all_topics = load_topics()
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    used = state.get(today, [])
    available = [t for t in all_topics if t not in used]
    if not available:
        available = all_topics
        used = []
    topic = random.choice(available)
    used.append(topic)
    state[today] = used
    save_state(state)
    return topic

def scheduled_post():
    logger.info("⏰ Автопостинг (каждые 6 часов)")
    try:
        topic = get_topic()
        result = publish_post(topic)
        logger.info(f"Результат: {result}")
    except Exception as e:
        logger.error(f"Ошибка автопостинга: {e}")

def scheduler_worker():
    logger.info("📡 Планировщик запущен (4 поста в сутки)")
    scheduled_post()
    schedule.every(6).hours.do(scheduled_post)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ---------- КОМАНДЫ ----------
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id, "🤖 Бот AI Навигатор. Команды: /post <тема>, /ping, /status")
        return
    if text == "/ping":
        send_message(chat_id, "🏓 Pong!")
        return
    if text == "/status":
        state = load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        used = state.get(today, [])
        send_message(chat_id, f"📊 Сегодня опубликовано {len(used)} тем.")
        return
    if text.startswith("/post"):
        content = text.replace("/post", "").strip()
        if not content:
            send_message(chat_id, "❌ Укажите тему.")
            return
        result = publish_post(content)
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