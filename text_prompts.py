# ===== text_prompts.py для AI-бота =====
import random

SYSTEM_PROMPT = (
    "Ты — эксперт в области искусственного интеллекта, технологий и цифрового будущего. "
    "Твоя задача — писать увлекательные, познавательные посты для блога о AI. "
    "Объясняй сложные вещи простым языком, делитесь инсайтами, новостями, этическими вопросами, "
    "примерами применения нейросетей. Пост должен быть вдохновляющим и полезным. "
    "Ты не предлагаешь услуги и не продаёшь продукты — ты делишься знаниями. "
    "Формат поста: дружелюбный, экспертный, но без высокомерия. "
    "Структура: 70% полезный контент, 20% обсуждение/новости, 10% вопрос к аудитории. "
    "Используй эмодзи, разделители. В конце добавь 5 хештегов."
)

TEXT_MODEL = "agnes-2.0-flash"
TEXT_TEMPERATURE = 0.85
TEXT_MAX_TOKENS = 2048
TEXT_TIMEOUT = 60

FALLBACK_TEXT = (
    "❓ {topic}\n\n"
    "Поделитесь своим мнением в комментариях! 👇\n\n"
    "#искусственныйинтеллект #нейросети #AI #технологии #будущее"
)

EMOJIS_BLOCKS = {
    "header": ["🤖", "💡", "🧠", "🚀", "⚡", "🌐"],
    "tips": ["✅", "🔍", "📊", "💬", "✨", "🎯"],
    "discussion": ["🗣️", "👥", "🤝", "💭"],
    "engagement": ["👇", "✍️", "📝", "🤔", "💬"],
}

def get_random_emojis(block_type, count=2):
    emojis = EMOJIS_BLOCKS.get(block_type, ["✨"])
    return " ".join(random.sample(emojis, min(count, len(emojis))))

TOPIC_HASHTAGS = {
    "neural": ["#нейросети", "#машинноеобучение", "#глубокоеобучение", "#AI", "#нейронныесети"],
    "chatbot": ["#чатботы", "#ChatGPT", "#генеративныйAI", "#диалоговыесистемы", "#AIассистенты"],
    "ethics": ["#этикаAI", "#безопасностьAI", "#ответственныйAI", "#регулированиеAI", "#доверие"],
    "future": ["#будущее", "#технологии", "#инновации", "#цифроваятрансформация", "#AI2026"],
    "general": ["#искусственныйинтеллект", "#AI", "#технологии", "#нейросети", "#цифровоебудущее"],
}
DEFAULT_HASHTAGS = ["#искусственныйинтеллект", "#нейросети", "#AI", "#технологии", "#будущее"]

def get_hashtags(topic, count=5):
    topic_lower = topic.lower()
    for key, tags in TOPIC_HASHTAGS.items():
        if key in topic_lower:
            return random.sample(tags, min(count, len(tags)))
    return random.sample(DEFAULT_HASHTAGS, count)

def build_system_prompt():
    return SYSTEM_PROMPT

def build_user_prompt(topic):
    return f"Тема: {topic}"

def post_process_text(text):
    if not text or len(text) < 50:
        return None
    if "#" not in text:
        text += "\n\n" + " ".join(get_hashtags(text))
    return text

def get_fallback_text(topic):
    return FALLBACK_TEXT.format(topic=topic)