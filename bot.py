#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Навигатор. Группа -240273450. Посты в 09:00 и 15:00.
Ключи читаются из переменных окружения.
"""

import os, sys, time, random, hashlib, logging, requests, schedule, re
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import feedparser, vk_api
from vk_api.upload import VkUpload
from vk_api.exceptions import ApiError

# ================== НАСТРОЙКИ ==================
BOT_NAME   = "AI Навигатор"
GROUP_ID   = int(os.getenv("AI_GROUP_ID", "-240273450"))
POST_TIMES = ["09:00", "15:00"]

# Ключи — из переменных окружения
VK_TOKEN        = os.getenv("AI_VK_TOKEN", "").strip()
HUGGINGFACE_KEY = os.getenv("HUGGINGFACE_API_KEY", "").strip()
PEXELS_KEY      = os.getenv("PEXELS_API_KEY", "").strip()

RSS_ENABLED = True
RSS_SOURCES = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru",
    "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru",
    "https://www.computerworld.com/index.rss",
    "https://www.technologyreview.com/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
]

DATA_DIR = "./data"
# ==============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [AI] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("AI")
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

# ================== ЗАПАСНЫЕ ТЕМЫ ==================
DEFAULT_TOPICS = [
    "Как использовать нейросети в повседневной жизни",
    "Что такое промпт-инжиниринг и зачем он нужен",
    "Как AI рисует картинки: простое объяснение",
    "Что такое мультимодальные модели",
    "Как обучают большие языковые модели",
    "Локальные нейросети: кому и зачем они нужны",
    "5 бесплатных AI-инструментов для работы и учёбы",
    "ChatGPT vs YandexGPT vs GigaChat: сравнение возможностей",
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
    "Что такое галлюцинации нейросетей и как с ними бороться",
    "Тренды AI на ближайший год",
    "Как объяснить ребёнку, что такое нейросети",
    "Как выбрать первый курс по искусственному интеллекту",
]

# ================== СТИЛИ КАРТИНОК ==================
IMG_STYLES = [
    "футуристичная цифровая иллюстрация, светящиеся нейронные связи",
    "минималистичный tech-дизайн, чистые градиенты, схемы",
    "абстрактная визуализация данных, светящиеся узлы и графы",
    "реалистичное фото робота-ассистента, мягкий свет",
    "флэт-иллюстрация: человек и нейросеть, пастельные тона",
    "киберпанк-стиль, неоновые огни, мегаполис будущего",
    "изометрическая 3D-иллюстрация, tech-объекты",
    "акварельная абстракция на тему нейросетей",
    "low-poly 3D, гранёные формы, tech-палитра",
    "сюрреалистичный коллаж: мозг и микросхемы",
    "ретро-футуризм 80-х, синтвейв-палитра",
    "минимализм: одна линия, светящаяся точка данных",
]
IMG_PALETTES = [
    "холодные сине-фиолетовые оттенки",
    "яркие неоновые акценты на тёмном фоне",
    "голубой и белый, чистые тона",
    "тёмно-синий фон с бирюзовым свечением",
    "фиолетово-розовый градиент",
    "монохром с одним ярким акцентом",
]
IMG_LIGHT = ["мягкое рассеянное свечение", "яркий контрастный свет",
             "неоновое освещение", "естественный свет, тёплые блики"]
IMG_COMPOSITION = ["крупный план", "общий план", "вид сверху",
                   "симметричная композиция", "правило третей"]

def build_prompt(topic):
    return (
        f"{topic}, {random.choice(IMG_STYLES)}, {random.choice(IMG_PALETTES)}, "
        f"{random.choice(IMG_LIGHT)}, {random.choice(IMG_COMPOSITION)}, "
        f"высокая детализация, 4k, качественное изображение, без текста, без надписей"
    )

# ================== ТЕКСТ ==================
def format_post(topic, raw):
    emojis = ["🧠", "🤖", "💡", "⚡", "🔍", "📊", "🚀", "🧩", "🌐", "📌"]
    e = random.choice(emojis)
    if not raw or len(raw) < 60:
        raw = (
            "В этом материале мы разобрали ключевые моменты темы, которые помогут "
            "разобраться без лишней воды. Материал будет полезен как новичкам, "
            "так и тем, кто уже знаком с темой и хочет углубиться."
        )
    raw = re.sub(r"\s+", " ", raw.strip())
    intro = f"{e} {topic}\n\n"
    bullets = random.sample([
        "Сохраните пост, чтобы вернуться к нему позже.",
        "Поделитесь с теми, кому это может быть полезно.",
        "Напишите в комментариях, что думаете по теме.",
        "Подписывайтесь, чтобы не пропустить новые материалы.",
        "Задавайте вопросы — разберём в следующих постах.",
    ], 2)
    conclusion = "👉 " + "\n👉 ".join(bullets) + "\n\n"
    hashtags = "#AI #нейросети #искусственныйинтеллект #технологии #навигатор"
    return intro + raw + "\n\n" + conclusion + hashtags

def generate_text_hf(topic):
    if not HUGGINGFACE_KEY: return None
    prompt = (
        f"Напиши полезный информативный пост про искусственный интеллект на тему: «{topic}». "
        f"5-7 предложений, дружелюбно, без воды, с практическими советами и примерами."
    )
    try:
        url = "https://api-inference.huggingface.co/models/google/flan-t5-large"
        r = requests.post(url,
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": prompt,
                  "parameters": {"max_new_tokens": 220, "temperature": 0.8, "return_full_text": False},
                  "options": {"wait_for_model": True}},
            timeout=60)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and d: return d[0].get("generated_text", "").strip()
            if isinstance(d, dict): return d.get("generated_text", "").strip()
        logger.warning(f"HF текст: {r.status_code} {r.text[:150]}")
    except Exception as e:
        logger.debug(f"HF текст недоступен: {e}")
    return None

def generate_text(topic):
    raw = generate_text_hf(topic)
    return format_post(topic, raw)

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

def get_topic():
    topics = []
    if RSS_ENABLED and RSS_SOURCES:
        for url in RSS_SOURCES:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:5]:
                    t = getattr(e, "title", "").strip()
                    if t and 20 < len(t) < 200:
                        topics.append(t)
            except Exception as e:
                logger.warning(f"RSS {url}: {e}")
    if not topics and os.path.exists("topics.txt"):
        with open("topics.txt", "r", encoding="utf-8") as f:
            topics = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not topics:
        logger.warning("Нет тем из RSS/topics.txt — запасной список")
        topics = DEFAULT_TOPICS
    logger.info(f"📚 Доступно тем: {len(topics)}")
    return random.choice(topics)

# ================== КАРТИНКИ ==================
def gen_img_hf(prompt):
    if not HUGGINGFACE_KEY: return None
    try:
        url = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1"
        r = requests.post(url,
            headers={"Authorization": f"Bearer {HUGGINGFACE_KEY}"},
            json={"inputs": prompt, "options": {"wait_for_model": True}},
            timeout=90)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
        logger.warning(f"HF img: {r.status_code}")
    except Exception as e:
        logger.debug(f"HF img недоступен: {e}")
    return None

def gen_img_poll(prompt):
    try:
        seed = random.randint(1, 999999)
        url = (f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
               f"?width=1024&height=576&nologo=true&seed={seed}")
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
            params={"query": query, "per_page": 10, "orientation": "landscape"},
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
        bg = random.choice([(15,20,45),(30,15,50),(10,25,40),(25,10,40)])
        img = Image.new("RGB", (1200, 630), color=bg)
        d = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
        except: font = ImageFont.load_default()
        words = topic.split(); lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 38: cur = (cur + " " + w).strip()
            else: lines.append(cur); cur = w
        if cur: lines.append(cur)
        y = 170
        for ln in lines[:6]:
            d.text((80, y), ln, fill=(120, 200, 255), font=font); y += 60
        d.text((80, 550), f"🧠 {BOT_NAME}", fill=(180, 220, 255), font=font)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=90); return buf.getvalue()
    except Exception as e:
        logger.error(f"Баннер: {e}"); return None

def generate_image(topic):
    p = build_prompt(topic)
    logger.info(f"🖼 Промпт: {p[:120]}...")
    for fn, name in [(gen_img_hf, "HF"), (gen_img_poll, "Pollinations")]:
        img = fn(p)
        if img: logger.info(f"✅ Картинка: {name}"); return img
    img = gen_img_pexels(" ".join(topic.split()[:4]) + " technology")
    if img: logger.info("✅ Картинка: Pexels"); return img
    logger.warning("⚠️ Баннер")
    return gen_banner(topic)

# ================== КЭШ ==================
def img_hash(img): return hashlib.md5(img).hexdigest()
def is_cached(h):
    f = os.path.join(DATA_DIR, "image_cache.txt")
    if not os.path.exists(f): return False
    with open(f) as fp: return h in {l.strip() for l in fp}
def save_cache(h):
    with open(os.path.join(DATA_DIR, "image_cache.txt"), "a") as fp: fp.write(h + "\n")

# ================== VK ==================
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
    """Публикация поста в группу с фото."""
    try:
        vk_session = vk_api.VkApi(token=VK_TOKEN)
        vk = vk_session.get_api()
        upload = VkUpload(vk_session)
    except Exception as e:
        logger.error(f"VK auth: {e}")
        return

    att = None
    if image_data:
        try:
            ph = safe_vk(
                upload.photo_wall,
                photo=image_data,
                group_id=abs(GROUP_ID),
            )
            if ph:
                att = f"photo{ph[0]['owner_id']}_{ph[0]['id']}"
                logger.info(f"📷 Фото загружено: {att}")
                time.sleep(3)
        except Exception as e:
            logger.error(f"Загрузка фото: {e}")

    res = safe_vk(
        vk.wall.post,
        owner_id=GROUP_ID,
        from_group=1,
        message=text,
        attachments=att or "",
    )
    if res:
        logger.info(f"✅ Пост в группе {GROUP_ID} (post_id={res.get('post_id')})")
    else:
        logger.error("❌ Не удалось опубликовать пост")

# ================== ЦИКЛ ==================
def post_now():
    logger.info(f"⏰ [{BOT_NAME}] Запуск постинга")
    topic = get_topic()
    logger.info(f"📝 Тема: {topic}")
    text = generate_text(topic)
    logger.info(f"📄 Текст ({len(text)} симв.): {text[:150]}...")
    img = generate_image(topic)
    if img:
        h = img_hash(img)
        if is_cached(h):
            logger.info("🔁 Дубль — перегенерация")
            img = generate_image(topic + " в другом стиле")
            if img: h = img_hash(img)
        if img: save_cache(h)
    publish(text, img)
    logger.info(f"🏁 [{BOT_NAME}] Цикл завершён\n")

def main():
    # Проверка ключей
    if not VK_TOKEN:
        logger.error("❌ Не задана переменная окружения AI_VK_TOKEN")
        sys.exit(1)
    logger.info(f"🚀 {BOT_NAME} запущен")
    logger.info(f"📡 Группа: {GROUP_ID}")
    logger.info(f"⏰ Расписание: {', '.join(POST_TIMES)}")
    logger.info(f"🔑 VK: {VK_TOKEN[:15]}...")
    logger.info(f"🔑 HF: {'есть' if HUGGINGFACE_KEY else 'НЕТ'}")
    logger.info(f"🔑 Pexels: {'есть' if PEXELS_KEY else 'НЕТ'}")
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
    try: main()
    except KeyboardInterrupt: logger.info("Остановлено")