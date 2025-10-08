
"""
Telegram-бот «Дети & Panda» — запись без слотов + 4‑значный код
Обновление: дополнительные триггеры старта и проверка связи.
- /start — приветствие
- /menu — то же самое, что /start
- текст "старт" или "start" без слеша — тоже покажет меню
- /ping — быстрый тест, что бот отвечает
- /count — количество записей (только админ)
- /export — экспорт CSV (только админ)
"""

from __future__ import annotations

import asyncio
import csv
import os
import re
import sqlite3
import random
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

import phonenumbers
import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
)

# Конфигурация
BOT_TOKEN = "8156929581:AAHiEHkJLSZqiupefNjOCJ9nSV5Rsp5NwUI"
ADMIN_CHAT_ID = 5778964874

TZ = pytz.timezone("Europe/Moscow")
EVENT_DATE = datetime(2025, 10, 25, tzinfo=TZ)

DB_PATH = "bookings.db"
CSV_EXPORT = "bookings_export.csv"

WELCOME_TEXT = (
    "👋 Здравствуйте!\n"
    "Добро пожаловать в наш уютный частный детский сад в Некрасовке 🌿\n\n"
    "🎉 Приглашаем вас и вашего малыша на День открытых дверей!\n"
    "📅 25 октября 2025 г. (суббота)\n"
    "🕙 Начало в 10:00\n"
    "📍 г. Москва, ул. Маршала Еременко, д. 5, корп. 5\n\n"
    "ПРОГРАММА СТРОГО ДЛЯ ДЕТЕЙ ОТ 1,5 ДО 5 ЛЕТ!\n\n"
    "✨ Программа мероприятия:\n"
    "10:00 — Приветствие и открытие\n"
    "10:30 — Спектакль кукольного театра 🎭\n"
    "10:40 — Мастер-класс по лечебной физкультуре + детский психолог🤸‍♀️\n"
    "11:00 — Розыгрыш призов\n\n"
    "💛 Вся программа бесплатная!\n"
    "Это отличная возможность познакомиться с нашими воспитателями, увидеть, как проходят занятия, "
    "и почувствовать атмосферу заботы и уюта 🌼\n\n"
    "Хотите записаться на участие?\n"
    "👉 Нажмите кнопку «Записаться», и мы закрепим за вами место."
)

# ─── Модель Брони ─────────────────────────────────────────────────────────────
@dataclass
class Booking:
    code: str
    parent: str
    phone_e164: str
    child_age: str
    created_at: str

# ─── Утилиты БД ───────────────────────────────────────────────────────────────
def get_columns(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("PRAGMA table_info(bookings)")
    return [row[1] for row in cur.fetchall()]

def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        # Новая схема
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                parent TEXT,
                phone_e164 TEXT UNIQUE,
                child_age TEXT,
                created_at TEXT
            )
            """
        )
        cols = get_columns(conn)
        if "code" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN code TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_code ON bookings(code)")
        conn.commit()

def find_by_phone(phone_e164: str) -> Optional[Booking]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT code, parent, phone_e164, child_age, created_at FROM bookings WHERE phone_e164 = ?",
            (phone_e164,),
        )
        row = cur.fetchone()
        if row:
            return Booking(*row)
        return None

def code_exists(code: str) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT 1 FROM bookings WHERE code = ?", (code,))
        return cur.fetchone() is not None

def generate_unique_code() -> str:
    for _ in range(10000):
        code = f"{random.randint(1, 9999):04d}"
        if not code_exists(code):
            return code
    return datetime.now().strftime("%H%M")

def insert_booking(b: Booking) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cols = get_columns(conn)
        if "slot" in cols:
            # Старая схема: есть NOT NULL slot → положим пустую строку
            conn.execute(
                "INSERT INTO bookings (code, parent, phone_e164, child_age, created_at, slot) VALUES (?,?,?,?,?,?)",
                (b.code, b.parent, b.phone_e164, b.child_age, b.created_at, ""),
            )
        else:
            conn.execute(
                "INSERT INTO bookings (code, parent, phone_e164, child_age, created_at) VALUES (?,?,?,?,?)",
                (b.code, b.parent, b.phone_e164, b.child_age, b.created_at),
            )
        conn.commit()

def count_total() -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM bookings")
        return int(cur.fetchone()[0])

# ─── Вспомогательные функции ─────────────────────────────────────────────────
def format_phone_to_e164(raw: str, region: str = "RU") -> Optional[str]:
    raw = raw.strip().replace(" ", "")
    try:
        if raw.startswith("8") and len(raw) == 11:
            raw = "+7" + raw[1:]
        parsed = phonenumbers.parse(raw, region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass
    return None

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Записаться", callback_data="signup:start")]]
    )

# ─── FSM ──────────────────────────────────────────────────────────────────────
class Form(StatesGroup):
    parent_name = State()
    phone = State()
    child_age = State()
    confirm = State()

# ─── Логика бота ──────────────────────────────────────────────────────────────
async def main() -> None:
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Проверка связи
    @dp.message(Command("ping"))
    async def ping(msg: Message) -> None:
        await msg.answer("pong")

    @dp.message(CommandStart())
    async def on_start(msg: Message, state: FSMContext) -> None:
        await state.clear()
        await msg.answer(WELCOME_TEXT, reply_markup=start_keyboard())

    @dp.message(Command("menu"))
    async def on_menu(msg: Message, state: FSMContext) -> None:
        await state.clear()
        await msg.answer(WELCOME_TEXT, reply_markup=start_keyboard())

    # На случай, если пользователь напишет "старт" или "start" без слеша
    @dp.message(StateFilter(None), F.text.as_("t"))
    async def on_any_text(msg: Message, t: str, state: FSMContext) -> None:
        if t is None:
            return
        low = t.strip().lower()
        if low in {"start", "старт"}:
            await state.clear()
            await msg.answer(WELCOME_TEXT, reply_markup=start_keyboard())

    @dp.callback_query(F.data == "signup:start")
    async def signup_start(cb: CallbackQuery, state: FSMContext) -> None:
        await cb.message.answer("Введите ФИО родителя (как обращаться):")
        await state.set_state(Form.parent_name)
        await cb.answer()

    @dp.message(Form.parent_name)
    async def on_parent_name(msg: Message, state: FSMContext) -> None:
        name = re.sub(r"\s+", " ", msg.text.strip())
        if len(name) < 2:
            await msg.answer("Пожалуйста, укажите корректное имя.")
            return
        await state.update_data(parent=name)
        await msg.answer("Укажите телефон для связи (например, +7 999 123-45-67):")
        await state.set_state(Form.phone)

    @dp.message(Form.phone)
    async def on_phone(msg: Message, state: FSMContext) -> None:
        phone_e164 = format_phone_to_e164(msg.text)
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
        age = msg.text.strip()
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

    @dp.callback_query(Form.confirm, F.data.startswith("confirm:"))
    async def on_confirm(cb: CallbackQuery, state: FSMContext) -> None:
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
            except Exception:
                pass

        await state.clear()
        await cb.answer()

    @dp.message(Command("export"))
    async def export_csv(msg: Message) -> None:
        if ADMIN_CHAT_ID and msg.from_user and msg.from_user.id != ADMIN_CHAT_ID:
            return
        rows = [("created_at", "code", "parent", "phone_e164", "child_age")]
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.execute("SELECT created_at, code, parent, phone_e164, child_age FROM bookings ORDER BY id DESC")
            rows.extend(cur.fetchall())
        with open(CSV_EXPORT, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        await msg.answer_document(document=open(CSV_EXPORT, "rb"), caption="Экспорт записей")

    @dp.message(Command("count"))
    async def count_cmd(msg: Message) -> None:
        if not (msg.from_user and msg.from_user.id == ADMIN_CHAT_ID):
            return
        total = count_total()
        await msg.answer(f"📊 Всего записавшихся: <b>{total}</b>")

    print("Bot started…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Установите переменную окружения BOT_TOKEN")
    asyncio.run(main())
    # --- FastAPI обёртка для Render ---
from fastapi import FastAPI
import uvicorn
import os, asyncio

app = FastAPI()
_polling_task = None

@app.get("/")
async def health():
    return {"status": "ok"}

@app.on_event("startup")
async def _on_startup():
    global _polling_task
    loop = asyncio.get_event_loop()
    _polling_task = loop.create_task(main())

@app.on_event("shutdown")
async def _on_shutdown():
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except Exception:
            pass

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
