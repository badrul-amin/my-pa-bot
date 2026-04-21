import os
import json
import re
from datetime import datetime
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── clients ──────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ["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"])
scheduler = AsyncIOScheduler()

# ── simple file-based storage ─────────────────────────────────────────────
DATA_FILE = "data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user(user_id):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"notes": [], "reminders": [], "history": []}
        save_data(data)
    return data[uid], data, uid

# ── AI brain ──────────────────────────────────────────────────────────────
def ask_groq(history, user_notes):
    notes_text = "\n".join(f"- {n}" for n in user_notes) if user_notes else "No notes yet."
    system_prompt = f"""You are a friendly personal AI assistant PA. You help with:
- Answering questions and explaining things simply
- Keeping notes and reminders
- Giving productivity advice
- General chat and support

The user's current notes:
{notes_text}

Today is {datetime.now().strftime("%A, %d %B %Y %I:%M %p")}.
Be friendly, concise, and helpful. If the user asks you to save a note or set a reminder, 
confirm you've done it. Respond in the same language the user uses (Malay or English)."""

    response = groq_client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": system_prompt}] + history,
        max_tokens=1000
    )
    return response.choices[0].message.content

# ── commands ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm your personal AI PA!\n\n"
        "I can help you with:\n"
        "🧠 Learning & questions\n"
        "📋 Taking notes\n"
        "⏰ Reminders\n"
        "💬 Just chatting!\n\n"
        "Commands:\n"
        "/notes — see all your notes\n"
        "/clear — clear chat history\n"
        "/help — show this message\n\n"
        "Just type anything to get started!"
    )

async def show_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _, _ = get_user(update.effective_user.id)
    notes = user["notes"]
    if not notes:
        await update.message.reply_text("📋 You have no notes yet! Just tell me to remember something.")
    else:
        text = "📋 *Your Notes:*\n\n" + "\n".join(f"{i+1}. {n}" for i, n in enumerate(notes))
        await update.message.reply_text(text, parse_mode="Markdown")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    data[uid]["history"] = []
    save_data(data)
    await update.message.reply_text("🧹 Chat history cleared! Fresh start.")

# ── main message handler ──────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user, data, uid = get_user(update.effective_user.id)

    # detect "save note" intent
    note_match = re.search(
        r"(remember|note|save|catat|ingat|simpan)[:\s]+(.+)",
        user_text, re.IGNORECASE
    )
    if note_match:
        note = note_match.group(2).strip()
        data[uid]["notes"].append(note)
        save_data(data)
        await update.message.reply_text(f"📌 Got it! I've saved this note:\n_{note}_", parse_mode="Markdown")
        return

    # add to history
    data[uid]["history"].append({"role": "user", "content": user_text})

    # keep last 20 messages only
    if len(data[uid]["history"]) > 20:
        data[uid]["history"] = data[uid]["history"][-20:]

    # get AI reply
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    reply = ask_groq(data[uid]["history"], data[uid]["notes"])

    data[uid]["history"].append({"role": "assistant", "content": reply})
    save_data(data)

    await update.message.reply_text(reply)

# ── run ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot_token = os.environ["8792774110:AAEdivSyvW2i5sUCJ3KSx4WbN0oUeX_DSic"]
    app = ApplicationBuilder().token(bot_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("notes", show_notes))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler.start()
    print("Bot is running...")
    app.run_polling()
