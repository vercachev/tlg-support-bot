import os
import logging
import json
from typing import Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
BASE_WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-service.onrender.com
PORT = int(os.getenv("PORT", 10000))
STATE_FILE = "reply_state.json"  # хранит { admin_id: target_user_id }

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Утилиты для состояния ответа ---
def load_state() -> Dict[str, str]:
    try:
        if not os.path.exists(STATE_FILE):
            return {}
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception(f"Failed to load state file: {e}")
        return {}

def save_state(state: Dict[str, str]):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        logger.exception(f"Failed to save state file: {e}")

def set_admin_reply_state(admin_id: int, user_id: int):
    state = load_state()
    state[str(admin_id)] = str(user_id)
    save_state(state)

def pop_admin_reply_state(admin_id: int):
    state = load_state()
    admin_key = str(admin_id)
    if admin_key in state:
        target = state.pop(admin_key)
        save_state(state)
        return int(target)
    return None

def get_admin_reply_state(admin_id: int):
    state = load_state()
    return int(state.get(str(admin_id))) if str(admin_id) in state else None

# --- Клавиатуры ---
def main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🙋‍♂️ Задати питання про візовий вступ", callback_data="ask_vstup")],
        [InlineKeyboardButton(text="📋 Задати питання про візовий супровід", callback_data="ask_suprovid")]
    ])
    return kb

def admin_reply_kb(user_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Відповісти", callback_data=f"reply_{user_id}")],
        [InlineKeyboardButton(text="✅ Позначити як вирішено", callback_data=f"resolve_{user_id}")]
    ])
    return kb

# --- Хендлеры ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Вітаємо! Я допоможу вам отримати консультацію. Оберіть тему вашого питання:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data.startswith("ask_"))
async def ask_question(callback: types.CallbackQuery):
    theme = "візовий вступ" if callback.data == "ask_vstup" else "візовий супровід"
    await callback.message.answer(f"Ви обрали тему: *{theme}*.\nБудь ласка, напишіть ваше питання одним повідомленням.", parse_mode="Markdown")
    await callback.answer()

@dp.message(F.text, ~F.from_user.id.in_(ADMIN_IDS))
async def handle_user_question(message: types.Message):
    """Приходит вопрос от обычного пользователя — пересылаем администраторам."""
    text = message.text
    user = message.from_user
    user_info = f"👤 Від: {user.full_name} (@{user.username or 'немає'})\nID: `{user.id}`"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 *Нове питання!*\n{user_info}\n\n*Текст:* {text}",
                parse_mode="Markdown",
                reply_markup=admin_reply_kb(user.id)
            )
        except Exception as e:
            logger.exception(f"Error sending question to admin {admin_id}: {e}")
    await message.answer("✅ Ваше питання надіслано власнику. Очікуйте на відповідь у цьому чаті.")

@dp.callback_query(F.data.startswith("reply_"))
async def start_reply(callback: types.CallbackQuery):
    """Админ нажал кнопку 'Відповісти' — переводим его в режим ответа."""
    admin_id = callback.from_user.id
    target_user_id = callback.data.split("_", 1)[1]
    try:
        set_admin_reply_state(admin_id, int(target_user_id))
        await callback.message.answer(
            f"Ви відповідаєте користувачу ID: `{target_user_id}`.\nНапишіть текст — він буде автоматично надісланий цьому користувачу.\n(Або натисніть кнопку 'Позначити як вирішено', якщо питання вже закрите.)",
            parse_mode="Markdown"
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error setting reply state: {e}")
        await callback.message.answer("❌ Не вдалося перейти в режим відповіді. Спробуйте ще раз.")
        await callback.answer()

@dp.callback_query(F.data.startswith("resolve_"))
async def resolve_question(callback: types.CallbackQuery):
    """Позначити як вирішено — просто уведомить администратора и очистит состояние, если он был в режиме."""
    admin_id = callback.from_user.id
    target_user_id = int(callback.data.split("_", 1)[1])
    # если админ был в режиме ответа на этого пользователя — очистим
    current = get_admin_reply_state(admin_id)
    if current == target_user_id:
        pop_admin_reply_state(admin_id)
    await callback.message.answer(f"Питання для користувача {target_user_id} позначено як вирішено.")
    await callback.answer()

@dp.message(F.text, F.from_user.id.in_(ADMIN_IDS))
async def admin_text_handler(message: types.Message):
    """Если админ в режиме ответа — отправляем текст пользователю; иначе — короткое уведомление."""
    admin_id = message.from_user.id
    target = get_admin_reply_state(admin_id)
    if target:
        text = message.text
        try:
            await bot.send_message(target, f"📩 *Відповідь від адміністратора:*\\n\\n{text}", parse_mode="Markdown")
            await message.answer(f"✅ Відповідь надіслана користувачу {target}")
        except Exception as e:
            logger.exception(f"Failed to send admin reply to user {target}: {e}")
            await message.answer(f"❌ Не вдалося надіслати користувачу {target}: {e}")
        # очистить состояние
        pop_admin_reply_state(admin_id)
    else:
        # Не в режиме ответа: подсказка
        await message.answer("Ви не обрали користувача для відповіді. Натисніть кнопку 'Відповісти' під питанням, щоб вибрати одержувача.")

# --- Webhook setup ---
async def on_startup(bot: Bot):
    if not BASE_WEBHOOK_URL:
        logger.error("WEBHOOK_URL не вказано. Встановіть змінну середовища WEBHOOK_URL.")
        return
    try:
        await bot.set_webhook(f"{BASE_WEBHOOK_URL}/webhook")
        logger.info("Webhook встановлено")
    except Exception as e:
        logger.exception(f"Не вдалося встановити webhook: {e}")

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    logger.info(f"Starting web server on 0.0.0.0:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()