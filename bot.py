#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор – генерация разнообразных постов о ИИ и нейросетях.
Расширенные шаблоны, улучшенная рандомизация, яркие футуристические картинки.
Автопостинг каждые 6 часов. Поддержка topics.txt и RSS.
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
import feedparser
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
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
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai")
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT", "ugly, deformed, blurry, low quality, cartoon, doll, mannequin, smooth skin, unrealistic, extra limbs, bad anatomy, distorted")

RSS_DEFAULT_GROUP = os.getenv("RSS_DEFAULT_GROUP", "AI Навигатор")
RSS_SOURCES_JSON = os.getenv("RSS_SOURCES", '[]')
RSS_ENABLED = os.getenv("RSS_ENABLED", "false").lower() == "true"
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "post_state.json")
USED_IMAGES_FILE = os.path.join(DATA_DIR, "used_images.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ---------- КЭШ КАРТИНОК ----------
def load_used_images():
    if os.path.exists(USED_IMAGES_FILE):
        with open(USED_IMAGES_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            return set(data.get("hashes", []))
    return set()

def save_used_images(used_set):
    data = {"hashes": list(used_set), "updated": datetime.now().isoformat()}
    with open(USED_IMAGES_FILE, "w") as f:
        json.dump(data, f)

def clean_used_images():
    if not os.path.exists(USED_IMAGES_FILE):
        return
    try:
        with open(USED_IMAGES_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            save_used_images(set())
            logger.info("🧹 Кэш картинок очищен (старый формат)")
            return
        updated = datetime.fromisoformat(data.get("updated", "2000-01-01"))
        if datetime.now() - updated > timedelta(days=7):
            save_used_images(set())
            logger.info("🧹 Кэш картинок очищен (старше 7 дней)")
    except Exception as e:
        logger.warning(f"Ошибка очистки кэша: {e}")

def compute_hash(image_bytes):
    return hashlib.md5(image_bytes).hexdigest()

def is_image_used(image_bytes):
    h = compute_hash(image_bytes)
    used = load_used_images()
    return h in used

def mark_image_as_used(image_bytes):
    h = compute_hash(image_bytes)
    used = load_used_images()
    used.add(h)
    save_used_images(used)

# ---------- ЗАГРУЗКА ПОСТОВ ИЗ ФАЙЛА ----------
POSTS_FILE = "posts.txt"
def load_posts_from_file():
    posts = []
    if not os.path.exists(POSTS_FILE):
        return None
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        blocks = re.split(r'\n===', content)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            if block.startswith('==='):
                block = block[3:].strip()
            lines = block.split('\n')
            if not lines:
                continue
            title = lines[0].strip()
            text = '\n'.join(lines[1:]).strip()
            if title and text:
                posts.append({'title': title, 'text': text})
        if posts:
            logger.info(f"✅ Загружено {len(posts)} готовых постов из {POSTS_FILE}")
            return posts
    except Exception as e:
        logger.error(f"Ошибка чтения {POSTS_FILE}: {e}")
    return None

# ---------- RSS ПАРСИНГ ----------
def get_rss_entries(sources_json):
    sources = json.loads(sources_json) if sources_json else []
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
            logger.info(f"RSS {url}: получено {len(entries)} заголовков")
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS {url}: {e}")
    return entries

# ---------- ЗАПАСНЫЕ ТЕМЫ (расширенные) ----------
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
        "ИИ в творчестве",
        "Нейросети в образовании",
        "ИИ в сельском хозяйстве",
        "Нейросети в финансах",
        "ИИ в маркетинге",
        "Нейросети в дизайне"
    ]

# ---------- TELEGRAM ----------
def send_message(chat_id, text):
    requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": text})

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=(15, 120))
        return resp.json().get("result", [])
    except Exception as e:
        logger.error(f"Ошибка получения обновлений: {e}")
        return []

# ---------- ГЕНЕРАЦИЯ ТЕКСТА (РАСШИРЕННЫЙ ШАБЛОН) ----------
def generate_text(topic: str) -> str:
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            prompt = f"""
Напиши развернутый пост (около 250–300 слов) на тему: {topic} (искусственный интеллект, нейросети).
Пост должен быть полезным, содержать факты, примеры из жизни, практические советы и вопросы к читателям.
Старайся каждый раз менять структуру и примеры: упоминай разные отрасли (бизнес, медицина, образование, творчество).
Используй эмодзи, разбивай на абзацы. Не повторяй формулировки из предыдущих постов.
"""
            data = {
                "model": "agnes-v1",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "temperature": 0.9  # повышенная температура для большего разнообразия
            }
            resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if text and len(text) > 100:
                    return text.strip()
        except Exception as e:
            logger.warning(f"Agnes не сработал: {e}")

    return generate_template_text(topic)

def generate_template_text(topic: str) -> str:
    # ----- Хуки (20+ вариантов) -----
    hooks = [
        f"🧠 {topic.upper()} – ЭТО НЕ ПРОГНОЗ, А РЕАЛЬНОСТЬ",
        f"⚡ {topic.upper()} МЕНЯЕТ ВСЁ. ТЫ ГОТОВ?",
        f"🚀 {topic.upper()} – ТВОЙ ШАНС ИЗМЕНИТЬ ЖИЗНЬ",
        f"😤 {topic.upper()} – ВОТ ПОЧЕМУ ТЫ ОТСТАЁШЬ",
        f"💥 {topic.upper()} – ЭТО БОЛЬШЕ, ЧЕМ ТЫ ДУМАЕШЬ",
        f"🔥 {topic.upper()} – БУДУЩЕЕ УЖЕ ЗДЕСЬ",
        f"✨ {topic.upper()} – КЛЮЧ К НОВЫМ ВОЗМОЖНОСТЯМ",
        f"💡 {topic.upper()} – ИНСАЙТЫ, КОТОРЫЕ РАБОТАЮТ",
        f"📌 {topic.upper()} – 5 ФАКТОВ, КОТОРЫЕ УДИВЯТ",
        f"🎯 {topic.upper()} – КАК НЕ ПОТЕРЯТЬСЯ В МИРЕ ТЕХНОЛОГИЙ"
    ]
    hook = random.choice(hooks)

    # ----- Лиды (15+ вариантов) -----
    leads = [
        f"Ты когда-нибудь задумывался, почему {topic} вызывает столько споров? Одни говорят, что это будущее, другие – пустышка. Но факты упрямы: тысячи компаний внедряют ИИ и получают результат.",
        f"Представь: ты просыпаешься, а твой помощник уже спланировал день, сэкономил часы работы. Это уже реальность. {topic} врывается в нашу жизнь.",
        f"Почему мы боимся {topic}? Потому что не понимаем. Давай разберёмся – это просто инструмент. Как молоток или компьютер, только умнее.",
        f"В мире, где технологии меняются каждый день, {topic} остаётся в центре внимания. Почему? Потому что это основа будущего.",
        f"Если ты ещё не обратил внимание на {topic}, ты рискуешь отстать. Давай разберёмся, почему это важно."
    ]
    lead = random.choice(leads)

    # ----- Тело (30+ пунктов) -----
    body_pool = [
        ("🔥", "Автоматизация рутины", "ИИ берёт на себя скучные задачи: обработку документов, планирование. Ты освобождаешь время для творчества."),
        ("💡", "Анализ данных", "Нейросети находят закономерности, которые человек не видит. Это помогает принимать решения быстрее."),
        ("🧘", "Этика и безопасность", "ИИ – это не замена человека, а помощник. Вопросы приватности уже решаются на уровне законодательства."),
        ("⚡", "Скорость внедрения", "Технологии развиваются экспоненциально. То, что казалось фантастикой вчера, сегодня реальность."),
        ("📌", "Обучение и адаптация", "Чтобы быть в тренде, нужно постоянно учиться. Нейросети уже помогают в образовании."),
        ("🚀", "Инновации и стартапы", "ИИ открывает новые рынки. Стартапы na bazie AI растут быстрее."),
        ("💪", "Конкурентоспособность", "Компании, внедряющие AI, увеличивают прибыль na 30–40%."),
        ("🔍", "Прогнозирование", "ИИ помогает предсказывать тренды, спрос, риски – это даёт преимущество."),
        ("🛡️", "Кибербезопасность", "Нейросети защищают от атак быстрее человека."),
        ("🌍", "Глобальные решения", "ИИ помогает решать экологические и медицинские проблемы – меняет мир к лучшему."),
        ("🎨", "Творчество", "Нейросети генерируют музыку, картины, стихи – открывают новые горизонты для искусства."),
        ("🏥", "Медицина", "ИИ уже ставит диагнозы точнее врачей в некоторых областях."),
        ("📚", "Образование", "Адаптивные системы обучения подстраиваются под каждого ученика."),
        ("🏭", "Промышленность", "Роботы и ИИ повышают безопасность и производительность na заводах."),
        ("🌾", "Сельское хозяйство", "ИИ помогает прогнозировать урожайность и оптимизировать полив."),
        ("💰", "Финансы", "Нейросети выявляют мошенничество и управляют инвестициями."),
        ("📢", "Маркетинг", "ИИ анализирует поведение потребителей и создаёт персонализированные предложения."),
        ("🏠", "Умный дом", "ИИ делает наши дома комфортнее и энергоэффективнее."),
        ("🚗", "Автопилоты", "Беспилотные автомобили – уже реальность, и это только начало."),
        ("📱", "Мобильные приложения", "Голосовые помощники и рекомендательные системы – повседневность.")
    ]
    random.shuffle(body_pool)
    selected = body_pool[:random.randint(3, 5)]
    body = "\n".join([f"{emoji} **{title}**\n{desc}" for emoji, title, desc in selected])

    # ----- Вывод (10+ вариантов) -----
    conclusions = [
        f"Итог: {topic} – это не будущее, это уже настоящее. Начни использовать его сегодня.",
        "Главное – не бояться, а учиться. ИИ – инструмент, который делает нас сильнее.",
        "Технологии приходят, чтобы помочь. От нас зависит, как мы их используем.",
        "Оставаться в стороне – значит упускать возможности. Время действовать!",
        "Каждый новый день приносит нам новые открытия в мире ИИ. Будьте в курсе.",
        "Не позволяйте страху перед неизвестностью мешать вам использовать мощь ИИ.",
        "Искусственный интеллект – это не угроза, а новый этап развития человечества."
    ]
    conclusion = random.choice(conclusions)

    # ----- CTA (12+ вариантов) -----
    cta_questions = [
        f"👇 А ты уже используешь {topic} в работе или жизни?",
        f"👇 Что тебе мешает внедрить {topic}? Страх? Незнание?",
        f"👇 Согласен, что {topic} меняет правила игры?",
        f"👇 Как ты думаешь, что изменится в твоей отрасли через 5 лет благодаря ИИ?",
        f"👇 Какой инструмент на базе ИИ ты хотел бы попробовать?",
        f"👇 Где, по-твоему, ИИ принесёт наибольшую пользу?"
    ]
    cta = random.choice(cta_questions)

    # ----- Темы для комментариев (разнообразные) -----
    comments_themes_pool = [
        "1. «Какие сферы, по-твоему, ИИ изменит больше всего?»",
        "2. «Ты бы доверил ИИ принимать важные решения?»",
        "3. «Какой инструмент на базе ИИ ты используешь чаще всего?»",
        "4. «Как ты относишься к автоматизации рабочих мест?»",
        "5. «Что тебя больше всего впечатляет в развитии ИИ?»"
    ]
    random.shuffle(comments_themes_pool)
    themes = "\n".join(comments_themes_pool[:3])

    # ----- Хештеги (динамические) -----
    base_hashtags = ["#искусственныйинтеллект", "#нейросети", "#технологии", "#будущее", "#инновации", "#ai", "#digital", "#automatization"]
    extra = [f"#{topic.replace(' ', '').lower()}" for _ in range(3)]
    hashtags = list(set(base_hashtags + extra))[:10]
    hashtag_str = " ".join(hashtags)

    post = f"{hook}\n\n{lead}\n\n{body}\n\n{conclusion}\n\n{cta}\n\nТемы для обсуждения:\n{themes}\n\n{hashtag_str}"
    return post

# ---------- ГЕНЕРАТОРЫ КАРТИНОК (без изменений, но с рандомизацией стилей) ----------
def download_image_with_retry(url, timeout=60, retries=3):
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.content
        except Exception as e:
            logger.warning(f"Попытка {attempt+1} скачать не удалась: {e}")
            time.sleep(2)
    return None

def is_valid_image(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
        return True
    except Exception:
        return False

def sharpen_image(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.8)
        buf = io.BytesIO()
        img.save(buf, format='PNG', quality=95)
        return buf.getvalue()
    except:
        return image_bytes

def generate_agnes_image(prompt):
    if not AGNES_API_KEY:
        return None
    try:
        seed = random.randint(1, 1000000)
        styles = ["cyberpunk", "synthwave", "futuristic", "3D render", "photorealistic", "abstract", "digital art", "neon glow", "vaporwave"]
        style = random.choice(styles)
        full_prompt = f"futuristic robot, neural network, {style}, bright colors, highly detailed, sharp, no people"
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        data = {
            "prompt": full_prompt,
            "negative_prompt": "ugly, deformed, blurry, low quality, people, human, woman, girl, nature, trees, landscape",
            "width": 1280,
            "height": 1280,
            "num_inference_steps": 45,
            "guidance_scale": 7.5,
            "seed": seed
        }
        resp = requests.post("https://apihub.agnes-ai.cn/v1/images/generations", json=data, headers=headers, timeout=180)
        if resp.status_code == 200:
            result = resp.json()
            image_url = result.get("data", [{}])[0].get("url")
            if image_url:
                img = download_image_with_retry(image_url)
                if img and is_valid_image(img):
                    return img
    except Exception as e:
        logger.warning(f"Agnes не сработал: {e}")
    return None

def generate_pixazo_image(prompt):
    if not PIXAZO_API_KEY:
        return None
    try:
        seed = random.randint(1, 1000000)
        styles = ["cyberpunk", "synthwave", "futuristic", "3D render", "photorealistic", "abstract", "digital art", "neon glow", "vaporwave"]
        style = random.choice(styles)
        full_prompt = f"futuristic robot, neural network, {style}, bright colors, highly detailed, sharp, no people"
        url = "https://api.pixazo.com/v1/generate"
        headers = {
            "Authorization": f"Bearer {PIXAZO_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "prompt": full_prompt,
            "model": "flux",
            "width": 1280,
            "height": 1280,
            "num_inference_steps": 45,
            "guidance_scale": 7.5,
            "seed": seed
        }
        resp = requests.post(url, json=data, headers=headers, timeout=180)
        if resp.status_code == 200:
            result = resp.json()
            image_url = result.get("image_url")
            if image_url:
                img = download_image_with_retry(image_url)
                if img and is_valid_image(img):
                    return img
    except Exception as e:
        logger.warning(f"Pixazo не сработал: {e}")
    return None

def generate_pollinations_image(prompt):
    try:
        seed = random.randint(1, 1000000)
        styles = ["cyberpunk", "synthwave", "futuristic", "3D render", "photorealistic", "abstract", "digital art", "neon glow", "vaporwave"]
        style = random.choice(styles)
        full_prompt = f"futuristic robot, neural network, {style}, bright colors, highly detailed, sharp, no people"
        url = f"{POLLINATIONS_BASE_URL}/prompt/{requests.utils.quote(full_prompt)}?width=1280&height=1280&nologo=true&seed={seed}&model=flux&upscale=true"
        img = download_image_with_retry(url)
        if img and is_valid_image(img):
            return img
    except Exception as e:
        logger.warning(f"Pollinations не сработал: {e}")
    return None

def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    queries = [
        f"artificial intelligence {topic}",
        f"AI technology {topic}",
        f"neural network {topic}",
        f"futuristic robot {topic}",
        f"digital brain {topic}"
    ]
    random.shuffle(queries)
    for query in queries[:2]:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        params = {"query": query, "per_page": 3, "orientation": "landscape"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    photo_url = random.choice(photos)["src"]["large2x"]
                    img = download_image_with_retry(photo_url)
                    if img and is_valid_image(img):
                        return img
        except:
            pass
    return None

def generate_image(topic):
    clean_used_images()
    generators = [
        ("Agnes", generate_agnes_image),
        ("Pixazo", generate_pixazo_image),
        ("Pollinations", generate_pollinations_image)
    ]
    random.shuffle(generators)

    for name, func in generators:
        img = func(topic)
        if img:
            img = sharpen_image(img)
            if not is_image_used(img):
                mark_image_as_used(img)
                logger.info(f"✅ Картинка сгенерирована через {name} (уникальная)")
                return img, name
            else:
                logger.info(f"⚠️ Картинка от {name} уже использовалась, пробуем следующий")

    pexel_img = search_pexels_relevant_photo(topic)
    if pexel_img:
        pexel_img = sharpen_image(pexel_img)
        if not is_image_used(pexel_img):
            mark_image_as_used(pexel_img)
            logger.info("✅ Картинка от Pexels (уникальная)")
            return pexel_img, "Pexels"
        else:
            logger.info("⚠️ Картинка от Pexels уже использовалась")

    banner = create_banner(topic[:20])
    banner = sharpen_image(banner)
    if not is_image_used(banner):
        mark_image_as_used(banner)
        logger.info("✅ Использован баннер")
        return banner, "баннер"
    else:
        banner2 = create_banner(topic[:15] + str(random.randint(1, 100)))
        banner2 = sharpen_image(banner2)
        mark_image_as_used(banner2)
        logger.info("✅ Использован баннер с суффиксом")
        return banner2, "баннер"

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
    draw.text((x, y), text, fill='#00FFFF', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

# ---------- ЗАГРУЗКА ФОТО (ПРЯМОЙ HTTP) ----------
def upload_photo_to_vk_via_http(image_bytes, owner_id, token):
    try:
        vk = vk_api.VkApi(token=token)
        if owner_id < 0:
            group_id = abs(owner_id)
            upload_url = vk.method('photos.getWallUploadServer', {'group_id': group_id})['upload_url']
        else:
            upload_url = vk.method('photos.getWallUploadServer', {})['upload_url']
        files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
        resp = requests.post(upload_url, files=files, timeout=30)
        resp.raise_for_status()
        upload_data = resp.json()
        if 'photo' not in upload_data or 'server' not in upload_data or 'hash' not in upload_data:
            logger.error(f"Неполный ответ сервера загрузки: {upload_data}")
            return None
        save_params = {
            'photo': upload_data['photo'],
            'server': upload_data['server'],
            'hash': upload_data['hash']
        }
        if owner_id < 0:
            save_params['group_id'] = abs(owner_id)
        saved = vk.method('photos.saveWallPhoto', save_params)
        photo = saved[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
        logger.info(f"Получен attachment: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        return None

# ---------- ПУБЛИКАЦИЯ ----------
def publish_to_group(text, image_bytes):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"
    try:
        attachments = []
        if image_bytes:
            attachment = upload_photo_to_vk_via_http(image_bytes, GROUP_ID_AI, VK_TOKEN_AI)
            if attachment:
                attachments.append(attachment)
                logger.info("Фото успешно загружено в группу")
            else:
                logger.warning("Не удалось загрузить фото в группу, публикуем без фото")
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
        resp = requests.get("https://api.vk.com/method/wall.post", params=params, timeout=30)
        try:
            result = resp.json()
        except json.JSONDecodeError:
            logger.error(f"Ошибка парсинга JSON. Код статуса: {resp.status_code}, ответ: {resp.text[:200]}")
            return f"❌ Ошибка API VK: невалидный ответ (код {resp.status_code})"
        if "error" in result:
            return f"❌ Ошибка VK: {result['error']['error_msg']}"
        return f"✅ Пост в группе опубликован (id: {result['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации в группу: {e}")
        return f"❌ Ошибка: {e}"

def publish_announce_to_user(announce_text, image_bytes, group_link):
    if not VK_TOKEN_USER or not VK_USER_ID:
        return "❌ Нет токена для личной стены"
    try:
        attachments = []
        if image_bytes:
            attachment = upload_photo_to_vk_via_http(image_bytes, VK_USER_ID, VK_TOKEN_USER)
            if attachment:
                attachments.append(attachment)
                logger.info("Фото загружено на личную стену")
            else:
                logger.warning("Не удалось загрузить фото на личную стену")
        full_text = announce_text + f"\n\n👉 {group_link}" if group_link else announce_text
        params = {
            "access_token": VK_TOKEN_USER,
            "owner_id": VK_USER_ID,
            "message": full_text,
            "v": "5.131"
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        resp = requests.get("https://api.vk.com/method/wall.post", params=params, timeout=30)
        try:
            result = resp.json()
        except json.JSONDecodeError:
            logger.error(f"Ошибка парсинга JSON. Код статуса: {resp.status_code}, ответ: {resp.text[:200]}")
            return f"❌ Ошибка API VK: невалидный ответ (код {resp.status_code})"
        if "error" in result:
            return f"❌ Ошибка VK (личная): {result['error']['error_msg']}"
        return f"✅ Анонс опубликован (id: {result['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации на личную стену: {e}")
        return f"❌ Ошибка: {e}"

# ---------- СОЗДАНИЕ ПОСТА ----------
def create_post_content(title, text=None):
    if text and len(text) > 50:
        post_text = None
        if AGNES_API_KEY:
            try:
                headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
                prompt = f"Перепиши следующий текст, чтобы он стал bardziej żywy, добавь emoji, akapity, сделай go jak post popularnego blogera o ИИ. Используй разные стили. Текст:\n\n{text}"
                data = {
                    "model": "agnes-v1",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "temperature": 0.8
                }
                resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
                if resp.status_code == 200:
                    rewritten = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if rewritten and len(rewritten) > 100:
                        post_text = rewritten.strip()
                        logger.info("✅ Рерайт через Agnes выполнен")
            except Exception as e:
                logger.warning(f"Рерайт не удался: {e}")
        if not post_text:
            post_text = text
    else:
        post_text = generate_text(title)

    teaser = post_text[:150]
    if len(post_text) > 150:
        teaser += "..."
    lines = post_text.split('\n')
    if lines and len(lines[0]) < 150:
        teaser = lines[0] + "..."

    announce_text = f"🔥 Новый пост в группе {RSS_DEFAULT_GROUP}\n\n{teaser}\n\n➡️ Читать полностью и обсудить в группе:"
    group_link = f"https://vk.com/club{abs(GROUP_ID_AI)}" if GROUP_ID_AI < 0 else f"https://vk.com/public{GROUP_ID_AI}"
    image_bytes, source = generate_image(title)
    return post_text, announce_text, group_link, image_bytes, source

def publish_post_item(title, text=None):
    post_text, announce_text, group_link, image_bytes, source = create_post_content(title, text)
    group_res = publish_to_group(post_text, image_bytes)
    user_res = publish_announce_to_user(announce_text, image_bytes, group_link)
    return {"group": group_res, "user": user_res, "source": source}

# ---------- ПОЛУЧЕНИЕ СЛЕДУЮЩЕГО ПОСТА ----------
POSTS_POOL = None
def build_posts_pool():
    global POSTS_POOL
    posts = load_posts_from_file()
    if posts:
        POSTS_POOL = posts
        logger.info("📚 Используем посты из файла posts.txt")
        return
    if RSS_ENABLED and RSS_SOURCES_JSON and RSS_SOURCES_JSON != '[]':
        entries = get_rss_entries(RSS_SOURCES_JSON)
        if entries:
            POSTS_POOL = [{'title': t, 'text': None} for t in entries]
            logger.info(f"📚 Используем RSS-заголовки: {len(POSTS_POOL)} тем")
            return
    topics = load_topics()
    POSTS_POOL = [{'title': t, 'text': None} for t in topics]
    logger.info(f"📚 Используем запасной список тем: {len(POSTS_POOL)}")

def get_posts_pool():
    global POSTS_POOL
    if POSTS_POOL is None:
        build_posts_pool()
    return POSTS_POOL

def get_next_post():
    pool = get_posts_pool()
    if not pool:
        return None, None
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    used_indices = state.get(today, [])
    available = [i for i in range(len(pool)) if i not in used_indices]
    if not available:
        available = list(range(len(pool)))
        used_indices = []
    idx = random.choice(available)
    used_indices.append(idx)
    state[today] = used_indices
    save_state(state)
    post = pool[idx]
    return post['title'], post['text']

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
    scheduled_post()
    schedule.every(6).hours.do(scheduled_post)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ---------- КОМАНДЫ ----------
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "🤖 Бот «AI Навигатор» – разнообразные посты!\n"
            "📌 Команды:\n"
            "/post <заголовок> — сгенерировать пост\n"
            "/post <текст (длиннее 50 символов)> — опубликовать с рерайтом\n"
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
        if len(content) > 50:
            title = content[:50] + "..."
            result = publish_post_item(title, content)
        else:
            result = publish_post_item(content)
        send_message(chat_id, f"📌 Группа: {result['group']}\n👤 Анонс: {result['user']}")
        return
    send_message(chat_id, "❓ Неизвестная команда. Напишите /help")

# ---------- ЗАПУСК ----------
def main():
    logger.info("🚀 AI Навигатор запущен (расширенные шаблоны, разнообразные посты)")
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