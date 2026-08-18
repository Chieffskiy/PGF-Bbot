import asyncio
import sqlite3
import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, FSInputFile, WebAppInfo
import threading

# ====== НАСТРОЙКИ (токен через переменную окружения!) ======
TOKEN = os.getenv("BOT_TOKEN")  # <-- БЕЗОПАСНО!
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в переменных окружения!")

ADMIN_ID = 1463056947
RULES_TEXT = "📋 Здесь будут правила модерации. Пока что заглушка."
MAP_URL = "https://chieffskiy.github.io/PGF-Bbot/"
# ===========================================================

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ---------- Flask (ВЕБ-СЕРВЕР ДЛЯ КАРТЫ) ----------
app = Flask(__name__)
CORS(app)  # <-- ЭТА СТРОЧКА РАЗРЕШАЕТ ЗАПРОСЫ С ДРУГИХ САЙТОВ

@app.route('/get_places', methods=['GET'])
def get_places_api():
    try:
        conn = sqlite3.connect('places.db')
        cur = conn.cursor()
        cur.execute('''
            SELECT id, place_name, latitude, longitude, photo_path 
            FROM places 
            WHERE status = "approved"
        ''')
        places = cur.fetchall()
        conn.close()
        result = []
        for p in places:
            photo_url = None
            if p[4] and os.path.exists(p[4]):
                photo_url = f"/photos/{os.path.basename(p[4])}"
            result.append({
                'id': p[0],
                'name': p[1] or 'Без названия',
                'lat': p[2],
                'lon': p[3],
                'photo': photo_url
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/photos/<filename>')
def get_photo(filename):
    try:
        return send_from_directory('photos', filename)
    except:
        return "Фото не найдено", 404

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            place_name TEXT,
            latitude REAL,
            longitude REAL,
            photo_path TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            registered_at TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            admin_reply TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            queue_position INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("rules", ?)', (RULES_TEXT,))
    conn.commit()
    conn.close()

init_db()

# ---------- КНОПКИ ----------
def get_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔎 Искать места")],
            [KeyboardButton(text="📤 Поделиться местом")],
            [KeyboardButton(text="👤 Мои места"), KeyboardButton(text="📋 Правила")],
            [KeyboardButton(text="🆘 Поддержка")]
        ],
        resize_keyboard=True
    )

def get_location_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True), KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def get_admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Все места"), KeyboardButton(text="⏳ На модерации")],
            [KeyboardButton(text="🗑️ Удалить место"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="💬 Обращения"), KeyboardButton(text="📝 Изменить правила")],
            [KeyboardButton(text="🔙 Выйти из админки")]
        ],
        resize_keyboard=True
    )

def get_support_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Написать обращение")],
            [KeyboardButton(text="📋 Мои обращения")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

def get_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def get_confirm_kb(action):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{action}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_{action}")
            ]
        ]
    )

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_temp(user_id):
    if not hasattr(dp, 'temp_data'):
        dp.temp_data = {}
    if user_id not in dp.temp_data:
        dp.temp_data[user_id] = {}
    return dp.temp_data[user_id]

def clear_temp(user_id):
    if hasattr(dp, 'temp_data') and user_id in dp.temp_data:
        del dp.temp_data[user_id]

def get_ticket(ticket_id):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, user_id, username, message, admin_reply, status, created_at FROM tickets WHERE id = ?', (ticket_id,))
    ticket = cur.fetchone()
    conn.close()
    return ticket

def get_pending_tickets():
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, username, message, created_at FROM tickets WHERE status = "pending" ORDER BY created_at ASC')
    tickets = cur.fetchall()
    conn.close()
    return tickets

def get_user_tickets(user_id):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, message, status, admin_reply, queue_position, created_at FROM tickets WHERE user_id = ? ORDER BY id DESC', (user_id,))
    tickets = cur.fetchall()
    conn.close()
    return tickets

def update_queue():
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id FROM tickets WHERE status = "pending" ORDER BY created_at ASC')
    tickets = cur.fetchall()
    for idx, ticket in enumerate(tickets, 1):
        cur.execute('UPDATE tickets SET queue_position = ? WHERE id = ?', (idx, ticket[0]))
    conn.commit()
    conn.close()

async def send_place_to_admin(place_id, chat_id):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, username, place_name, latitude, longitude, photo_path FROM places WHERE id = ?', (place_id,))
    place = cur.fetchone()
    conn.close()
    if not place:
        return
    user_id, username, place_name, lat, lon, photo_path = place
    caption = (
        f"🆕 **ЗАЯВКА НА МОДЕРАЦИЮ** (ID: `{place_id}`)\n"
        f"👤 От: @{username or 'anon'}\n"
        f"📝 Название: {place_name or 'Без названия'}\n"
        f"📍 {lat}, {lon}\n"
        f"[Открыть карту](https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=15)"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{place_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{place_id}")
            ]
        ]
    )
    if photo_path and os.path.exists(photo_path):
        await bot.send_photo(chat_id, photo=FSInputFile(photo_path), caption=caption, reply_markup=kb, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode="Markdown")

# ---------- ОСНОВНАЯ ЛОГИКА БОТА ----------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if not message.from_user: return
    user_id = message.from_user.id
    clear_temp(user_id)
    await message.answer(
        "👋 Привет! Ищешь красивые места для прогулок, поездок или фото? 📸\nТы попал по адресу!\n\n🔎 — найти места\n📤 — поделиться местом",
        reply_markup=get_main_kb()
    )

@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    if not message.from_user or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора!")
        return
    clear_temp(message.from_user.id)
    await message.answer("🛡️ **Админ-панель**", reply_markup=get_admin_kb(), parse_mode="Markdown")

@dp.message(lambda m: m.text == "🔎 Искать места")
async def search_places(m: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🗺️ Открыть карту мест",
                web_app=WebAppInfo(url=MAP_URL)
            )]
        ]
    )
    await m.answer(
        "🗺️ Нажми на кнопку, чтобы открыть карту с одобренными местами:\n\n"
        "📍 На карте отмечены все места, которые прошли модерацию.",
        reply_markup=kb
    )

@dp.message(lambda m: m.text == "📤 Поделиться местом")
async def share_start(m: types.Message):
    user_id = m.from_user.id
    clear_temp(user_id)
    data = get_temp(user_id)
    data['mode'] = 'add_place'
    data['media_group_id'] = None
    await m.answer("📍 Отправь геолокацию места", reply_markup=get_location_kb())

@dp.message(lambda m: m.text == "❌ Отмена" and get_temp(m.from_user.id).get('mode') == 'add_place')
async def cancel_location(m: types.Message):
    clear_temp(m.from_user.id)
    await m.answer("👋 Отменено", reply_markup=get_main_kb())

@dp.message(lambda m: m.location and get_temp(m.from_user.id).get('mode') == 'add_place')
async def handle_location(m: types.Message):
    user_id = m.from_user.id
    data = get_temp(user_id)
    data['lat'] = m.location.latitude
    data['lon'] = m.location.longitude
    await m.answer("📍 Место получено! Теперь напиши **НАЗВАНИЕ** (до 50 символов)", reply_markup=get_cancel_kb())

@dp.message(lambda m: m.text and len(m.text) <= 50 and get_temp(m.from_user.id).get('mode') == 'add_place' and get_temp(m.from_user.id).get('lat') and not get_temp(m.from_user.id).get('name'))
async def handle_name(m: types.Message):
    user_id = m.from_user.id
    data = get_temp(user_id)
    if m.text == "❌ Отмена":
        clear_temp(user_id)
        await m.answer("👋 Отменено", reply_markup=get_main_kb())
        return
    data['name'] = m.text
    await m.answer(f"📝 Название: {m.text}\n\nТеперь отправь **ОДНО ФОТО**", reply_markup=types.ReplyKeyboardRemove())

@dp.message(lambda m: m.photo and get_temp(m.from_user.id).get('mode') == 'add_place' and get_temp(m.from_user.id).get('name'))
async def handle_photo(m: types.Message):
    user_id = m.from_user.id
    data = get_temp(user_id)
    
    if m.media_group_id:
        if data.get('media_group_id') == m.media_group_id:
            await m.answer(
                "⚠️ Ты отправил несколько фото в одном сообщении.\n"
                "Пожалуйста, отправляй **только одно фото** за раз.\n\n"
                "Попробуй ещё раз: отправь одно фото этого места.",
                reply_markup=get_cancel_kb()
            )
            return
        else:
            data['media_group_id'] = m.media_group_id
    
    photo = m.photo[-1]
    file = await bot.get_file(photo.file_id)
    os.makedirs('photos', exist_ok=True)
    path = f"photos/{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    await bot.download_file(file.file_path, path)
    data['photo_path'] = path
    
    await m.answer_photo(
        photo=FSInputFile(path),
        caption=f"📍 **Финальный предпросмотр:**\n📝 {data['name']}\n📍 {data['lat']}, {data['lon']}\n\nОтправить на модерацию?",
        reply_markup=get_confirm_kb('place')
    )

@dp.message(lambda m: m.text == "👤 Мои места")
async def my_places(m: types.Message):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, place_name, status FROM places WHERE user_id = ? ORDER BY id DESC', (m.from_user.id,))
    places = cur.fetchall()
    conn.close()
    if not places:
        await m.answer("📭 Ты ещё не добавлял места.")
        return
    text = "👤 **Мои места:**\n\n"
    for p in places:
        emoji = "✅" if p[2] == "approved" else "⏳" if p[2] == "pending" else "❌"
        text += f"{emoji} ID `{p[0]}` — {p[1] or 'Без названия'}\n"
    await m.answer(text, parse_mode="Markdown")

@dp.message(lambda m: m.text == "📋 Правила")
async def rules_cmd(m: types.Message):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT value FROM settings WHERE key = "rules"')
    rules = cur.fetchone()[0]
    conn.close()
    await m.answer(rules)

@dp.message(lambda m: m.text == "🆘 Поддержка")
async def support_cmd(m: types.Message):
    await m.answer("🆘 **Поддержка**", reply_markup=get_support_kb(), parse_mode="Markdown")

@dp.message(lambda m: m.text == "🔙 Назад")
async def back_cmd(m: types.Message):
    await start_cmd(m)

@dp.message(lambda m: m.text == "📝 Написать обращение")
async def new_ticket(m: types.Message):
    user_id = m.from_user.id
    clear_temp(user_id)
    data = get_temp(user_id)
    data['mode'] = 'ticket'
    await m.answer("📝 Напиши своё обращение", reply_markup=get_cancel_kb())

@dp.message(lambda m: m.text and get_temp(m.from_user.id).get('mode') == 'ticket')
async def handle_ticket_text(m: types.Message):
    user_id = m.from_user.id
    if m.text == "❌ Отмена":
        clear_temp(user_id)
        await m.answer("👋 Отменено", reply_markup=get_support_kb())
        return
    data = get_temp(user_id)
    data['ticket_text'] = m.text
    await m.answer(f"📝 **Ваше обращение:**\n\n{m.text}\n\nОтправить?", reply_markup=get_confirm_kb('ticket'))

@dp.message(lambda m: m.text == "📋 Мои обращения")
async def my_tickets_cmd(m: types.Message):
    tickets = get_user_tickets(m.from_user.id)
    if not tickets:
        await m.answer("📭 У тебя нет обращений.")
        return
    text = "📋 **Мои обращения:**\n\n"
    for t in tickets:
        status_map = {'pending': '⏳ Ожидает', 'replied': '✅ Закрыто', 'rejected': '❌ Отклонено'}
        text += f"{status_map.get(t[2], '❓')} ID `{t[0]}` | Очередь: {t[4] or '?'}\n"
        text += f"📝 {t[1][:50]}...\n"
        if t[2] == 'replied' and t[3]:
            text += f"📨 Ответ: {t[3][:50]}...\n"
        text += "\n"
    await m.answer(text, parse_mode="Markdown")

@dp.message(lambda m: m.text == "💬 Обращения" and m.from_user.id == ADMIN_ID)
async def admin_tickets(m: types.Message):
    tickets = get_pending_tickets()
    if not tickets:
        await m.answer("📭 Нет активных обращений.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for t in tickets:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"🆔 {t[0]} | {t[1] or 'anon'} | {t[2][:20]}...", callback_data=f"ticket_{t[0]}")])
    await m.answer(f"💬 **Активные обращения:** {len(tickets)}", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data and c.data.startswith('ticket_') and c.from_user.id == ADMIN_ID)
async def view_ticket(c: types.CallbackQuery):
    ticket_id = int(c.data.split('_')[1])
    ticket = get_ticket(ticket_id)
    if not ticket or ticket[5] != 'pending':
        await c.answer("❌ Обращение уже обработано")
        return
    tid, user_id, username, msg, _, _, created = ticket
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"reply_ticket_{tid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_ticket_{tid}")]
    ])
    await c.message.answer(
        f"🆔 **Обращение #{tid}**\n👤 @{username or 'anon'}\n📅 {created[:16]}\n📝 {msg}\n\n⏳ Ожидает ответа",
        reply_markup=kb, parse_mode="Markdown"
    )
    await c.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('reject_ticket_') and c.from_user.id == ADMIN_ID)
async def reject_ticket(c: types.CallbackQuery):
    ticket_id = int(c.data.split('_')[2])
    ticket = get_ticket(ticket_id)
    if not ticket or ticket[5] != 'pending':
        await c.answer("❌ Уже обработано")
        return
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('UPDATE tickets SET status = "rejected" WHERE id = ?', (ticket_id,))
    conn.commit()
    conn.close()
    update_queue()
    await bot.send_message(ticket[1], f"❌ **Обращение #{ticket_id} отклонено.**")
    await c.message.edit_text(f"✅ Обращение #{ticket_id} отклонено")
    await c.answer("❌ Отклонено")

@dp.callback_query(lambda c: c.data and c.data.startswith('reply_ticket_') and c.from_user.id == ADMIN_ID)
async def reply_prompt(c: types.CallbackQuery):
    ticket_id = int(c.data.split('_')[2])
    ticket = get_ticket(ticket_id)
    if not ticket or ticket[5] != 'pending':
        await c.answer("❌ Уже обработано")
        return
    user_id = c.from_user.id
    clear_temp(user_id)
    data = get_temp(user_id)
    data['mode'] = 'reply_ticket'
    data['reply_ticket_id'] = ticket_id
    await c.message.answer(
        "✏️ Введи ответ на обращение.\n\nЧтобы отменить, нажми кнопку ниже.",
        reply_markup=get_cancel_kb()
    )
    await c.answer()

@dp.message(lambda m: m.text and get_temp(m.from_user.id).get('mode') == 'reply_ticket' and not get_temp(m.from_user.id).get('reply_text') and m.from_user.id == ADMIN_ID)
async def handle_reply_text(m: types.Message):
    user_id = m.from_user.id
    if m.text == "❌ Отмена":
        clear_temp(user_id)
        await m.answer("👋 Отменено", reply_markup=get_admin_kb())
        return
    data = get_temp(user_id)
    data['reply_text'] = m.text
    await m.answer(
        f"📨 **Ваш ответ:**\n\n{m.text}\n\nЧтобы отправить, напиши **/done**\nЧтобы отменить, нажми кнопку ниже.",
        reply_markup=get_cancel_kb()
    )

@dp.message(Command("done"))
async def done_reply(m: types.Message):
    user_id = m.from_user.id
    data = get_temp(user_id)
    
    if data.get('mode') != 'reply_ticket':
        return
    
    reply = data.get('reply_text')
    ticket_id = data.get('reply_ticket_id')
    
    if not reply or not ticket_id:
        await m.answer("❌ Ошибка: нет ответа или ID тикета")
        return
    
    ticket = get_ticket(ticket_id)
    if not ticket or ticket[5] != 'pending':
        await m.answer("❌ Это обращение уже обработано")
        clear_temp(user_id)
        return
    
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('UPDATE tickets SET admin_reply = ?, status = "replied" WHERE id = ?', (reply, ticket_id))
    conn.commit()
    conn.close()
    update_queue()
    
    await bot.send_message(ticket[1], f"✅ **Ответ на обращение** (ID: {ticket_id})\n\n📨 {reply}")
    
    clear_temp(user_id)
    await m.answer(f"✅ Ответ на обращение #{ticket_id} отправлен!", reply_markup=get_admin_kb())

@dp.message(lambda m: m.text == "📋 Все места" and m.from_user.id == ADMIN_ID)
async def admin_all_places(m: types.Message):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, username, place_name, status FROM places ORDER BY id DESC LIMIT 20')
    places = cur.fetchall()
    conn.close()
    if not places:
        await m.answer("📭 Нет мест")
        return
    text = "📋 **Последние 20 мест:**\n\n"
    for p in places:
        text += f"{'✅' if p[3]=='approved' else '⏳'} ID `{p[0]}` | {p[2] or 'Без названия'} | @{p[1] or 'anon'}\n"
    await m.answer(text, parse_mode="Markdown")

@dp.message(lambda m: m.text == "⏳ На модерации" and m.from_user.id == ADMIN_ID)
async def admin_pending(m: types.Message):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, username, place_name FROM places WHERE status = "pending"')
    places = cur.fetchall()
    conn.close()
    if not places:
        await m.answer("✅ Нет мест на модерации")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in places:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"📸 {p[2] or 'Без названия'} (ID {p[0]})", callback_data=f"show_{p[0]}")])
    await m.answer("⏳ **Места на модерации**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data and c.data.startswith('show_') and c.from_user.id == ADMIN_ID)
async def show_place(c: types.CallbackQuery):
    place_id = int(c.data.split('_')[1])
    await send_place_to_admin(place_id, c.from_user.id)
    await c.answer("✅ Заявка отправлена")

@dp.message(lambda m: m.text == "🗑️ Удалить место" and m.from_user.id == ADMIN_ID)
async def delete_prompt(m: types.Message):
    user_id = m.from_user.id
    clear_temp(user_id)
    data = get_temp(user_id)
    data['mode'] = 'delete_place'
    await m.answer("🗑️ Введи ID места для удаления", reply_markup=get_cancel_kb())

@dp.message(lambda m: m.text and get_temp(m.from_user.id).get('mode') == 'delete_place' and m.from_user.id == ADMIN_ID)
async def delete_confirm(m: types.Message):
    user_id = m.from_user.id
    if m.text == "❌ Отмена":
        clear_temp(user_id)
        await m.answer("👋 Отменено", reply_markup=get_admin_kb())
        return
    try:
        place_id = int(m.text)
    except:
        await m.answer("❌ Введи число!")
        return
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    cur.execute('SELECT id, place_name, photo_path FROM places WHERE id = ?', (place_id,))
    place = cur.fetchone()
    conn.close()
    if not place:
        await m.answer(f"❌ Место с ID {place_id} не найдено")
        return
    data = get_temp(user_id)
    data['delete_place_id'] = place_id
    data['delete_photo_path'] = place[2]
    await m.answer(f"🗑️ Удалить место \"{place[1] or 'Без названия'}\" (ID {place_id})?", reply_markup=get_confirm_kb('delete'))

@dp.message(lambda m: m.text == "📊 Статистика" and m.from_user.id == ADMIN_ID)
async def admin_stats(m: types.Message):
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    total = cur.execute('SELECT COUNT(*) FROM places').fetchone()[0]
    approved = cur.execute('SELECT COUNT(*) FROM places WHERE status = "approved"').fetchone()[0]
    pending = cur.execute('SELECT COUNT(*) FROM places WHERE status = "pending"').fetchone()[0]
    tickets_total = cur.execute('SELECT COUNT(*) FROM tickets').fetchone()[0]
    tickets_pending = cur.execute('SELECT COUNT(*) FROM tickets WHERE status = "pending"').fetchone()[0]
    conn.close()
    await m.answer(
        f"📊 **Статистика:**\n\n"
        f"📌 Всего мест: {total}\n"
        f"✅ Одобрено: {approved}\n"
        f"⏳ На модерации: {pending}\n\n"
        f"💬 Всего обращений: {tickets_total}\n"
        f"⏳ Активных: {tickets_pending}",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == "📝 Изменить правила" and m.from_user.id == ADMIN_ID)
async def edit_rules_prompt(m: types.Message):
    user_id = m.from_user.id
    clear_temp(user_id)
    data = get_temp(user_id)
    data['mode'] = 'edit_rules'
    await m.answer("📝 Введи новый текст правил", reply_markup=get_cancel_kb())

@dp.message(lambda m: m.text and get_temp(m.from_user.id).get('mode') == 'edit_rules' and m.from_user.id == ADMIN_ID)
async def edit_rules_confirm(m: types.Message):
    user_id = m.from_user.id
    if m.text == "❌ Отмена":
        clear_temp(user_id)
        await m.answer("👋 Отменено", reply_markup=get_admin_kb())
        return
    data = get_temp(user_id)
    data['rules_text'] = m.text
    await m.answer(f"📋 **Новые правила:**\n\n{m.text}\n\nСохранить?", reply_markup=get_confirm_kb('rules'))

@dp.message(lambda m: m.text == "🔙 Выйти из админки" and m.from_user.id == ADMIN_ID)
async def admin_exit(m: types.Message):
    clear_temp(m.from_user.id)
    await start_cmd(m)

# ---------- ОБЩИЙ ОБРАБОТЧИК ПОДТВЕРЖДЕНИЙ ----------
@dp.callback_query(lambda c: c.data and c.data.startswith('confirm_'))
async def confirm_action(c: types.CallbackQuery):
    user_id = c.from_user.id
    action = c.data.split('_')[1]
    data = get_temp(user_id)

    if action == 'ticket':
        text = data.get('ticket_text')
        if not text:
            await c.answer("❌ Ошибка")
            return
        conn = sqlite3.connect('places.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO tickets (user_id, username, message, status, created_at) VALUES (?, ?, ?, ?, ?)',
                    (user_id, c.from_user.username or "anon", text, 'pending', datetime.now().isoformat()))
        conn.commit()
        ticket_id = cur.lastrowid
        conn.close()
        update_queue()
        await bot.send_message(ADMIN_ID, f"🆕 **НОВОЕ ОБРАЩЕНИЕ** (ID: {ticket_id})\n👤 @{c.from_user.username or 'anon'}\n📝 {text[:200]}...", parse_mode="Markdown")
        clear_temp(user_id)
        await c.message.edit_text(f"✅ Обращение #{ticket_id} отправлено")
        await c.answer("✅ Отправлено")
        await support_cmd(c.message)

    elif action == 'place':
        lat = data.get('lat')
        lon = data.get('lon')
        name = data.get('name')
        photo = data.get('photo_path')
        if not all([lat, lon, name, photo]):
            await c.answer("❌ Ошибка")
            return
        conn = sqlite3.connect('places.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO places (user_id, username, place_name, latitude, longitude, photo_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (user_id, c.from_user.username or "anon", name, lat, lon, photo, 'pending', datetime.now().isoformat()))
        conn.commit()
        place_id = cur.lastrowid
        conn.close()
        clear_temp(user_id)
        await c.message.edit_caption(caption=c.message.caption + "\n\n✅ **Отправлено на модерацию!**")
        await c.answer("✅ Отправлено")
        await start_cmd(c.message)

    elif action == 'delete':
        place_id = data.get('delete_place_id')
        photo_path = data.get('delete_photo_path')
        if not place_id:
            await c.answer("❌ Ошибка")
            return
        if photo_path and os.path.exists(photo_path):
            os.remove(photo_path)
        conn = sqlite3.connect('places.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM places WHERE id = ?', (place_id,))
        conn.commit()
        conn.close()
        clear_temp(user_id)
        await c.message.edit_text(f"✅ Место #{place_id} удалено")
        await c.answer("✅ Удалено")
        await admin_cmd(c.message)

    elif action == 'rules':
        rules_text = data.get('rules_text')
        if not rules_text:
            await c.answer("❌ Ошибка")
            return
        conn = sqlite3.connect('places.db')
        cur = conn.cursor()
        cur.execute('UPDATE settings SET value = ? WHERE key = "rules"', (rules_text,))
        conn.commit()
        conn.close()
        clear_temp(user_id)
        await c.message.edit_text("✅ Правила обновлены")
        await c.answer("✅ Сохранено")
        await admin_cmd(c.message)

    else:
        await c.answer("❌ Неизвестное действие")

@dp.callback_query(lambda c: c.data and c.data.startswith('cancel_'))
async def cancel_action(c: types.CallbackQuery):
    user_id = c.from_user.id
    action = c.data.split('_')[1]
    clear_temp(user_id)
    await c.message.edit_text("👋 Отменено")
    await c.answer("❌ Отменено")
    if action in ['rules', 'delete']:
        await admin_cmd(c.message)
    elif action == 'ticket':
        await support_cmd(c.message)
    elif action == 'place':
        await start_cmd(c.message)
    else:
        await start_cmd(c.message)

@dp.callback_query(lambda c: c.data and (c.data.startswith('approve_') or c.data.startswith('reject_')) and c.from_user.id == ADMIN_ID)
async def moderate_place(c: types.CallbackQuery):
    action, place_id = c.data.split('_')
    place_id = int(place_id)
    conn = sqlite3.connect('places.db')
    cur = conn.cursor()
    if action == 'approve':
        cur.execute('UPDATE places SET status = "approved" WHERE id = ?', (place_id,))
        conn.commit()
        await c.message.edit_caption(caption=c.message.caption + "\n\n✅ **ОДОБРЕНО**")
        await c.answer("✅ Одобрено")
    else:
        cur.execute('SELECT photo_path FROM places WHERE id = ?', (place_id,))
        photo = cur.fetchone()
        if photo and photo[0] and os.path.exists(photo[0]):
            os.remove(photo[0])
        cur.execute('DELETE FROM places WHERE id = ?', (place_id,))
        conn.commit()
        await c.message.edit_caption(caption=c.message.caption + "\n\n❌ **ОТКЛОНЕНО**")
        await c.answer("❌ Отклонено")
    conn.close()

# ---------- ЗАПУСК ----------
def run_flask():
    # Render назначает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

async def main():
    print("✅ Бот запущен с WebApp картой!")
    print(f"✅ Админ ID: {ADMIN_ID}")
    print(f"✅ Карта доступна по ссылке: {MAP_URL}")
    
    threading.Thread(target=run_flask).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())