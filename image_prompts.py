# ===== image_prompts.py для AI-бота =====
import random

STYLES = [
    "абстрактная цифровая графика",
    "визуализация нейронной сети",
    "футуристический интерфейс",
    "технологичная иллюстрация",
    "инфографика с AI-элементами",
    "геометрический паттерн",
    "голографическое изображение",
    "минималистичный UI",
    "схема алгоритма",
    "абстрактный поток данных",
]

def get_context_from_topic(topic):
    keywords = []
    if "нейросеть" in topic or "нейронная сеть" in topic:
        keywords.append("нейронные сети, связи, узлы")
    if "робот" in topic:
        keywords.append("робототехника, механизмы")
    if "чат" in topic or "бот" in topic:
        keywords.append("чат-боты, диалоговые системы")
    if "генерация" in topic or "творчество" in topic:
        keywords.append("генеративный AI, творчество")
    if "этика" in topic:
        keywords.append("этика, ответственность, контроль")
    if "будущее" in topic:
        keywords.append("будущее, инновации, прогресс")
    if not keywords:
        keywords.append("AI, технологии, цифровой мир")
    return ", ".join(keywords)

def build_image_prompt(topic):
    style = random.choice(STYLES)
    context = get_context_from_topic(topic)

    base = (
        f"Создай изображение для поста на тему: '{topic}'. "
        f"Стиль: {style}. "
        f"Учти контекст: {context}. "
        "Изображение должно быть уникальным, не использовать распространённые стоковые клише. "
        "Предпочтение отдаётся беслюдным изображениям: абстракция, графика, схемы, интерфейсы, футуристичные объекты. "
        "Если присутствуют люди – они должны быть разнообразными, но лучше избегать людей. "
        "Цветовая палитра: технологичная (синий, фиолетовый, неоновый, белый, чёрный). "
        "Формат: квадратный (1:1). "
        "Высокое качество, 8K, векторная графика или фотореализм – в зависимости от стиля. "
        "Без текста и надписей."
    )

    details = [
        "с акцентом на динамику",
        "с мягким свечением",
        "с геометрическими формами",
        "с контрастными цветами",
        "с элементами минимализма",
        "с необычным ракурсом",
        "с использованием негативного пространства",
    ]
    base += " " + random.choice(details)

    if random.random() < 0.3:
        base += " Стиль – киберпанк или футуризм."

    return base

AGNES_IMAGE_PARAMS = {
    "model": "agnes-image-2.1-flash",
    "size": "1536x1536",
    "n": 1
}

GIGACHAT_IMAGE_PARAMS = {
    "model": "GigaChat-Image",
    "size": "1536x1536",
    "n": 1
}

POLLINATIONS_IMAGE_PARAMS = {
    "width": 1536,
    "height": 1536,
    "nologo": True
}

TIMEOUT_AGNES = 180
TIMEOUT_GIGACHAT = 180
TIMEOUT_POLLINATIONS = 60
DOWNLOAD_TIMEOUT = 90
DOWNLOAD_RETRIES = 4
DOWNLOAD_DELAY = 2
DOWNLOAD_BACKOFF = 2

SUFFIX_AGNES = ""
SUFFIX_GIGACHAT = ""
SUFFIX_POLLINATIONS = " высокое качество, уникальный стиль, AI-тематика"