# bot_login.py
import os
import json
import asyncio
import tempfile
import signal

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneNumberInvalidError
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.functions.account import UpdateProfileRequest

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

# --- إعدادات من متغيرات البيئة ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
SESSION_STORE = os.environ.get("SESSION_STORE", "./sessions.json")

# --- Conversation states ---
AWAIT_PHONE, AWAIT_CODE, AWAIT_PASS, AWAIT_NAME, AWAIT_PHOTO = range(5)
clients = {}  # ذاكرة الجلسات في الذاكرة

# --- Helpers ---
def load_sessions():
    try:
        with open(SESSION_STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_sessions(sessions):
    try:
        with open(SESSION_STORE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("فشل حفظ الجلسات:", e)

def main_menu():
    kb = [
        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="login"),
         InlineKeyboardButton("⛔ تسجيل الخروج", callback_data="logout")],
        [InlineKeyboardButton("📸 تغيير الصورة", callback_data="change_photo"),
         InlineKeyboardButton("✏️ تغيير الاسم", callback_data="change_name")],
    ]
    return InlineKeyboardMarkup(kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("هذا البوت خاص بالمالك فقط.")
        return
    await update.message.reply_text(
        "أهلاً! استخدم الأزرار أدناه.\n"
        "اضغط على 'تسجيل الدخول' لبدء جلسة المستخدم عبر الهاتف.",
        reply_markup=main_menu()
    )

# --- Callback handler ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await q.edit_message_text("غير مصرح.")
        return

    data = q.data
    if data == "login":
        await q.edit_message_text("أرسل رقم هاتفك بصيغة دولية (مثال: +201234567890).")
        return AWAIT_PHONE
    elif data == "logout":
        sesss = load_sessions()
        sid = str(uid)
        if sid in sesss:
            try:
                entry = clients.get(uid)
                if entry:
                    c = entry.get("client")
                    if c and awaitable_is_connected(c):
                        try:
                            await c.disconnect()
                        except:
                            pass
                    clients.pop(uid, None)
                sesss.pop(sid, None)
                save_sessions(sesss)
                await q.edit_message_text("تم تسجيل الخروج وحذف الجلسة.")
            except Exception as e:
                await q.edit_message_text(f"حدث خطأ أثناء تسجيل الخروج: {e}")
        else:
            await q.edit_message_text("لا توجد جلسة محفوظة لتسجيل الخروج منها.")
    elif data == "change_name":
        await q.edit_message_text("أرسل الاسم الجديد. يمكنك كتابة 'الاسم الأول|اللقب' لو أحببت.")
        return AWAIT_NAME
    elif data == "change_photo":
        await q.edit_message_text("أرسل الصورة أو الصور الآن. يمكنك إرسال أكثر من صورة متتابعة.")
        return AWAIT_PHOTO
    else:
        await q.edit_message_text("خيار غير معروف.")

async def awaitable_is_connected(client: TelegramClient):
    """
    Telethon's is_connected قد تكون sync أو coroutine حسب النسخة،
    فانظر هنا وادعم الحالتين.
    """
    try:
        res = client.is_connected()
        if asyncio.iscoroutine(res):
            return await res
        return res
    except TypeError:
        try:
            return await client.is_connected()
        except:
            return False
    except Exception:
        return False

# --- Receive phone ---
async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("غير مصرح.")
        return ConversationHandler.END

    phone = update.message.text.strip()
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await client.connect()
        await client.send_code_request(phone)
        context.user_data["login_client_tmp"] = client
        context.user_data["login_phone"] = phone
        await update.message.reply_text("تم إرسال كود التسجيل إلى حسابك. أرسل الكود هنا.")
        return AWAIT_CODE
    except Exception as e:
        try:
            await client.disconnect()
        except:
            pass
        await update.message.reply_text(f"فشل إرسال الكود: {e}")
        return ConversationHandler.END

# --- Receive code ---
async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("غير مصرح.")
        return ConversationHandler.END

    code = update.message.text.strip()
    client: TelegramClient = context.user_data.get("login_client_tmp")
    phone = context.user_data.get("login_phone")
    if not client or not phone:
        await update.message.reply_text("انتهت الجلسة المؤقتة، أعد المحاولة من جديد (/start).")
        return ConversationHandler.END

    try:
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            await update.message.reply_text("الحساب محمي بكلمة مرور (2FA). أرسل كلمة المرور الآن.")
            return AWAIT_PASS
        except PhoneCodeInvalidError:
            await update.message.reply_text("الكود غير صحيح. أعد المحاولة أو اطلب كود جديد.")
            await client.disconnect()
            return ConversationHandler.END

        me = await client.get_me()
        session_str = client.session.save()
        sessions = load_sessions()
        sessions[str(uid)] = {"session": session_str}
        save_sessions(sessions)
        clients[uid] = {"client": client, "session": session_str, "me": me.to_dict()}
        await update.message.reply_text(f"تم تسجيل الدخول بنجاح إلى: {me.username or me.first_name}", reply_markup=main_menu())
        return ConversationHandler.END
    except Exception as e:
        try:
            await client.disconnect()
        except:
            pass
        await update.message.reply_text(f"فشل تسجيل الدخول: {e}")
        return ConversationHandler.END

# --- Receive 2FA password ---
async def receive_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("غير مصرح.")
        return ConversationHandler.END

    password = update.message.text.strip()
    client: TelegramClient = context.user_data.get("login_client_tmp")
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        session_str = client.session.save()
        sessions = load_sessions()
        sessions[str(uid)] = {"session": session_str}
        save_sessions(sessions)
        clients[uid] = {"client": client, "session": session_str, "me": me.to_dict()}
        await update.message.reply_text(f"تم تسجيل الدخول بنجاح (2FA) إلى: {me.username or me.first_name}", reply_markup=main_menu())
        return ConversationHandler.END
    except Exception as e:
        try:
            await client.disconnect()
        except:
            pass
        await update.message.reply_text(f"فشل تسجيل الدخول بكلمة المرور: {e}")
        return ConversationHandler.END

# --- Change name ---
async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("غير مصرح.")
        return ConversationHandler.END

    text = update.message.text.strip()
    first, last = (text.split("|", 1) + [None])[:2]

    entry = clients.get(uid)
    if not entry:
        sessions = load_sessions()
        sess = sessions.get(str(uid))
        if not sess:
            await update.message.reply_text("لا يوجد حساب مسجل. سجل الدخول أولاً.")
            return ConversationHandler.END
        client = TelegramClient(StringSession(sess["session"]), API_ID, API_HASH)
        await client.connect()
        entry = {"client": client, "session": sess["session"]}
        clients[uid] = entry

    client: TelegramClient = entry["client"]
    try:
        await client(UpdateProfileRequest(first_name=first, last_name=last))
        await update.message.reply_text("تم تحديث الاسم بنجاح.", reply_markup=main_menu())
    except Exception as e:
        await update.message.reply_text(f"فشل تحديث الاسم: {e}")
    return ConversationHandler.END

# --- Set profile photos ---
async def set_profile_photos(client: TelegramClient, file_paths: list, delay: int = 5):
    for path in file_paths:
        try:
            uploaded = await client.upload_file(path)
            await client(UploadProfilePhotoRequest(uploaded))
            print(f"تم تعيين الصورة: {path}")
            await asyncio.sleep(delay)
        except Exception as e:
            print(f"فشل تعيين الصورة {path}: {e}")

# --- Receive photo(s) ---
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("غير مصرح.")
        return ConversationHandler.END

    entry = clients.get(uid)
    if not entry:
        sessions = load_sessions()
        sess = sessions.get(str(uid))
        if not sess:
            await update.message.reply_text("لا يوجد حساب مسجل. سجل الدخول أولاً.")
            return ConversationHandler.END
        client = TelegramClient(StringSession(sess["session"]), API_ID, API_HASH)
        await client.connect()
        entry = {"client": client, "session": sess["session"]}
        clients[uid] = entry

    client: TelegramClient = entry["client"]
    files_to_set = []

    # Photo
    if update.message.photo:
        tmp_dir = tempfile.gettempdir()
        file_path = os.path.join(tmp_dir, f"tg_upload_{uid}.jpg")
        await update.message.photo[-1].get_file().download(custom_path=file_path)
        files_to_set.append(file_path)
    # Document
    elif update.message.document:
        tmp_dir = tempfile.gettempdir()
        file_path = os.path.join(tmp_dir, f"tg_upload_{uid}_{update.message.document.file_name}")
        await update.message.document.get_file().download(custom_path=file_path)
        files_to_set.append(file_path)
    else:
        await update.message.reply_text("الرجاء إرسال صورة أو ملف صورة.")
        return ConversationHandler.END

    # تعيين الصور بفاصل 5 ثواني
    await set_profile_photos(client, files_to_set, delay=5)

    await update.message.reply_text("تم تحديث الصورة/الصور الشخصية.", reply_markup=main_menu())

    # حذف الملفات المؤقتة
    for f in files_to_set:
        try:
            os.remove(f)
        except:
            pass

    return ConversationHandler.END

# --- Shutdown clients ---
async def shutdown_clients():
    for uid, entry in list(clients.items()):
        try:
            c: TelegramClient = entry.get("client")
            if c:
                try:
                    await c.disconnect()
                except:
                    pass
        except Exception:
            pass
    clients.clear()

# --- Conversation builder ---
def build_conversation():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_query_handler)],
        states={
            AWAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            AWAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)],
            AWAIT_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pass)],
            AWAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            AWAIT_PHOTO: [MessageHandler(filters.ALL, receive_photo)],
        },
        fallbacks=[],
        allow_reentry=True,
    )

# --- Main ---
def main():
    if not BOT_TOKEN or not API_ID or not API_HASH or not OWNER_ID:
        print("تأكد من إعداد BOT_TOKEN و API_ID و API_HASH و OWNER_ID في Config Vars على Heroku")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(build_conversation())
    # CallbackQueryHandler مضاف أيضاً في entry_points لكن إضافته مرة ثانية آمنة
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # احصل على حلقة الأحداث
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # SIGTERM و SIGINT لإغلاق الجلسات عند إيقاف Heroku
    def stop_signal_handler(signum, frame):
        print("إشارة إيقاف، جاري فصل الجلسات...")
        # ابدأ مهمة إغلاق الجلسات ثم أوقف الحلقة
        loop.create_task(shutdown_clients())
        # نترك إيقاف الحلقة بعد السماح للمهام أن تبدأ
        # لا ننفذ loop.stop() فوراً هنا إن كانت هناك مهام مهمة تنتظر الاكتمال،
        # لكن لسهولة العمل نوقف الحلقة بعد فترة قصيرة (يمكن تعديل حسب الحاجة).
        try:
            loop.stop()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, stop_signal_handler)
    signal.signal(signal.SIGINT, stop_signal_handler)

    print("بدء التشغيل. البوت جاهز للعمل.")
    # run_polling internally manages the loop; هنا نستخدمها كما هي
    app.run_polling()

if __name__ == "__main__":
    main()
