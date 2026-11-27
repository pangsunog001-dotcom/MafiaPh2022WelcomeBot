import logging
import os
import threading

from flask import Flask
from telegram import ChatMember, Update
from telegram.ext import (
    Application, CommandHandler, ChatMemberHandler, ContextTypes,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

WELCOME_TEXT = (
    "🎉 Welcome to the group, {mention}!\n\n"
    "👤 Name: {full_name}\n"
    "🆔 ID: `{user_id}`\n"
    "🔗 Username: {username}\n\n"
    "Enjoy your stay! This message self-deletes in 1.5 minutes."
)
GOODBYE_TEXT = (
    "👋 Goodbye, {mention} has left the group.\n\n"
    "👤 Name: {full_name}\n"
    "🆔 ID: `{user_id}`\n"
    "🔗 Username: {username}\n\n"
    "This message self-deletes in 1.5 minutes."
)
DELETE_AFTER = 90  # 1 min 30 sec

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("welcome-bot")


# ---------- Flask keep-alive server ----------
flask_app = Flask(__name__)


@flask_app.route("/")
def health():
    return "Bot is alive!", 200


def run_flask():
    port = int(os.environ.get("PORT", 8000))
    flask_app.run(host="0.0.0.0", port=port)


# ---------- Bot logic ----------
def format_user(u):
    return {
        "mention": u.mention_html(),
        "full_name": u.full_name,
        "user_id": str(u.id),
        "username": f"@{u.username}" if u.username else "No username",
    }


def schedule_delete(context, msg):
    context.job_queue.run_once(delete_soon, DELETE_AFTER, data={"message": msg})


async def delete_soon(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.job.data["message"].delete()
    except Exception as e:
        log.warning("Delete failed: %s", e)


async def on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = context.chat_member
    old, new = cm.old_chat_member, cm.new_chat_member

    was_member = old.status not in (ChatMember.LEFT, ChatMember.BANNED)
    is_member = new.status not in (ChatMember.LEFT, ChatMember.BANNED)

    if not was_member and is_member:
        msg = await cm.chat.send_message(WELCOME_TEXT.format(**format_user(new)), parse_mode="HTML")
        schedule_delete(context, msg)
    elif was_member and not is_member:
        msg = await cm.chat.send_message(GOODBYE_TEXT.format(**format_user(new)), parse_mode="HTML")
        schedule_delete(context, msg)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi! Add me to a group and make me admin.")


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(ChatMemberHandler(on_member_update, ChatMemberHandler.CHAT_MEMBER))

    log.info("Bot starting (24/7 on Render free tier)...")
    app.run_polling(allowed_updates=["message", "chat_member"])


if __name__ == "__main__":
    main()
