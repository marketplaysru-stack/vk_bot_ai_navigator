#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор — посты в группу -240273450 в 09:00 и 15:00.
"""

import os, sys, json, time, random, hashlib, logging, requests, schedule
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import feedparser, vk_api
from vk_api.upload import VkUpload
from vk_api.exceptions import ApiError

BOT_NAME = "AI Навигатор"
GROUP_ID = -240273450

# -------------------- ЛОГИРОВАНИЕ --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [AI] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("AI")

# -------------------- .env --------------------
load_dotenv()
VK_TOKEN        = os.getenv("VK_TOKEN", "").strip()
HUGGINGFACE_KEY = os.getenv("HUGGINGFACE_API_KEY", "").strip()
PEXELS_KEY      = os.getenv("PEXELS_API_KEY", "").strip()
POST_TIMES      = [t.strip() for t in os.getenv("POST_TIMES", "09:00,15:00").split(",") if t.strip()]
RSS_ENABLED     = os.getenv("RSS_ENABLED", "true").lower() == "true"
RSS_SOURCES     = json.loads(os.getenv("RSS_SOURCES", "[]"))
DATA_DIR        = os.getenv("DATA_DIR", "./data")
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

# -------------------- ТЕМЫ --------------------
DEFAULT_TOPICS = [
    "Как использовать нейросети в повседневной жизни",
    "5 бесплатных AI-инструментов для работы и учёбы",
    "Что такое промпт-инжиниринг и зачем он нужен",
    "Как AI помогает в маркетинге и рекламе",
    "Стоит ли бояться, что нейросети заменят профессии",
    "Как выбрать первый курс по искусственному интеллекту",
    "ChatGPT vs YandexGPT: сравнение возможностей",
    "Как AI рисует картинки: простое объяснение",
    "Топ-10 ошибок новичков при работе с AI",
    "Как AI экономит время в бизнесе: примеры",
    "Нейросети для создания презентаций и документов",
    "Как проверить, не сгенерирован ли текст нейросетью",
    "Этика использования AI: что важно знать",
    "AI в образовании: польза и риски",
    "Как защитить свои данные при работе с чат-ботами",
    "Голосовые ассистенты: обзор и сравнение",
    "AI-переводчики: насколько они точны",
    "Как AI помогает в поиске работы",
    "Автоматизация рутины с помощью нейросетей",
    "Что такое мультимодальные модели",
    "Локальные нейросети: кому и зачем",
    "AI-музыка: как нейросети сочиняют треки",
    "Как обучают большие языковые модели",
    "Тренды AI на ближайший год",
    "Как объяснить ребёнку, что такое нейросети",
]

def load_topics_from_file():
    if not os.path.exists("topics.txt"): return []
    with open("topics.txt", "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]

def check_rss_sources():
    if not RSS_ENABLED or not RSS_SOURCES:
        logger.info("📡 RSS отключён"); return 0
    logger.info(f"📡 Проверяю RSS ({len(RSS_SOURCES)} шт.)...")
    total = 0
    for src in RSS_SOURCES:
        try:
            feed = feedparser.parse(src.get("url"))
            n = len(feed.entries); total += n
            logger.info(f"  {'✅' if n else '⚠️'} {src.get('url')} — тем: {n}")
        except Exception as e:
            logger.error(f"  ❌ {src.get('url')} — {e}")
    logger.info(f"📡 Всего тем из RSS: {total}")
    return total

def get_topic():
    topics = []
    if RSS_ENABLED and RSS_SOURCES:
        for src in RSS_SOURCES:
            try:
                feed = feedparser.parse(src["url"])
                for e in feed.entries[:5]:
                    t = getattr(e, "title", "").strip()
                    if t: topics.append(t)
            except Exception as e:
                logger.warning(f"RSS {src.get('url')}: {e}")
    if not topics: topics = load_topics_from_file()
    if not topics:
        logger.warning("Нет тем из RSS/topics.txt — запасной список")
        topics = DEFAULT_TOPICS
    logger.info(f"📚 Доступно тем: {len(topics)}")
    return random.choice(topics)

# -------------------- ГЕНЕРАЦИЯ ТЕКСТА --------------------
def generate_text_hf(prompt):
    if not HUGGINGFACE_KEY: return None
    try:
        url = "https://api-inference.huggingface.co/models/google/flan-t5-large"
        headers = {"Authorization": f"Bearer {HUGGINGFACE_KEY}"}
        payload = {"inputs": prompt,
                   "parameters": {"max_new_tokens": 180, "temperature": 0.7, "return_full_text": False},
                   "options": {"wait_for_model": True}}
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and d: return d[0].get("generated_text", "").strip()
            if isinstance(d, dict): return d.get("generated_text", "").strip()
        logger.warning(f"HF текст: {r.status_code}")
    except Exception as e:
        logger.error(f"HF текст: {e}")
    return None

def generate_text(topic):
    prompt = (f"Ты — автор постов для группы «{BOT_NAME}» про нейросети. "
              f"Напиши полезный пост на тему: {topic}. 5-7 предложений, дружелюбно, "
              f"с практическими советами. Без рекламных призывов.")
    text = generate_text_hf(prompt)
    if text and len(text) > 40: return text
    templates = [
        f"🧭 {topic}\n\nРазбираемся вместе с «{BOT_NAME}». Мы собрали самое важное про AI, что стоит знать каждому. Сохраняйте пост, чтобы не потерять!",
        f"📌 {topic}\n\nКоротко и по делу — для тех, кто хочет разобраться в нейросетях без воды. Что ещё разобрать? Пишите в комментариях. #ai #навигатор",
        f"💡 {topic}\n\nПолезная подборка от «{BOT_NAME}». Мы регулярно публикуем материалы про AI, которые помогают экономить время и принимать лучшие решения.",
    ]
    return random.choice(templates)

# -------------------- ГЕНЕРАЦИЯ КАРТИНКИ --------------------
def build_prompt(topic):
    # Стили под AI-тематику
    styles = [
        "футуристичная цифровая иллюстрация, нейросеть, светящиеся линии",
        "минималистичный tech-дизайн, схемы, градиенты",
        "абстрактная визуализация данных, светящиеся узлы",
        "реалистичное фото робота-ассистента, мягкий свет",
        "флэт-иллюстрация, человек и нейросеть, пастель",
        "неоновые сине-фиолетовые тона, киберпанк-стиль",
    ]
    colors = ["холодные сине-фиолетовые оттенки", "яркие неоновые акценты",
              "тёмный фон с контрастным свечением", "светлые tech-оттенки"]
    return f"{topic}, {random.choice(styles)}, {random.choice(colors)}, высокое качество, 4k"

def gen_img_hf(prompt):
    if not HUGGINGFACE_KEY: return None
    try:
        url = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1"
        headers = {"Authorization": f"Bearer {HUGGINGFACE_KEY}"}
        r = requests.post(url, json={"inputs": prompt, "options": {"wait_for_model": True}},
                          headers=headers, timeout=90)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
        logger.warning(f"HF img: {r.status_code}")
    except Exception as e:
        logger.error(f"HF img: {e}")
    return None

def gen_img_poll(prompt):
    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=576&nologo=true"
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and r.content: return r.content
    except Exception as e:
        logger.error(f"Poll: {e}")
    return None

def gen_img_pexels(query):
    if not PEXELS_KEY: return None
    try:
        r = requests.get("https://api.pexels.com/v1/search",
                         headers={"Authorization": PEXELS_KEY},
                         params={"query": query, "per_page": 5, "orientation": "landscape"},
                         timeout=20)
        if r.status_code == 200:
            ph = r.json().get("photos", [])
            if ph:
                img = requests.get(random.choice(ph)["src"]["large"], timeout=30)
                if img.status_code == 200: return img.content
    except Exception as e:
        logger.error(f"Pexels: {e}")
    return None

def gen_banner(topic):
    try:
        img = Image.new("RGB", (1200, 630), color=(15, 20, 45))
        d = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
        except: font = ImageFont.load_default()
        words = topic.split(); lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 40: cur = (cur + " " + w).strip()
            else: lines.append(cur); cur = w
        if cur: lines.append(cur)
        y = 180
        for ln in lines[:6]:
            d.text((80, y), ln, fill=(120, 200, 255), font=font); y += 60
        d.text((80, 540), f"🧭 {BOT_NAME}", fill=(180, 220, 255), font=font)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=90); return buf.getvalue()
    except Exception as e:
        logger.error(f"Баннер: {e}"); return None

def generate_image(topic):
    p = build_prompt(topic)
    logger.info(f"🖼 Промпт: {p[:100]}...")
    for fn, name in [(gen_img_hf, "HF"), (gen_img_poll, "Pollinations")]:
        img = fn(p)
        if img: logger.info(f"✅ Картинка: {name}"); return img
    img = gen_img_pexels(" ".join(topic.split()[:4]) + " technology")
    if img: logger.info("✅ Картинка: Pexels"); return img
    logger.warning("⚠️ Баннер")
    return gen_banner(topic)

# -------------------- КЭШ --------------------
def img_hash(img): return hashlib.md5(img).hexdigest()
def is_cached(h):
    f = os.path.join(DATA_DIR, "image_cache.txt")
    if not os.path.exists(f): return False
    with open(f) as fp: return h in {l.strip() for l in fp}
def save_cache(h):
    with open(os.path.join(DATA_DIR, "image_cache.txt"), "a") as fp: fp.write(h + "\n")

# -------------------- VK --------------------
def safe_vk(fn, *a, max_retries=3, **kw):
    for i in range(max_retries):
        try: return fn(*a, **kw)
        except ApiError as e:
            if e.code == 9:
                w = 600 * (i + 1)
                logger.warning(f"🚫 Flood control. Жду {w//60} мин (попытка {i+1})")
                time.sleep(w)
            elif e.code == 14:
                logger.error("Требуется капча"); return None
            else:
                logger.error(f"VK {e.code}: {e}"); return None
    return None

def publish(text, image_data):
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    upload = VkUpload(vk_session)
    att = None
    if image_data:
        try:
            ph = safe_vk(upload.photo_group, photo=image_data, group_id=str(abs(GROUP_ID)))
            if ph:
                att = f"photo{ph[0]['owner_id']}_{ph[0]['id']}"
                time.sleep(3)
        except Exception as e:
            logger.error(f"Загрузка фото: {e}")
    res = safe_vk(vk.wall.post, owner_id=GROUP_ID, from_group=1,
                  message=text, attachments=att or "")
    if res: logger.info(f"✅ Пост в группе {GROUP_ID}")

# -------------------- ЦИКЛ --------------------
def post_now():
    logger.info(f"⏰ [{BOT_NAME}] Запуск постинга")
    topic = get_topic()
    logger.info(f"📝 Тема: {topic}")
    text = generate_text(topic)
    logger.info(f"📄 Текст: {text[:120]}...")
    img = generate_image(topic)
    if img:
        h = img_hash(img)
        if is_cached(h):
            logger.info("🔁 Дубль — перегенерация")
            img = generate_image(topic + " другой ракурс")
            if img: h = img_hash(img)
        if img: save_cache(h)
    publish(text, img)
    logger.info(f"🏁 [{BOT_NAME}] Цикл завершён\n")

def main():
    logger.info(f"🚀 {BOT_NAME} запущен")
    logger.info(f"📡 Группа: {GROUP_ID}")
    logger.info(f"⏰ Расписание: {', '.join(POST_TIMES)}")
    logger.info(f"🔑 HF: {'есть' if HUGGINGFACE_KEY else 'НЕТ'}")
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
        logger.error("❌ Нет VK_TOKEN в .env"); sys.exit(1)
    try: main()
    except KeyboardInterrupt: logger.info("Остановлено")