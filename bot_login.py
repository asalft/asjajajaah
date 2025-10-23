import os
import json
import asyncio
import tempfile
import signal
import random
import glob

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- إعدادات ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
SESSION_STORE = os.environ.get("SESSION_STORE", "./sessions.json")

PHOTO_FOLDER = "photos"       # المجلد الذي يحتوي الصور
CHANGE_INTERVAL = 300         # عدد الثواني بين كل تغيير (5 دقائق)

AWAIT_PHONE, AWAIT_CODE, AWAIT_PASS, AWAIT_PHOTO = range(4)
clients = {}

# --- تحميل/حفظ الجلسات ---
def load_sessions():
    try:
        with open(SESSION_STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_sessions(sessions):
    with open(SESSION_STORE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

# --- لوحة التحكم ---
def main_menu():
    kb = [
        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="login"),
         InlineKeyboardButton("📸 تغيير الصورة الآن", callback_data="change_photo")],
        [InlineKeyboardButton("⛔ تسجيل الخروج", callback_data="logout")],
    ]
    return InlineKeyboardMarkup(kb)

# --- بدء التشغيل ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 هذا البوت خاص بالمالك فقط.")
        return
    await update.message.reply_text("مرحباً! استخدم الأزرار بالأسفل 👇", reply_markup=main_menu())

# --- معالجة الأزرار ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id

    if uid != OWNER_ID:
        await q.edit_message_text("🚫 غير مصرح.")
        return

    if q.data == "login":
        await q.edit_message_text("📞 أرسل رقم هاتفك الآن (مثال: +201234567890):")
        return AWAIT_PHONE
    elif q.data == "change_photo":
        await q.edit_message_text("📸 أرسل الصورة الآن بأي طريقة (صورة أو ملف أو محوّلة).")
        return AWAIT_PHOTO
    elif q.data == "logout":
        sessions = load_sessions()
        sid = str(uid)
        if sid in sessions:
            try:
                entry = clients.pop(uid, None)
                if entry:
                    await entry["client"].disconnect()
                sessions.pop(sid)
                save_sessions(sessions)
                await q.edit_message_text("✅ تم تسجيل الخروج بنجاح.")
            except Exception as e:
                await q.edit_message_text(f"⚠️ حدث خطأ أثناء تسجيل الخروج: {e}")
        else:
            await q.edit_message_text("لا توجد جلسة محفوظة.")
    else:
        await q.edit_message_text("❓ خيار غير معروف.")

# --- تسجيل الدخول ---
async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    phone = update.message.text.strip()
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        await client.send_code_request(phone)
        context.user_data["client"] = client
        context.user_data["phone"] = phone
        await update.message.reply_text("📨 تم إرسال كود إلى حسابك، أرسله هنا:")
        return AWAIT_CODE
    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الكود: {e}")
        await client.disconnect()
        return ConversationHandler.END

async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    client: TelegramClient = context.user_data.get("client")
    phone = context.user_data.get("phone")
    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        await update.message.reply_text("🔒 الحساب محمي بكلمة مرور (2FA)، أرسلها الآن:")
        return AWAIT_PASS
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ الكود غير صحيح، حاول مجدداً.")
        await client.disconnect()
        return ConversationHandler.END

    me = await client.get_me()
    session_str = client.session.save()
    sessions = load_sessions()
    sessions[str(update.effective_user.id)] = {"session": session_str}
    save_sessions(sessions)
    clients[update.effective_user.id] = {"client": client, "me": me.to_dict()}
    await update.message.reply_text(f"✅ تم تسجيل الدخول بنجاح إلى: {me.first_name}", reply_markup=main_menu())
    return ConversationHandler.END

async def receive_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client: TelegramClient = context.user_data.get("client")
    await client.sign_in(password=password)
    me = await client.get_me()
    session_str = client.session.save()
    sessions = load_sessions()
    sessions[str(update.effective_user.id)] = {"session": session_str}
    save_sessions(sessions)
    clients[update.effective_user.id] = {"client": client, "me": me.to_dict()}
    await update.message.reply_text(f"✅ تم تسجيل الدخول بنجاح إلى: {me.first_name}", reply_markup=main_menu())
    return ConversationHandler.END

# --- استلام الصور ---
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sessions = load_sessions()
    sess = sessions.get(str(uid))
    if not sess:
        await update.message.reply_text("❌ لا يوجد حساب مسجل. سجل الدخول أولاً.")
        return ConversationHandler.END

    client = clients.get(uid, {}).get("client")
    if not client:
        client = TelegramClient(StringSession(sess["session"]), API_ID, API_HASH)
        await client.connect()
        clients[uid] = {"client": client}

    tmp_dir = tempfile.gettempdir()
    files = []

    if update.message.photo:
        f = os.path.join(tmp_dir, f"{uid}_photo.jpg")
        await update.message.photo[-1].get_file().download(custom_path=f)
        files.append(f)
    elif update.message.document and update.message.document.mime_type.startswith("image/"):
        f = os.path.join(tmp_dir, update.message.document.file_name)
        await update.message.document.get_file().download(custom_path=f)
        files.append(f)
    else:
        await update.message.reply_text("❌ الرجاء إرسال صورة أو ملف صورة.")
        return ConversationHandler.END

    for path in files:
        try:
            uploaded = await client.upload_file(path)
            await client(UploadProfilePhotoRequest(uploaded))
            await update.message.reply_text("✅ تم تحديث الصورة الشخصية بنجاح.")
        except Exception as e:
            await update.message.reply_text(f"⚠️ فشل رفع الصورة: {e}")
        finally:
            try:
                os.remove(path)
            except:
                pass

    return ConversationHandler.END

# --- التبديل التلقائي للصور ---
async def auto_change_photos():
    await asyncio.sleep(10)  # انتظار بسيط بعد التشغيل
    print("🔁 بدأ تبديل الصور التلقائي...")
    while True:
        try:
            # اختر أي عميل متصل
            if not clients:
                await asyncio.sleep(CHANGE_INTERVAL)
                continue

            for uid, entry in clients.items():
                client = entry["client"]
                if not await client.is_connected():
                    await client.connect()

                photo_files = glob.glob(os.path.join(PHOTO_FOLDER, "*"))
                if not photo_files:
                    print("⚠️ لا توجد صور في مجلد photos/")
                    await asyncio.sleep(CHANGE_INTERVAL)
                    continue

                chosen = random.choice(photo_files)
                uploaded = await client.upload_file(chosen)
                await client(UploadProfilePhotoRequest(uploaded))
                print(f"✅ تم تعيين الصورة الجديدة: {chosen}")

            await asyncio.sleep(CHANGE_INTERVAL)
        except Exception as e:
            print(f"⚠️ خطأ في التبديل التلقائي: {e}")
            await asyncio.sleep(CHANGE_INTERVAL)

# --- إنهاء الجلسات ---
async def shutdown_clients():
    for entry in clients.values():
        c = entry.get("client")
        if c:
            try:
                await c.disconnect()
            except:
                pass
    clients.clear()

# --- بناء المحادثة ---
def build_conversation():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_query_handler)],
        states={
            AWAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            AWAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)],
            AWAIT_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pass)],
            AWAIT_PHOTO: [MessageHandler(
                (
                    filters.PHOTO |
                    filters.Document.IMAGE |
                    (filters.FORWARDED & (filters.PHOTO | filters.Document.IMAGE))
                ),
                receive_photo
            )],
        },
        fallbacks=[],
        allow_reentry=True,
    )

# --- التشغيل الرئيسي ---
def main():
    if not BOT_TOKEN or not API_ID or not API_HASH or not OWNER_ID:
        print("⚠️ تأكد من إعداد BOT_TOKEN و API_ID و API_HASH و OWNER_ID.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(build_conversation())

    loop = asyncio.get_event_loop()

    def stop_signal_handler(*_):
        print("🛑 إيقاف البوت...")
        loop.create_task(shutdown_clients())
        loop.stop()

    signal.signal(signal.SIGTERM, stop_signal_handler)
    signal.signal(signal.SIGINT, stop_signal_handler)

    # تشغيل مهمة التبديل التلقائي بالتوازي
    loop.create_task(auto_change_photos())

    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
