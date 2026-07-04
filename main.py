import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
BASE_WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://your-service.onrender.com

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

bot = Bot(token=TOKEN)
dp = Dispatcher()

def main_menu():
    kb = [
        [InlineKeyboardButton(text="🙋‍♂️ Задати питання про візовий вступ", callback_data="ask_vstup")],
        [InlineKeyboardButton(text="📋 Задати питання про візовий супровід", callback_data="ask_suprovid")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_reply_kb(user_id):
    kb = [[InlineKeyboardButton(text="✍️ Відповісти", callback_data=f"reply_{user_id}")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

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
    # Notify all admins
    for admin_id in ADMIN_IDS:
        try:
            user_info = f"👤 Від: {message.from_user.full_name} (@{message.from_user.username or 'немає'})\nID: `{message.from_user.id}`"
            await bot.send_message(
                admin_id,
                f"🆕 *Нове питання!*\\n{user_info}\\n\\n*Текст:* {message.text}",
                parse_mode="Markdown",
                reply_markup=admin_reply_kb(message.from_user.id)
            )
        except Exception as e:
            logging.exception(f"Error sending to admin {admin_id}: {e}")

    await message.answer("✅ Ваше питання надіслано власнику. Очікуйте на відповідь у цьому чаті.")

@dp.callback_query(F.data.startswith("reply_"))
async def start_reply(callback: types.CallbackQuery):
    target_user_id = callback.data.split("_", 1)[1]
    await callback.message.answer(
        f"Введіть відповідь для користувача (ID: `{target_user_id}`).\n\nВідправте повідомлення у форматі:\n`/ans {target_user_id} ТЕКСТ_ВІДПОВІДІ`",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(Command("ans"), F.from_user.id.in_(ADMIN_IDS))
async def send_answer_to_user(message: types.Message):
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            return await message.answer("Формат: `/ans ID текст_відповіді`")
        user_id = int(parts[1])
        answer_text = parts[2]
        await bot.send_message(user_id, f"📩 *Відповідь від адміністратора:*\\n\\n{answer_text}", parse_mode="Markdown")
        await message.answer(f"✅ Відповідь надіслана користувачу {user_id}")
    except Exception as e:
        logging.exception("Помилка при відправленні відповіді")
        await message.answer(f"❌ Помилка: {e}")

async def on_startup(bot: Bot):
    if not BASE_WEBHOOK_URL:
        logging.error("WEBHOOK_URL не вказано. Встановіть змінну середовища WEBHOOK_URL.")
        return
    await bot.set_webhook(f"{BASE_WEBHOOK_URL}/webhook")
    logging.info("Webhook встановлено")

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    main()
