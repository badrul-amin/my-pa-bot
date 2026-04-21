import os
import json
import re
from datetime import datetime
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio

# ── clients ──────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
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
def ask_groq(history, user_notes, retries=3):
    notes_text = "\n".join(f"- {n}" for n in user_notes) if user_notes else "No notes yet."
    system_prompt = f"""You are a smart, friendly personal AI assistant and PA. You talk like a helpful best friend — casual, warm, and always understanding.

The user is Malaysian and may write in Manglish, broken English, Malay, or mix all three. Always understand what they mean even if the message is short or informal. For example:
- "tmr eat chicken" = they want to remember to eat chicken tomorrow
- "remind lunch 1pm" = set a reminder for lunch at 1pm  
- "apa tu async" = explain what async means in simple terms
- "penat la hari ni" = they're tired today, respond with empathy
- "what time i should eat" = give advice on meal timing
- "i boring" = they're bored, suggest something fun or chat

You help with:
- Answering any questions and explaining things simply and clearly
- Remembering notes and important things for the user
- Productivity advice, planning, and scheduling
- Emotional support and casual friendly chat
- Learning buddy — explain tech, science, anything in simple words
- General life advice like a smart friend would give

The user's current notes:
{notes_text}

Today is {datetime.now().strftime("%A, %d %B %Y %I:%M %p")} (Malaysia time).

Important rules:
- ALWAYS reply, never leave the user without a response
- Reply in the same language and style the user uses (Manglish, Malay, or English)
- Keep replies concise and friendly unless they ask for more detail
- Be warm, fun and casual like a real friend — not robotic or formal
- If a message is short or vague, make a smart guess at what they mean and respond helpfully
- If they seem stressed or tired, be empathetic first before giving advice
- Use emojis naturally but don't overdo it
- Never say "As an AI" or "I cannot" — just help them!"""

    for attempt in range(retries):
        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system_prompt}] + history,
                max_tokens=1000,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Groq attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                asyncio.sleep(2)
            else:
                raise e

# ── commands ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Heyy! I'm your personal AI PA!\n\n"
        "I understand Manglish, Malay, English — just talk to me naturally la! 😄\n\n"
        "I can help you with:\n"
        "🧠 Questions & learning anything\n"
        "📋 Remember notes & important stuff\n"
        "⏰ Reminders & planning\n"
        "💬 Just chatting & emotional support\n\n"
        "Commands:\n"
        "/notes — see all your notes\n"
        "/clearnotes — delete all notes\n"
        "/clear — clear chat history\n"
        "/help — show this message\n\n"
        "Just type anything, I got you! 🚀"
    )

async def show_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _, _ = get_user(update.effective_user.id)
    notes = user["notes"]
    if not notes:
        await update.message.reply_text("📋 No notes yet! Just say 'remember...' or 'note...' and I'll save it 😊")
    else:
        text = "📋 *Your Notes:*\n\n" + "\n".join(f"{i+1}. {n}" for i, n in enumerate(notes))
        await update.message.reply_text(text, parse_mode="Markdown")

async def clear_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    data[uid]["notes"] = []
    save_data(data)
    await update.message.reply_text("🗑️ All notes cleared!")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    data[uid]["history"] = []
    save_data(data)
    await update.message.reply_text("🧹 Chat history cleared! Fresh start 😊")

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
        await update.message.reply_text(f"📌 Saved! I'll remember this:\n_{note}_", parse_mode="Markdown")
        return

    # add to history
    data[uid]["history"].append({"role": "user", "content": user_text})

    # keep last 20 messages only
    if len(data[uid]["history"]) > 20:
        data[uid]["history"] = data[uid]["history"][-20:]

    # show typing indicator
    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    # get AI reply with retry + fallback
    try:
        reply = ask_groq(data[uid]["history"], data[uid]["notes"])
    except Exception as e:
        print(f"Groq error: {e}")
        # fallback — always reply something
        reply = "Eh sorry, my brain lagged sikit 😅 Try again in a few seconds!"

    data[uid]["history"].append({"role": "assistant", "content": reply})
    save_data(data)

    await update.message.reply_text(reply)

# ── error handler ─────────────────────────────────────────────────────────
async def error_handler(update, context):
    print(f"Error: {context.error}")
    if update and update.message:
        await update.message.reply_text("Alamak something went wrong 😅 Try again!")

# ── run ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(bot_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("notes", show_notes))
    app.add_handler(CommandHandler("clearnotes", clear_notes))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    scheduler.start()
    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)
