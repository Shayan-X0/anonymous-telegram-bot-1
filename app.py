import os
import re
import sqlite3
import secrets
import string
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# تنظیمات اصلی
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = "anonymous_bot.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID تنظیم نشده است.")


# =========================
# وب‌سرور ساده برای Render/UptimeRobot
# =========================

web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Anonymous Telegram Bot is running ✅"


# =========================
# دیتابیس
# =========================

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            blocked INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_messages (
            admin_msg_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def generate_code(length=6):
    alphabet = string.ascii_uppercase + string.digits
    return "#" + "".join(secrets.choice(alphabet) for _ in range(length))


def get_or_create_user(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT user_id, code, blocked FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    if row:
        conn.close()
        return {
            "user_id": row[0],
            "code": row[1],
            "blocked": bool(row[2])
        }

    while True:
        code = generate_code()
        try:
            cur.execute(
                "INSERT INTO users (user_id, code, blocked) VALUES (?, ?, 0)",
                (user_id, code)
            )
            conn.commit()
            break
        except sqlite3.IntegrityError:
            continue

    conn.close()
    return {
        "user_id": user_id,
        "code": code,
        "blocked": False
    }


def get_user_by_code(code: str):
    code = normalize_code(code)

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT user_id, code, blocked FROM users WHERE code = ?", (code,))
    row = cur.fetchone()

    conn.close()

    if not row:
        return None

    return {
        "user_id": row[0],
        "code": row[1],
        "blocked": bool(row[2])
    }


def save_admin_message(admin_msg_id: int, user_id: int, code: str):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO admin_messages (admin_msg_id, user_id, code)
        VALUES (?, ?, ?)
    """, (admin_msg_id, user_id, code))

    conn.commit()
    conn.close()


def get_user_by_admin_message(admin_msg_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, code FROM admin_messages
        WHERE admin_msg_id = ?
    """, (admin_msg_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "user_id": row[0],
        "code": row[1]
    }


def set_blocked_by_code(code: str, blocked: bool):
    code = normalize_code(code)

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET blocked = ? WHERE code = ?",
        (1 if blocked else 0, code)
    )

    changed = cur.rowcount
    conn.commit()
    conn.close()

    return changed > 0


def set_blocked_by_user_id(user_id: int, blocked: bool):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET blocked = ? WHERE user_id = ?",
        (1 if blocked else 0, user_id)
    )

    changed = cur.rowcount
    conn.commit()
    conn.close()

    return changed > 0


def normalize_code(code: str):
    code = code.strip().upper()
    if not code.startswith("#"):
        code = "#" + code
    return code


def extract_code_from_text(text: str):
    if not text:
        return None

    match = re.search(r"#([A-Z0-9]{6})", text.upper())
    if not match:
        return None

    return "#" + match.group(1)


# =========================
# متن‌ها
# =========================

START_TEXT = """
سلام 👋

از اینجا می‌تونی پیام ناشناس بفرستی.

پیامت بدون اسم، یوزرنیم و تگ برای من ارسال می‌شه.
"""

MESSAGE_RECEIVED_TEXT = """
✅ پیامت رسید.

اگر نیاز باشه، از همین ربات جوابت رو می‌دم.
"""

MESSAGE_SEEN_TEXT = """
👀 پیامت دیده شد.
"""

BLOCKED_TEXT = """
⛔ امکان ارسال پیام برای شما وجود ندارد.
"""

ADMIN_HELP_TEXT = """
پنل ادمین ربات ناشناس

روش پاسخ دادن:
روی پیام کاربر Reply بزن و متن جوابت را بفرست.

یا با دستور:

/reply CODE متن پاسخ

مثال:
/reply A7K29Q سلام، پیامت رو دیدم.

دستورات مدیریت:

/block CODE
/unblock CODE

مثال:
/block A7K29Q
/unblock A7K29Q
"""


# =========================
# هندلرها
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id == ADMIN_ID:
        await update.message.reply_text(ADMIN_HELP_TEXT)
        return

    get_or_create_user(user_id)
    await update.message.reply_text(START_TEXT)


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(ADMIN_HELP_TEXT)


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id

    # اگر ادمین پیام معمولی داد و ریپلای بود، به کاربر جواب بده
    if user_id == ADMIN_ID:
        await handle_admin_reply(update, context)
        return

    user = get_or_create_user(user_id)

    if user["blocked"]:
        await message.reply_text(BLOCKED_TEXT)
        return

    code = user["code"]

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👀 دیده شد", callback_data=f"seen:{code}"),
            InlineKeyboardButton("⛔ بلاک", callback_data=f"block:{code}")
        ]
    ])

    header = (
        "📩 پیام ناشناس جدید\n\n"
        f"کد کاربر: {code}\n\n"
        "برای پاسخ، روی همین پیام یا پیام پایین Reply بزن."
    )

    sent_header = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=header,
        reply_markup=keyboard
    )

    save_admin_message(sent_header.message_id, user_id, code)

    # ارسال محتوای پیام بدون فوروارد کردن، تا هویت فرستنده نیاید
    try:
        copied = await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
        save_admin_message(copied.message_id, user_id, code)
    except Exception:
        # اگر به هر دلیلی copy نشد، متن را جدا بفرست
        if message.text:
            copied = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"متن پیام:\n\n{message.text}"
            )
            save_admin_message(copied.message_id, user_id, code)

    await message.reply_text(MESSAGE_RECEIVED_TEXT)


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if update.effective_user.id != ADMIN_ID:
        return

    # فقط وقتی ادمین روی پیام کاربر ریپلای کرده باشد
    if not message.reply_to_message:
        return

    replied_msg_id = message.reply_to_message.message_id
    target = get_user_by_admin_message(replied_msg_id)

    # اگر پیام در دیتابیس نبود، تلاش کن کد را از متن پیام ریپلای‌شده پیدا کنی
    if not target:
        code = extract_code_from_text(message.reply_to_message.text or message.reply_to_message.caption or "")
        if code:
            user = get_user_by_code(code)
            if user:
                target = {
                    "user_id": user["user_id"],
                    "code": user["code"]
                }

    if not target:
        await message.reply_text("❌ کاربر مربوط به این پیام پیدا نشد.")
        return

    user = get_user_by_code(target["code"])

    if not user:
        await message.reply_text("❌ این کاربر در دیتابیس پیدا نشد.")
        return

    if user["blocked"]:
        await message.reply_text("⛔ این کاربر بلاک است. اول آن را آنبلاک کن.")
        return

    try:
        # اگر جواب ادمین متن بود
        if message.text:
            await context.bot.send_message(
                chat_id=target["user_id"],
                text=message.text
            )
        else:
            # اگر عکس، ویس، ویدیو، فایل و... بود
            await context.bot.copy_message(
                chat_id=target["user_id"],
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

        await message.reply_text(f"✅ پاسخ برای کاربر {target['code']} ارسال شد.")

    except Exception as e:
        await message.reply_text(f"❌ ارسال پاسخ ناموفق بود:\n{e}")


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "فرمت درست:\n\n/reply CODE متن پاسخ\n\nمثال:\n/reply A7K29Q سلام"
        )
        return

    code = normalize_code(context.args[0])
    text = " ".join(context.args[1:])

    user = get_user_by_code(code)

    if not user:
        await update.message.reply_text("❌ کاربری با این کد پیدا نشد.")
        return

    if user["blocked"]:
        await update.message.reply_text("⛔ این کاربر بلاک است.")
        return

    try:
        await context.bot.send_message(
            chat_id=user["user_id"],
            text=text
        )
        await update.message.reply_text(f"✅ پاسخ برای {code} ارسال شد.")
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال پیام ناموفق بود:\n{e}")


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 1:
        await update.message.reply_text("فرمت درست:\n/block CODE")
        return

    code = normalize_code(context.args[0])
    ok = set_blocked_by_code(code, True)

    if ok:
        await update.message.reply_text(f"⛔ کاربر {code} بلاک شد.")
    else:
        await update.message.reply_text("❌ کاربری با این کد پیدا نشد.")


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 1:
        await update.message.reply_text("فرمت درست:\n/unblock CODE")
        return

    code = normalize_code(context.args[0])
    ok = set_blocked_by_code(code, False)

    if ok:
        await update.message.reply_text(f"✅ کاربر {code} آنبلاک شد.")
    else:
        await update.message.reply_text("❌ کاربری با این کد پیدا نشد.")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("اجازه دسترسی نداری.", show_alert=True)
        return

    data = query.data

    if ":" not in data:
        await query.answer()
        return

    action, code = data.split(":", 1)
    code = normalize_code(code)

    user = get_user_by_code(code)

    if not user:
        await query.answer("کاربر پیدا نشد.", show_alert=True)
        return

    if action == "seen":
        if user["blocked"]:
            await query.answer("این کاربر بلاک است.", show_alert=True)
            return

        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=MESSAGE_SEEN_TEXT
            )
            await query.answer("پیام دیده شد ارسال شد ✅")
        except Exception as e:
            await query.answer(f"خطا: {e}", show_alert=True)

    elif action == "block":
        set_blocked_by_code(code, True)
        await query.answer("کاربر بلاک شد ⛔")
        await query.message.reply_text(f"⛔ کاربر {code} بلاک شد.")

    elif action == "unblock":
        set_blocked_by_code(code, False)
        await query.answer("کاربر آنبلاک شد ✅")
        await query.message.reply_text(f"✅ کاربر {code} آنبلاک شد.")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("دستور نامعتبر است. /help را بزن.")
    else:
        await update.message.reply_text("پیامت را همینجا بفرست.")


# =========================
# اجرای ربات
# =========================

def run_bot():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", admin_help))
    application.add_handler(CommandHandler("reply", reply_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))

    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(MessageHandler(filters.ALL, handle_user_message))

    print("Bot is running...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None
    )


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()

    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)
