import asyncio
import json
import logging
import os
import threading

from flask import Flask
from telegram import ChatMember, Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DELETE_AFTER = 90  # 1 min 30 sec
STORE_FILE = "greetings.json"  # persists while service is alive

DEFAULT_WELCOME = (
    "🎉 Welcome to the group, {mention}!\n\n"
    "👤 Name: {full_name}\n"
    "🆔 ID: <code>{user_id}</code>\n"
    "🔗 Username: {username}\n\n"
    "Enjoy your stay! This message self-deletes in 1.5 minutes."
)
DEFAULT_GOODBYE = (
    "👋 Goodbye, {mention} has left the group.\n\n"
    "👤 Name: {full_name}\n"
    "🆔 ID: <code>{user_id}</code>\n"
    "🔗 Username: {username}\n\n"
    "This message self-deletes in 1.5 minutes."
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("welcome-bot")


# ---------- Simple per-chat storage ----------
def load_store():
    try:
        with open(STORE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_store(store):
    with open(STORE_FILE, "w") as f:
        json.dump(store, f)

STORE = load_store()


def format_user(u):
    return {
        "mention": u.mention_html(),
        "full_name": u.full_name,
        "user_id": str(u.id),
        "username": f"@{u.username}" if u.username else "No username",
    }


async def delete_soon(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.job.data["message"].delete()
    except Exception as e:
        log.warning("Delete failed: %s", e)


def schedule_delete(context, msg):
    context.job_queue.run_once(delete_soon, DELETE_AFTER, data={"message": msg})


# ---------- Send the saved (possibly media) greeting ----------
async def send_greeting(chat, msg_ref: dict, text_vars: dict, context):
    text = (msg_ref.get("text") or "").format(**text_vars) if msg_ref.get("text") else None
    chat_id = chat.id
    try:
        if not msg_ref.get("type"):
            sent = await chat.send_message(text, parse_mode=ParseMode.HTML)
        else:
            caption = (msg_ref.get("caption") or "").format(**text_vars) \
                      if msg_ref.get("caption") else None
            kwargs = {"caption": caption, "parse_mode": ParseMode.HTML}
            t = msg_ref["type"]
            if t == "photo":
                sent = await context.bot.send_photo(chat_id, msg_ref["file_id"], **kwargs)
            elif t == "video":
                sent = await context.bot.send_video(chat_id, msg_ref["file_id"], **kwargs)
            elif t == "animation":
                sent = await context.bot.send_animation(chat_id, msg_ref["file_id"], **kwargs)
            elif t == "sticker":
                sent = await context.bot.send_sticker(chat_id, msg_ref["file_id"])
            elif t == "audio":
                sent = await context.bot.send_audio(chat_id, msg_ref["file_id"],
                                                    caption=caption, parse_mode=ParseMode.HTML)
            elif t == "voice":
                sent = await context.bot.send_voice(chat_id, msg_ref["file_id"],
                                                    caption=caption, parse_mode=ParseMode.HTML)
            else:
                sent = await context.bot.send_document(chat_id, msg_ref["file_id"],
                                                       caption=caption, parse_mode=ParseMode.HTML)
        schedule_delete(context, sent)
    except Exception as e:
        log.warning("Greeting send failed: %s", e)


# ---------- Join/leave handler ----------
async def on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = context.chat_member
    old, new = cm.old_chat_member, cm.new_chat_member
    chat_id = str(cm.chat.id)
    conf = STORE.get(chat_id, {})

    was_member = old.status not in (ChatMember.LEFT, ChatMember.BANNED)
    is_member = new.status not in (ChatMember.LEFT, ChatMember.BANNED)

    if not was_member and is_member:
        ref = conf.get("welcome") or {"type": None, "text": DEFAULT_WELCOME}
        await send_greeting(cm.chat, ref, format_user(new), context)
    elif was_member and not is_member:
        ref = conf.get("goodbye") or {"type": None, "text": DEFAULT_GOODBYE}
        await send_greeting(cm.chat, ref, format_user(new), context)


# ---------- Commands ----------
async def set_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    chat_id = str(update.effective_chat.id)
    reply: Message | None = update.message.reply_to_message

    if not reply:
        await update.message.reply_text(
            f"Reply to a message with /{key} to save it.\n"
            "Media (photo/video/GIF/sticker/audio/voice) with a caption works too — "
            "use {mention}, {full_name}, {user_id}, {username} in the caption."
        )
        return

    if reply.photo:
        ref = {"type": "photo", "file_id": reply.photo[-1].file_id}
    elif reply.video:
        ref = {"type": "video", "file_id": reply.video.file_id}
    elif reply.animation:
        ref = {"type": "animation", "file_id": reply.animation.file_id}
    elif reply.sticker:
        ref = {"type": "sticker", "file_id": reply.sticker.file_id}
    elif reply.audio:
        ref = {"type": "audio", "file_id": reply.audio.file_id}
    elif reply.voice:
        ref = {"type": "voice", "file_id": reply.voice.file_id}
    elif reply.document:
        ref = {"type": "document", "file_id": reply.document.file_id}
    else:
        ref = {"type": None}

    if ref["type"]:
        ref["caption"] = reply.caption or ""
    else:
        ref["text"] = reply.text or reply.caption or ""

    STORE.setdefault(chat_id, {})[key] = ref
    save_store(STORE)
    await update.message.reply_text(f"✅ Saved! /{key} is now set.")

async def show_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, default: str):
    chat_id = str(update.effective_chat.id)
    ref = STORE.get(chat_id, {}).get(key) or {"type": None, "text": default}
    await send_greeting(update.effective_chat, ref, format_user(update.effective_user), context)

async def clear_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    chat_id = str(update.effective_chat.id)
    STORE.get(chat_id, {}).pop(key, None)
    save_store(STORE)
    await update.message.reply_text(f"♻️ /{key} reset to default.")


async def cmd_setwelcome(update, context):   await set_greeting(update, context, "welcome")
async def cmd_setgoodbye(update, context):   await set_greeting(update, context, "goodbye")
async def cmd_welcome(update, context):      await show_greeting(update, context, "welcome", DEFAULT_WELCOME)
async def cmd_goodbye(update, context):      await show_greeting(update, context, "goodbye", DEFAULT_GOODBYE)
async def cmd_clearwelcome(update, context): await clear_greeting(update, context, "welcome")
async def cmd_cleargoodbye(update, context): await clear_greeting(update, context, "goodbye")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi! Add me to a group and make me admin.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/setwelcome – reply to a message to set welcome\n"
        "/setgoodbye – reply to a message to set goodbye\n"
        "/welcome – preview welcome\n"
        "/goodbye – preview goodbye\n"
        "/clearwelcome, /cleargoodbye – reset to default\n\n"
        "Placeholders: {mention} {full_name} {user_id} {username}"
    )


# ---------- Flask keep-alive (main thread) ----------
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Bot is alive!", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)),
                  threaded=True, use_reloader=False)


# ---------- Bot polling (background thread, own event loop) ----------
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome))
    app.add_handler(CommandHandler("setgoodbye", cmd_setgoodbye))
    app.add_handler(CommandHandler("welcome", cmd_welcome))
    app.add_handler(CommandHandler("goodbye", cmd_goodbye))
    app.add_handler(CommandHandler("clearwelcome", cmd_clearwelcome))
    app.add_handler(CommandHandler("cleargoodbye", cmd_cleargoodbye))
    app.add_handler(ChatMemberHandler(on_member_update, ChatMemberHandler.CHAT_MEMBER))

    log.info("Bot starting (24/7 on Render free tier)...")

    loop.run_until_complete(app.initialize())
    loop.run_until_complete(app.start())
    loop.run_until_complete(
        app.updater.start_polling(allowed_updates=["message", "chat_member"])
    )
    loop.run_forever()


def main():
    threading.Thread(target=run_bot, daemon=True).start()
    log.info("Health server on port %s", os.environ.get("PORT", 8000))
    run_flask()  # blocks the main thread — keeps Render happy


if __name__ == "__main__":
    main()
