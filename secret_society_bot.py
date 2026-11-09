"""
SECRET SOCIETY Telegram Bot — MVP
Python 3.10+
Install:
    pip install aiogram aiosqlite pillow
Run:
    export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    export ADMIN_ID="YOUR_TELEGRAM_NUMERIC_ID"
    python secret_society_bot.py

Features:
- Admin panel inside Telegram (missions, XP, announcements, stats, reward review)
- SQLite persistence
- Ranks and XP
- Mission list and mission completion (admin-approved)
- Reward application workflow to admin private chat
- Generated classified-dossier cover image for each mission (Pillow)
- Admin can also attach a custom image to a mission
"""

import asyncio
import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(".")
DB_PATH = str(DATA_DIR / "secret_society.sqlite3")
ASSET_DIR = DATA_DIR / "mission_assets"
ASSET_DIR.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN or not ADMIN_ID:
    raise RuntimeError("Set BOT_TOKEN and ADMIN_ID environment variables first.")

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

RANKS = [
    ("Initiate", 0),
    ("Operative", 100),
    ("Phantom", 500),
    ("Elite", 1500),
    ("Shadow", 3000),
    ("Candidate", 5000),
    ("Council", 10000),
]

class AdminFlow(StatesGroup):
    mission_title = State()
    mission_description = State()
    mission_reward_xp = State()
    mission_photo = State()
    xp_user_id = State()
    xp_amount = State()
    announce_text = State()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def rank_for(xp: int) -> str:
    current = RANKS[0][0]
    for name, threshold in RANKS:
        if xp >= threshold:
            current = name
    return current

def rank_progress(xp: int):
    current_idx = 0
    for i, (_, threshold) in enumerate(RANKS):
        if xp >= threshold:
            current_idx = i
    current_name, current_threshold = RANKS[current_idx]
    next_rank = RANKS[current_idx + 1] if current_idx + 1 < len(RANKS) else None
    if next_rank:
        next_name, next_threshold = next_rank
        pct = int((xp - current_threshold) / max(1, next_threshold - current_threshold) * 100)
        return current_name, next_name, next_threshold, max(0, min(100, pct))
    return current_name, None, None, 100

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_kb():
    return kb([
        [InlineKeyboardButton(text="➕ ساخت مأموریت", callback_data="admin:newmission"),
         InlineKeyboardButton(text="📋 مأموریت‌ها", callback_data="admin:missions")],
        [InlineKeyboardButton(text="🎁 درخواست جایزه‌ها", callback_data="admin:rewards"),
         InlineKeyboardButton(text="⭐ دادن XP", callback_data="admin:xp")],
        [InlineKeyboardButton(text="📢 پیام همگانی", callback_data="admin:announce"),
         InlineKeyboardButton(text="📊 آمار ربات", callback_data="admin:stats")],
    ])

def user_kb():
    return kb([
        [InlineKeyboardButton(text="🗂 مأموریت‌ها", callback_data="user:missions"),
         InlineKeyboardButton(text="🪪 پروفایل", callback_data="user:profile")],
        [InlineKeyboardButton(text="🎁 درخواست بررسی جایزه", callback_data="user:reward")],
        [InlineKeyboardButton(text="🏆 رتبه‌بندی", callback_data="user:leaderboard")],
    ])

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            xp INTEGER DEFAULT 0, created_at TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS missions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT,
            xp INTEGER DEFAULT 100, image_path TEXT, active INTEGER DEFAULT 1,
            created_at TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS completions(
            user_id INTEGER, mission_id INTEGER, status TEXT DEFAULT 'pending',
            submitted_at TEXT, PRIMARY KEY(user_id, mission_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS reward_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, status TEXT DEFAULT 'pending',
            created_at TEXT)""")
        await db.commit()

async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name, xp, created_at) VALUES(?,?,?,?,?)",
            (user.id, user.username or "", user.first_name or "", 0,
             datetime.now(timezone.utc).isoformat())
        )
        await db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                         (user.username or "", user.first_name or "", user.id))
        await db.commit()

def make_dossier_image(mission_id: int, title: str) -> str:
    """Create a simple classified dossier cover for a mission."""
    path = ASSET_DIR / f"mission_{mission_id}.png"
    img = Image.new("RGB", (1000, 650), (14, 17, 20))
    d = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
        font_mid = ImageFont.truetype("DejaVuSans.ttf", 26)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font_big = font_mid = font_small = ImageFont.load_default()
    # Dossier border, stamp, paper panel
    d.rectangle((35, 35, 965, 615), outline=(75, 105, 95), width=3)
    d.rectangle((65, 85, 935, 560), fill=(25, 30, 31), outline=(70, 80, 78), width=2)
    d.text((85, 55), "SECRET SOCIETY  //  INTELLIGENCE DIVISION", font=font_small, fill=(135, 190, 160))
    d.text((85, 120), f"CASE FILE #{mission_id:04d}", font=font_mid, fill=(210, 215, 205))
    d.line((85, 165, 900, 165), fill=(90, 115, 105), width=2)
    wrapped = textwrap.wrap(title.upper(), width=28)[:3]
    y = 205
    for line in wrapped:
        d.text((85, y), line, font=font_big, fill=(235, 235, 220))
        y += 58
    d.text((85, 475), "STATUS: CLASSIFIED", font=font_mid, fill=(210, 75, 70))
    d.text((85, 520), "AUTHORIZED OPERATIVES ONLY", font=font_small, fill=(130, 160, 145))
    # red stamp
    d.rectangle((700, 205, 885, 280), outline=(170, 55, 50), width=5)
    d.text((720, 225), "CLASSIFIED", font=font_mid, fill=(200, 70, 65))
    img.save(path)
    return str(path)

@dp.message(CommandStart())
async def start(message: Message):
    await ensure_user(message.from_user)
    await message.answer(
        "🕶️ <b>SECRET SOCIETY</b>\n\nبه سازمان خوش آمدی، Operative.\nاز پنل زیر استفاده کن.",
        reply_markup=user_kb(), parse_mode="HTML"
    )

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ دسترسی مدیر لازم است.")
    await message.answer("🛠 <b>پنل مدیریت سازمان</b>", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("دسترسی غیرمجاز", show_alert=True)
    action = call.data.split(":", 1)[1]
    await call.answer()
    if action == "newmission":
        await state.set_state(AdminFlow.mission_title)
        await call.message.answer("عنوان مأموریت جدید را ارسال کن:")
    elif action == "missions":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,title,xp,active FROM missions ORDER BY id DESC")
            rows = await cur.fetchall()
        if not rows:
            return await call.message.answer("هنوز مأموریتی ثبت نشده.")
        text = "\n".join(f"#{i} | {title} | {xp} XP | {'فعال' if active else 'غیرفعال'}" for i,title,xp,active in rows)
        await call.message.answer(text)
    elif action == "rewards":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,user_id,created_at FROM reward_requests WHERE status='pending' ORDER BY id")
            rows = await cur.fetchall()
        if not rows:
            return await call.message.answer("درخواست جایزه‌ی بررسی‌نشده‌ای نیست.")
        for rid, uid, created in rows:
            await call.message.answer(
                f"🎁 درخواست #{rid}\nUser ID: <code>{uid}</code>\n{created}",
                parse_mode="HTML",
                reply_markup=kb([[InlineKeyboardButton(text="✅ تأیید", callback_data=f"reward:approve:{rid}"),
                                  InlineKeyboardButton(text="❌ رد", callback_data=f"reward:reject:{rid}")]])
            )
    elif action == "xp":
        await state.set_state(AdminFlow.xp_user_id)
        await call.message.answer("Telegram User ID کاربر را بفرست:")
    elif action == "announce":
        await state.set_state(AdminFlow.announce_text)
        await call.message.answer("متن اطلاعیه را بفرست:")
    elif action == "stats":
        async with aiosqlite.connect(DB_PATH) as db:
            users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
            missions = (await (await db.execute("SELECT COUNT(*) FROM missions WHERE active=1")).fetchone())[0]
            pending = (await (await db.execute("SELECT COUNT(*) FROM reward_requests WHERE status='pending'")).fetchone())[0]
        await call.message.answer(f"📊 کاربران: {users}\nمأموریت‌های فعال: {missions}\nدرخواست جایزه در انتظار: {pending}")

@dp.message(AdminFlow.mission_title)
async def mission_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(title=message.text[:100])
    await state.set_state(AdminFlow.mission_description)
    await message.answer("شرح مأموریت را ارسال کن:")

@dp.message(AdminFlow.mission_description)
async def mission_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(description=message.text[:3000])
    await state.set_state(AdminFlow.mission_reward_xp)
    await message.answer("چند XP جایزه بده؟ (عدد، مثلاً 150)")

@dp.message(AdminFlow.mission_reward_xp)
async def mission_xp(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: xp = max(1, min(100000, int(message.text)))
    except ValueError: return await message.answer("لطفاً عدد معتبر بفرست.")
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO missions(title,description,xp,created_at) VALUES(?,?,?,?)",
                               (data["title"], data["description"], xp, datetime.now(timezone.utc).isoformat()))
        mission_id = cur.lastrowid
        await db.commit()
    path = make_dossier_image(mission_id, data["title"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE missions SET image_path=? WHERE id=?", (path, mission_id))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ مأموریت #{mission_id} ساخته شد و تصویر پرونده‌ای آن تولید شد.\nXP: {xp}",
                         reply_markup=admin_kb())

@dp.message(AdminFlow.xp_user_id)
async def xp_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: uid = int(message.text)
    except ValueError: return await message.answer("User ID باید عدد باشد.")
    await state.update_data(uid=uid)
    await state.set_state(AdminFlow.xp_amount)
    await message.answer("مقدار XP (برای کم کردن، عدد منفی بفرست):")

@dp.message(AdminFlow.xp_amount)
async def xp_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: amount = int(message.text)
    except ValueError: return await message.answer("عدد معتبر بفرست.")
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET xp=MAX(0,xp+?) WHERE user_id=?", (amount, data["uid"]))
        await db.commit()
    await state.clear()
    await message.answer("⭐ XP کاربر به‌روزرسانی شد.", reply_markup=admin_kb())

@dp.message(AdminFlow.announce_text)
async def announcement(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        ids = [r[0] for r in await cur.fetchall()]
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, "📢 <b>اطلاعیه سازمان</b>\n\n" + message.text[:3500], parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await state.clear()
    await message.answer(f"اطلاعیه برای {sent} کاربر ارسال شد.", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("user:"))
async def user_callbacks(call: CallbackQuery):
    await ensure_user(call.from_user)
    action = call.data.split(":", 1)[1]
    await call.answer()
    if action == "profile":
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT xp FROM users WHERE user_id=?", (call.from_user.id,))).fetchone()
        xp = row[0] if row else 0
        rank, next_rank, next_xp, pct = rank_progress(xp)
        text = f"🪪 <b>پرونده عضو</b>\nID: <code>{call.from_user.id}</code>\nXP: {xp}\nRank: <b>{rank}</b>"
        if next_rank: text += f"\nرتبه بعدی: {next_rank} ({next_xp} XP)\nپیشرفت: {pct}%"
        await call.message.answer(text, parse_mode="HTML")
    elif action == "missions":
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT id,title,description,xp,image_path FROM missions WHERE active=1 ORDER BY id DESC")).fetchall()
        if not rows: return await call.message.answer("فعلاً مأموریت فعالی وجود ندارد.")
        for mid,title,desc,xp,img_path in rows:
            markup = kb([[InlineKeyboardButton(text="📩 ارسال برای بررسی", callback_data=f"mission:submit:{mid}")]])
            caption = f"🗂 <b>CASE #{mid:04d}</b>\n<b>{title}</b>\n\n{desc}\n\n🎯 پاداش: {xp} XP"
            try:
                with open(img_path, "rb") as f:
                    await call.message.answer_photo(BufferedInputFile(f.read(), filename="case.png"), caption=caption,
                                                    parse_mode="HTML", reply_markup=markup)
            except Exception:
                await call.message.answer(caption, parse_mode="HTML", reply_markup=markup)
    elif action == "reward":
        async with aiosqlite.connect(DB_PATH) as db:
            xp_row = await (await db.execute("SELECT xp FROM users WHERE user_id=?", (call.from_user.id,))).fetchone()
            xp = xp_row[0] if xp_row else 0
            if xp < 5000:
                return await call.message.answer(f"برای درخواست جایزه باید حداقل 5000 XP داشته باشی.\nXP فعلی: {xp}")
            cur = await db.execute("INSERT INTO reward_requests(user_id,created_at) VALUES(?,?)",
                                   (call.from_user.id, datetime.now(timezone.utc).isoformat()))
            rid = cur.lastrowid
            await db.commit()
        await call.message.answer("🎁 درخواستت برای مدیر ارسال شد. منتظر بررسی باش.")
        await bot.send_message(ADMIN_ID, f"🎁 درخواست جایزه #{rid}\nUser: {call.from_user.id}\nUsername: @{call.from_user.username or 'ندارد'}",
                               reply_markup=kb([[InlineKeyboardButton(text="✅ تأیید", callback_data=f"reward:approve:{rid}"),
                                                 InlineKeyboardButton(text="❌ رد", callback_data=f"reward:reject:{rid}")]]))
    elif action == "leaderboard":
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT first_name,xp FROM users ORDER BY xp DESC LIMIT 10")).fetchall()
        text = "🏆 <b>Top Operatives</b>\n\n" + "\n".join(f"{i}. {name or 'Operative'} — {xp} XP" for i,(name,xp) in enumerate(rows,1))
        await call.message.answer(text, parse_mode="HTML")

@dp.callback_query(F.data.startswith("mission:submit:"))
async def mission_submit(call: CallbackQuery):
    mid = int(call.data.rsplit(":", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT xp,title FROM missions WHERE id=? AND active=1", (mid,))).fetchone()
        if not row: return await call.answer("مأموریت پیدا نشد.", show_alert=True)
        try:
            await db.execute("INSERT INTO completions(user_id,mission_id,status,submitted_at) VALUES(?,?,?,?)",
                             (call.from_user.id, mid, "pending", datetime.now(timezone.utc).isoformat()))
            await db.commit()
        except aiosqlite.IntegrityError:
            return await call.answer("قبلاً برای این مأموریت درخواست ثبت کرده‌ای.", show_alert=True)
    await call.answer("درخواست ثبت شد.")
    await call.message.answer("📩 درخواست انجام مأموریت ثبت شد؛ مدیر بررسی می‌کند.")
    await bot.send_message(ADMIN_ID, f"📝 بررسی مأموریت #{mid}: {row[1]}\nکاربر: {call.from_user.id}",
                           reply_markup=kb([[InlineKeyboardButton(text="✅ تأیید و XP", callback_data=f"missionapprove:{call.from_user.id}:{mid}"),
                                             InlineKeyboardButton(text="❌ رد", callback_data=f"missionreject:{call.from_user.id}:{mid}")]]))

@dp.callback_query(F.data.startswith("missionapprove:"))
async def mission_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی غیرمجاز", show_alert=True)
    _, uid, mid = call.data.split(":")
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT xp,title FROM missions WHERE id=?", (int(mid),))).fetchone()
        if not row: return await call.answer("مأموریت پیدا نشد.")
        cur = await db.execute("UPDATE completions SET status='approved' WHERE user_id=? AND mission_id=? AND status='pending'",
                               (int(uid), int(mid)))
        if cur.rowcount == 0: return await call.answer("این درخواست قبلاً بررسی شده.")
        await db.execute("UPDATE users SET xp=xp+? WHERE user_id=?", (row[0], int(uid)))
        await db.commit()
    await bot.send_message(int(uid), f"✅ مأموریت «{row[1]}» تأیید شد!\n+{row[0]} XP")
    await call.message.edit_text(f"تأیید شد: کاربر {uid}، مأموریت {mid}")
    await call.answer("تأیید شد")

@dp.callback_query(F.data.startswith("missionreject:"))
async def mission_reject(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی غیرمجاز", show_alert=True)
    _, uid, mid = call.data.split(":")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE completions SET status='rejected' WHERE user_id=? AND mission_id=? AND status='pending'",
                         (int(uid), int(mid)))
        await db.commit()
    await bot.send_message(int(uid), "❌ درخواست مأموریتت رد شد. می‌تونی دوباره تلاش کنی.")
    await call.message.edit_text("درخواست رد شد.")
    await call.answer()

@dp.callback_query(F.data.startswith("reward:"))
async def reward_review(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی غیرمجاز", show_alert=True)
    _, decision, rid = call.data.split(":")
    status = "approved" if decision == "approve" else "rejected"
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT user_id,status FROM reward_requests WHERE id=?", (int(rid),))).fetchone()
        if not row or row[1] != "pending": return await call.answer("قبلاً بررسی شده.")
        await db.execute("UPDATE reward_requests SET status=? WHERE id=?", (status, int(rid)))
        await db.commit()
    await bot.send_message(row[0], "🎁 درخواست جایزه‌ات تأیید شد! برای هماهنگی جایزه با مدیر صحبت کن." if decision == "approve"
                           else "درخواست جایزه‌ات فعلاً تأیید نشد.")
    await call.message.edit_text(f"درخواست #{rid}: {status}")
    await call.answer("انجام شد")

async def main():
    await init_db()
    print("Secret Society bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
