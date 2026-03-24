import asyncio
import logging
import os
import uuid
import json
import base64
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# ─── Настройки ────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("BOT_TOKEN")           # токен от @BotFather
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN")        # Personal Access Token с правом repo
GITHUB_OWNER  = os.getenv("GITHUB_OWNER")        # твой username на GitHub
GITHUB_REPO   = os.getenv("GITHUB_REPO")         # название репозитория
ADMIN_ID      = int(os.getenv("ADMIN_ID", "0"))  # твой Telegram ID

# Файл с базой пользователей хранится прямо в репозитории
USERS_DB_PATH = "configs/users_db.json"
USERS_DIR     = "configs/users"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ─── GitHub API ───────────────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

async def github_get_file(session: aiohttp.ClientSession, path: str):
    """Получить файл из репозитория. Возвращает (content, sha) или (None, None)."""
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    async with session.get(url, headers=HEADERS) as r:
        if r.status == 404:
            return None, None
        data = await r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]

async def github_put_file(session: aiohttp.ClientSession, path: str, content: str, sha: str = None, message: str = "update"):
    """Создать или обновить файл в репозитории."""
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    async with session.put(url, headers=HEADERS, json=payload) as r:
        return r.status in (200, 201)

async def load_users_db(session: aiohttp.ClientSession) -> dict:
    """Загрузить базу пользователей из GitHub."""
    content, _ = await github_get_file(session, USERS_DB_PATH)
    if content is None:
        return {}
    try:
        return json.loads(content)
    except Exception:
        return {}

async def save_users_db(session: aiohttp.ClientSession, db: dict):
    """Сохранить базу пользователей в GitHub."""
    _, sha = await github_get_file(session, USERS_DB_PATH)
    content = json.dumps(db, ensure_ascii=False, indent=2)
    await github_put_file(session, USERS_DB_PATH, content, sha, message="update users db")

async def create_user_config_file(session: aiohttp.ClientSession, token: str) -> bool:
    """Создать пустой файл конфига для нового пользователя."""
    path = f"{USERS_DIR}/{token}.txt"
    placeholder = "# Конфиг обновляется автоматически каждые 2 часа.\n# Если файл пустой — подожди следующего обновления (до 2 часов).\n"
    return await github_put_file(session, path, placeholder, message=f"create config for {token[:8]}")

async def get_master_configs(session: aiohttp.ClientSession) -> str:
    """Получить текущие конфиги из master-файла."""
    content, _ = await github_get_file(session, "configs/mobile-whitelist-1.txt")
    return content or ""

def make_subscription_url(token: str) -> str:
    return f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/configs/users/{token}.txt"

# ─── Хендлеры бота ────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name or "пользователь"

    async with aiohttp.ClientSession() as session:
        db = await load_users_db(session)

        if user_id in db:
            token = db[user_id]["token"]
            sub_url = make_subscription_url(token)
            await message.answer(
                f"👋 Привет, {name}!\n\n"
                f"У тебя уже есть подписка. Используй команду /mylink чтобы получить ссылку.",
            )
            return

        # Новый пользователь — создаём токен
        token = uuid.uuid4().hex
        created = datetime.utcnow().isoformat()

        db[user_id] = {
            "token": token,
            "name": name,
            "username": message.from_user.username,
            "created": created,
        }

        # Создаём файл в GitHub и сохраняем базу
        await message.answer("⏳ Создаю твою персональную подписку...")

        ok = await create_user_config_file(session, token)
        await save_users_db(session, db)

        if not ok:
            await message.answer("❌ Ошибка при создании конфига. Попробуй /start ещё раз.")
            return

    sub_url = make_subscription_url(token)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data=f"copy:{token}")
    ]])

    await message.answer(
        f"✅ Готово, {name}!\n\n"
        f"Твоя персональная ссылка на подписку:\n\n"
        f"<code>{sub_url}</code>\n\n"
        f"📲 <b>Как использовать:</b>\n"
        f"1. Скачай <b>v2rayNG</b> (Android) или <b>Streisand</b> (iOS)\n"
        f"2. Добавь подписку → вставь ссылку выше\n"
        f"3. Нажми «Обновить» — конфиги загрузятся\n\n"
        f"🔄 Конфиги обновляются автоматически каждые 2 часа.\n"
        f"Если ссылка пустая — подожди до следующего обновления.\n\n"
        f"Команды:\n"
        f"/mylink — показать мою ссылку\n"
        f"/help — инструкция",
        parse_mode="HTML",
        reply_markup=kb,
    )

    # Уведомить админа
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🆕 Новый пользователь: {name} (@{message.from_user.username})\n"
                f"ID: {user_id}\nТокен: {token[:8]}..."
            )
        except Exception:
            pass

@dp.message(Command("mylink"))
async def cmd_mylink(message: types.Message):
    user_id = str(message.from_user.id)

    async with aiohttp.ClientSession() as session:
        db = await load_users_db(session)

    if user_id not in db:
        await message.answer("У тебя нет подписки. Напиши /start чтобы получить её.")
        return

    token   = db[user_id]["token"]
    sub_url = make_subscription_url(token)

    await message.answer(
        f"🔗 Твоя ссылка на подписку:\n\n<code>{sub_url}</code>\n\n"
        f"Добавь её в v2rayNG / Streisand как <b>Subscription</b>.",
        parse_mode="HTML",
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 <b>Инструкция</b>\n\n"
        "<b>Android (v2rayNG):</b>\n"
        "1. Скачай v2rayNG из Play Market\n"
        "2. ☰ → Subscription group → ＋\n"
        "3. Вставь свою ссылку → OK → Update\n"
        "4. Выбери сервер → ▶️ запуск\n\n"
        "<b>iOS (Streisand):</b>\n"
        "1. Скачай Streisand из App Store\n"
        "2. ＋ → Subscribe → вставь ссылку\n"
        "3. Выбери сервер → Connect\n\n"
        "Если не работает — попробуй другой сервер из списка.\n"
        "Конфиги обновляются каждые 2 часа автоматически.",
        parse_mode="HTML",
    )

# ─── Админ-команды ─────────────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    async with aiohttp.ClientSession() as session:
        db = await load_users_db(session)

    total = len(db)
    lines = [f"📊 <b>Статистика</b>\n\nВсего пользователей: <b>{total}</b>\n\nПоследние 10:"]
    for uid, info in list(db.items())[-10:]:
        lines.append(f"• {info.get('name', '?')} (@{info.get('username', '?')}) — {info['created'][:10]}")

    await message.answer("\n".join(lines), parse_mode="HTML")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    """Рассылка всем: /broadcast Текст сообщения"""
    if message.from_user.id != ADMIN_ID:
        return

    text = message.text.removeprefix("/broadcast").strip()
    if not text:
        await message.answer("Использование: /broadcast Текст сообщения")
        return

    async with aiohttp.ClientSession() as session:
        db = await load_users_db(session)

    sent, failed = 0, 0
    for user_id in db:
        try:
            await bot.send_message(int(user_id), text)
            sent += 1
            await asyncio.sleep(0.05)  # не спамить Telegram API
        except Exception:
            failed += 1

    await message.answer(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}")

@dp.callback_query(F.data.startswith("copy:"))
async def cb_copy(call: types.CallbackQuery):
    token   = call.data.split(":", 1)[1]
    sub_url = make_subscription_url(token)
    await call.answer(f"Ссылка скопирована!", show_alert=False)

# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    log.info("Bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
