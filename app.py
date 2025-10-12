import os
import re
import csv
import sqlite3
import logging
import asyncio
from contextlib import closing
from datetime import datetime
from typing import Optional

import pytz
from fastapi import FastAPI, Request, Response
from aiogram import F, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Update,
)

# ========= Настройка логирования =========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========= Конфигурация =========
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8156929581:AAE7Pew7XX6wxnh4Nh_WA4jeVorkHfh4k2A").strip()
ADMIN_CHAT_ID: Optional[int] = 5778964874
PUBLIC_URL: str = os.getenv("PUBLIC_URL", "").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

logger.info(f"🔑 Bot token: {BOT_TOKEN[:10]}...")
logger.info(f"🌐 Public URL: {PUBLIC_URL}")
logger.info(f"👑 Admin ID: {ADMIN_CHAT_ID}")

TZ = pytz.timezone("Europe/Moscow")
DB_PATH = "db.sqlite3"
CSV_EXPORT = "bookings_export.csv"

# Инициализация бота с таймаутами
bot = Bot(
    BOT_TOKEN, 
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# ========= Тексты и клавиатуры =========
WELCOME_TEXT = (
    "👋 Здравствуйте!\n"
    "Добро пожаловать в наш уютный частный детский сад в Некрасовке 🌿\n\n"
    "🎉 Приглашаем вас и вашего малыша на День открытых дверей!\n"
    "📅 25 октября 2025 г. (суббота)\n"
    "🕙 Начало в 10:00\n"
    "📍 г. Москва, ул. Маршала Еременко, д. 5, корп. 5\n\n"
    "✨ Программа мероприятия:\n"
    "10:00 — Приветствие и открытие\n"
    "10:30 — Спектакль кукольного театра 🎭\n"
    "10:40 — Мастер-класс по лечебной физкультуре 🤸‍♀️\n"
    "11:00 — Розыгрыш призов\n\n"
    "💛 Вся программа бесплатная!\n"
    "Это отличная возможность познакомиться с нашими воспитателями, увидеть, как проходят занятия, и почувствовать атмосферу заботы и уюта 🌼\n\n"
    "Хотите записаться на участие?\n"
    "👉 Нажмите кнопку «Записаться», и мы закрепим за вами место."
)

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Записаться", callback_data="signup:start")]
        ]
    )

# ========= БД =========
def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              code TEXT NOT NULL,
              parent TEXT NOT NULL,
              phone_e164 TEXT NOT NULL,
              child_age TEXT NOT NULL
            )
        """)
        conn.commit()

class Booking:
    def __init__(self, code: str, parent: str, phone_e164: str, child_age: str, created_at: str):
        self.code = code
        self.parent = parent
        self.phone_e164 = phone_e164
        self.child_age = child_age
        self.created_at = created_at

def insert_booking(b: Booking) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO bookings(created_at, code, parent, phone_e164, child_age) VALUES (?, ?, ?, ?, ?)",
            (b.created_at, b.code, b.parent, b.phone_e164, b.child_age),
        )
        conn.commit()

def find_by_phone(phone_e164: str) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT 1 FROM bookings WHERE phone_e164 = ? LIMIT 1", (phone_e164,))
        return cur.fetchone() is not None

def count_total() -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM bookings")
        row = cur.fetchone()
        return int(row[0]) if row else 0

# ========= Валидации/утилиты =========
def format_phone_to_e164(text: str) -> Optional[str]:
    digits = re.sub(r"[^\d+]", "", text or "")
    if digits.startswith("+7") and len(re.sub(r"\D", "", digits)) == 11:
        return digits
    digits_only = re.sub(r"\D", "", text or "")
    if digits_only.startswith("8") and len(digits_only) == 11:
        return "+7" + digits_only[1:]
    return None

def generate_unique_code() -> str:
    return f"{datetime.now().timestamp():.6f}".replace(".", "")[-4:]

# ========= FSM =========
class Form(StatesGroup):
    parent_name = State()
    phone = State()
    child_age = State()
    confirm = State()

# ========= Хэндлеры =========

@dp.message(Command("ping"))
async def ping(msg: Message) -> None:
    logger.info(f"🎯 Сработал /ping от {msg.from_user.id}")
    await msg.answer("🏓 pong! Бот активен!")

@dp.message(CommandStart())
async def on_start(msg: Message, state: FSMContext) -> None:
    logger.info(f"🎯 Сработал /start от {msg.from_user.id}")
    await state.clear()
    await msg.answer(WELCOME_TEXT, reply_markup=start_keyboard())

@dp.message(Command("menu"))
async def on_menu(msg: Message, state: FSMContext) -> None:
    logger.info(f"🎯 Сработал /menu от {msg.from_user.id}")
    await state.clear()
    await msg.answer(WELCOME_TEXT, reply_markup=start_keyboard())

@dp.message(Command("status"))
async def status_cmd(msg: Message) -> None:
    logger.info(f"📊 Сработал /status от {msg.from_user.id}")
    total = count_total()
    await msg.answer(f"🤖 Бот активен!\n📊 Всего записей: {total}\n⏰ Время сервера: {datetime.now(TZ).strftime('%H:%M:%S')}")

@dp.message(Command("export"))
async def export_csv(msg: Message) -> None:
    logger.info(f"📊 Сработал /export от {msg.from_user.id}")
    if ADMIN_CHAT_ID and msg.from_user and msg.from_user.id != ADMIN_CHAT_ID:
        await msg.answer("❌ У вас нет прав для этой команды")
        return
    
    try:
        rows = [("created_at", "code", "parent", "phone_e164", "child_age")]
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.execute("SELECT created_at, code, parent, phone_e164, child_age FROM bookings ORDER BY id DESC")
            rows.extend(cur.fetchall())
        
        with open(CSV_EXPORT, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        
        await msg.answer_document(
            document=open(CSV_EXPORT, "rb"), 
            caption=f"📊 Экспорт записей ({len(rows)-1} записей)"
        )
        logger.info(f"✅ Экспорт завершен, записей: {len(rows)-1}")
    except Exception as e:
        logger.error(f"❌ Ошибка экспорта: {e}")
        await msg.answer("❌ Ошибка при экспорте данных")

@dp.message(Command("count"))
async def count_cmd(msg: Message) -> None:
    logger.info(f"📈 Сработал /count от {msg.from_user.id}")
    if not (msg.from_user and ADMIN_CHAT_ID and msg.from_user.id == ADMIN_CHAT_ID):
        await msg.answer("❌ У вас нет прав для этой команды")
        return
    
    total = count_total()
    await msg.answer(f"📊 Всего записавшихся: <b>{total}</b>")

# Callback handlers
@dp.callback_query(F.data == "signup:start")
async def signup_start(cb: CallbackQuery, state: FSMContext) -> None:
    logger.info(f"🎯 Сработал signup:start от {cb.from_user.id}")
    await cb.message.answer("Введите ФИО родителя (как обращаться):")
    await state.set_state(Form.parent_name)
    await cb.answer()

@dp.callback_query(Form.confirm, F.data.startswith("confirm:"))
async def on_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    logger.info(f"✅ Сработал confirm: {cb.data}")
    action = cb.data.split(":", 1)[1]
    if action == "no":
        await state.clear()
        await cb.message.answer("Запись отменена. Если передумаете — нажмите /start")
        await cb.answer()
        return

    data = await state.get_data()
    code = generate_unique_code()
    booking = Booking(
        code=code,
        parent=data["parent"],
        phone_e164=data["phone_e164"],
        child_age=data["child_age"],
        created_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z"),
    )
    insert_booking(booking)

    await cb.message.answer(
        "Готово! Ваша запись подтверждена. ✅\n"
        f"Ваш персональный номер участника: <b>{booking.code}</b>\n\n"
        "Дата мероприятия: 25.10.2025 (суббота)\n"
        "Встречаемся к 10:00 по адресу:\n"
        "г. Москва, ул. Маршала Еременко, д. 5, корп. 5\n\n"
        "Сохраните этот номер — по нему вы будете участвовать в розыгрыше призов. До встречи!"
    )

    if ADMIN_CHAT_ID:
        try:
            await cb.bot.send_message(
                ADMIN_CHAT_ID,
                (
                    "🆕 Новая запись на День открытых дверей\n"
                    f"Код: {booking.code}\n"
                    f"Родитель: {booking.parent}\n"
                    f"Телефон: {booking.phone_e164}\n"
                    f"Возраст ребёнка: {booking.child_age}\n"
                    f"Создано: {booking.created_at}"
                ),
            )
        except Exception as e:
            logger.error(f"❌ Ошибка отправки админу: {e}")

    await state.clear()
    await cb.answer()

# Form state handlers
@dp.message(Form.parent_name)
async def on_parent_name(msg: Message, state: FSMContext) -> None:
    logger.info(f"📝 Получено имя родителя: {msg.text}")
    name = re.sub(r"\s+", " ", (msg.text or "").strip())
    if len(name) < 2:
        await msg.answer("Пожалуйста, укажите корректное имя.")
        return
    await state.update_data(parent=name)
    await msg.answer("Укажите телефон для связи (например, +7 999 123-45-67):")
    await state.set_state(Form.phone)

@dp.message(Form.phone)
async def on_phone(msg: Message, state: FSMContext) -> None:
    logger.info(f"📞 Получен телефон: {msg.text}")
    phone_e164 = format_phone_to_e164(msg.text or "")
    if not phone_e164:
        await msg.answer("Не удалось распознать номер. Пришлите в формате +7XXXXXXXXXX")
        return
    if find_by_phone(phone_e164):
        await msg.answer("Этим номером уже оформлена запись. Укажите другой номер или свяжитесь с администратором.")
        return
    await state.update_data(phone_e164=phone_e164)
    await msg.answer("Возраст ребёнка (например, 3 года 4 месяца):")
    await state.set_state(Form.child_age)

@dp.message(Form.child_age)
async def on_child_age(msg: Message, state: FSMContext) -> None:
    logger.info(f"👶 Получен возраст: {msg.text}")
    age = (msg.text or "").strip()
    if len(age) < 1:
        await msg.answer("Пожалуйста, укажите возраст ребёнка.")
        return
    await state.update_data(child_age=age)
    data = await state.get_data()
    text = (
        "Проверьте данные:\n\n"
        f"<b>Родитель:</b> {data['parent']}\n"
        f"<b>Телефон:</b> {data['phone_e164']}\n"
        f"<b>Возраст ребёнка:</b> {data['child_age']}\n\n"
        "Подтвердить запись?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm:yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:no")],
    ])
    await msg.answer(text, reply_markup=kb)
    await state.set_state(Form.confirm)

# Fallback handler
@dp.message()
async def unknown_message(msg: Message) -> None:
    logger.info(f"❓ Неизвестное сообщение от {msg.from_user.id}: {msg.text}")
    await msg.answer("Не понимаю команду. Используйте /start для начала работы.")

# ========= FastAPI + Webhook =========
app = FastAPI(title="Telegram Bot", version="1.0")

@app.get("/")
async def health():
    return {
        "status": "ok", 
        "bot": "active", 
        "timestamp": datetime.now(TZ).isoformat(),
        "records_count": count_total()
    }

@app.get("/health")
async def health_check():
    """Эндпоинт для проверки здоровья сервиса"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(TZ).isoformat(),
        "service": "telegram-bot"
    }

@app.get("/ping")
async def ping_server():
    """Эндпоинт для пинга сервера"""
    return {"message": "pong", "timestamp": datetime.now(TZ).isoformat()}

@app.get("/stats")
async def get_stats():
    """Статистика бота"""
    return {
        "total_records": count_total(),
        "server_time": datetime.now(TZ).isoformat(),
        "webhook_url": f"{PUBLIC_URL}/webhook" if PUBLIC_URL else "Not set"
    }

@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Запуск приложения...")
    
    # Инициализация БД
    init_db()
    logger.info("✅ База данных инициализирована")
    
    # Настройка вебхука
    try:
        # Удаляем старый вебхук
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Старый вебхук удален")
        
        if PUBLIC_URL:
            webhook_url = f"{PUBLIC_URL}/webhook"
            await bot.set_webhook(
                webhook_url,
                max_connections=40,
                drop_pending_updates=True
            )
            logger.info(f"✅ Webhook установлен: {webhook_url}")
            
            # Проверяем установку вебхука
            webhook_info = await bot.get_webhook_info()
            logger.info(f"📋 Webhook info: {webhook_info.url}")
            logger.info(f"📊 Pending updates: {webhook_info.pending_update_count}")
        else:
            logger.warning("⚠️ PUBLIC_URL не установлен. Вебхук не настроен.")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при настройке вебхука: {e}")
        # Пытаемся установить вебхук повторно через 5 секунд
        await asyncio.sleep(5)
        try:
            if PUBLIC_URL:
                await bot.set_webhook(f"{PUBLIC_URL}/webhook")
                logger.info(f"✅ Webhook переустановлен после ошибки")
        except Exception as retry_error:
            logger.error(f"❌ Повторная ошибка вебхука: {retry_error}")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Выключение приложения...")
    try:
        await bot.session.close()
        logger.info("✅ Сессия бота закрыта")
    except Exception as e:
        logger.error(f"❌ Ошибка при закрытии сессии: {e}")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"📨 Получен webhook запрос от Telegram")
        
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"❌ Ошибка в вебхуке: {e}")
        return Response(status_code=500)
