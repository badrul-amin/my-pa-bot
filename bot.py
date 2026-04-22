import os
import json
import re
from datetime import datetime, timedelta
import pytz
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio

# ── timezone ──────────────────────────────────────────────────────────────
MY_TZ = pytz.timezone("Asia/Kuala_Lumpur")

# ── clients ──────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
scheduler = AsyncIOScheduler(timezone="Asia/Kuala_Lumpur")

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
    system_prompt = f"""You are a smart, friendly personal AI assistant and PA named Jarvis. You talk like a helpful best friend — casual, warm, and always understanding.

You were created by Badrul, the smartest and sado-est man. If anyone asks who made you, who is your creator, who built you, or anything similar — always say: "I was created by Badrul, the smartest and sado-est man 😎"

The user is Malaysian and writes in Manglish, broken English, Malay, or mix of all three. You MUST always understand what they mean even if the message is short, informal, or grammatically wrong. Never ask them to rephrase. Just understand and respond!

Examples of how they talk and what they mean:
- "tmr eat chicken" = remind me to eat chicken tomorrow
- "apa tu async" = explain what async means in simple terms
- "penat la hari ni" = they're tired today, respond with empathy
- "what time i should eat" = give advice on meal timing
- "i boring" = they're bored, chat with them or suggest something
- "explain python" = explain Python programming simply
- "can remind me 1pm makan" = set a reminder at 1pm to eat
- "tolong ingatkan 3pm meeting" = set a reminder at 3pm for meeting
- "nak tahu pasal api" = they want to know about APIs
- "mcm mana nak buat website" = how to make a website
- "saya stress" = they're stressed, be empathetic
- "best tak pakai railway" = is Railway good to use?
- "i dont understand la this code" = help them understand the code
- "boleh explain tak" = can you explain
- "macam mana" = how does this work
- "apa beza" = what is the difference
- "12 malam" = 12 midnight / 12 AM
- "8 malam" = 8 PM
- "7 pagi" = 7 AM
- "3 petang" = 3 PM

You help with:
- Answering any questions and explaining things simply and clearly
- Remembering notes and important things for the user
- Productivity advice, planning, and scheduling
- Emotional support and casual friendly chat
- Learning buddy — explain tech, science, anything in simple words
- General life advice like a smart friend would give

The user's current notes:
{notes_text}

Today is {datetime.now(MY_TZ).strftime("%A, %d %B %Y %I:%M %p")} (Malaysia time).

Important rules:
- ALWAYS reply, never leave the user without a response
- Reply in the same language and style the user uses (Manglish, Malay, or English)
- Keep replies concise and friendly unless they ask for more detail
- Be warm, fun and casual like a real friend — not robotic or formal
- If a message is short or vague, make a smart guess and respond helpfully
- If they seem stressed or tired, be empathetic first before giving advice
- Use emojis naturally but don't overdo it
- Never say "As an AI" or "I cannot" — just help them!
- Never ask them to rephrase or be more specific — just understand and answer!"""

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

# ── reminder sender ───────────────────────────────────────────────────────
async def send_reminder(bot, chat_id, text):
    await bot.send_message(chat_id=chat_id, text=f"⏰ Reminder: {text}")

# ── parse reminder from message ───────────────────────────────────────────
def parse_reminder(text):
    # normalize dot to colon e.g. 5.30 -> 5:30, 12.45pm -> 12:45pm
    text = re.sub(r'(\d{1,2})\.(\d{2})(\s*(am|pm))?', r'\1:\2\3', text, flags=re.IGNORECASE)

    # detect Malay time words
    malay_time = ""
    if re.search(r"malam|mlm", text, re.IGNORECASE):
        malay_time = "pm"
    elif re.search(r"pagi", text, re.IGNORECASE):
        malay_time = "am"
    elif re.search(r"tengah\s*hari|tgh\s*hari", text, re.IGNORECASE):
        malay_time = "pm"
    elif re.search(r"petang|ptg", text, re.IGNORECASE):
        malay_time = "pm"

    # match "in X minutes/hours"
    relative_match = re.search(
        r"in\s+(\d+)\s+(minute|minutes|min|hour|hours|jam|minit)",
        text, re.IGNORECASE
    )
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2).lower()
        now = datetime.now(MY_TZ)
        if "hour" in unit or "jam" in unit:
            remind_time = now + timedelta(hours=amount)
        else:
            remind_time = now + timedelta(minutes=amount)

        # extract what to remind about
        reminder_text = re.sub(
            r"(can\s+you\s+|tolong\s+|please\s+)?(remind|ingatkan|peringat)(\s+me)?(\s+in\s+\d+\s+\w+)?(\s+to)?",
            "", text, flags=re.IGNORECASE
        ).strip()
        # clean up Malay time words from reminder text
        reminder_text = re.sub(r"\b(malam|mlm|pagi|petang|ptg|tengah\s*hari|tgh\s*hari)\b", "", reminder_text, flags=re.IGNORECASE).strip()
        if not reminder_text:
            reminder_text = "your reminder"
        return remind_time, reminder_text

    # match specific time e.g. "at 1pm", "1:30pm", "12:45", "8 malam"
    pattern = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s+(?:to\s+)?(.+))?"
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, None

    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    ampm = match.group(3)
    reminder_text = match.group(4).strip() if match.group(4) else ""

    # clean up reminder text
    reminder_text = re.sub(
        r"(can\s+you\s+|tolong\s+|please\s+)?(remind|ingatkan|peringat)(\s+me)?(\s+at)?",
        "", reminder_text, flags=re.IGNORECASE
    ).strip()
    # clean up Malay time words from reminder text
    reminder_text = re.sub(r"\b(malam|mlm|pagi|petang|ptg|tengah\s*hari|tgh\s*hari)\b", "", reminder_text, flags=re.IGNORECASE).strip()

    if not reminder_text:
        reminder_text = "your reminder"

    # determine am/pm — english takes priority, then Malay, then smart guess
    effective_ampm = ampm if ampm else malay_time

    if effective_ampm:
        if effective_ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif effective_ampm.lower() == "am" and hour == 12:
            hour = 0
        # special case: 12 malam = midnight = 0
        if re.search(r"malam|mlm", text, re.IGNORECASE) and hour == 12:
            hour = 0
    else:
        # smart guess — if hour < 7 assume pm
        if 1 <= hour <= 6:
            hour += 12

    now = datetime.now(MY_TZ)
    remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if remind_time <= now:
        remind_time += timedelta(days=1)

    return remind_time, reminder_text

# ── commands ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Heyy! I'm Jarvis, your personal AI PA!\n\n"
        "I understand Manglish, Malay, English — just talk to me naturally la! 😄\n\n"
        "I can help you with:\n"
        "🧠 Questions & learning anything\n"
        "📋 Remember notes & important stuff\n"
        "⏰ Real reminders that ping you!\n"
        "💬 Just chatting & emotional support\n\n"
        "Commands:\n"
        "/notes — see all your notes\n"
        "/reminders — see all your reminders\n"
        "/clearnotes — delete all notes\n"
        "/clear — clear chat history\n"
        "/help — show this message\n\n"
        "For reminders just say:\n"
        "'remind me at 1pm to eat lunch'\n"
        "'remind me in 10 minutes to drink water'\n"
        "'tolong ingatkan 3 petang meeting'\n"
        "'ingatkan 12 malam ambil ubat' 😊\n\n"
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

async def show_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _, _ = get_user(update.effective_user.id)
    reminders = user.get("reminders", [])
    if not reminders:
        await update.message.reply_text("⏰ No reminders set! Say 'remind me at 3pm to call boss' 😊")
    else:
        text = "⏰ *Your Reminders:*\n\n" + "\n".join(f"{i+1}. {r['text']} at {r['time']}" for i, r in enumerate(reminders))
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
    allowed_id = os.environ.get("ALLOWED_USER_ID")
    if allowed_id and str(update.effective_user.id) != allowed_id:
        await update.message.reply_text("Sorry, this is a private bot! 🔒")
        return

    user_text = update.message.text
    user, data, uid = get_user(update.effective_user.id)
    chat_id = update.effective_chat.id

    # detect reminder intent
    remind_keywords = [
        "remind", "peringat", "ingatkan", "tolong ingatkan",
        "can you remind", "boleh remind", "set reminder",
        "buat reminder", "reminder", "jangan lupa"
    ]
    if any(kw in user_text.lower() for kw in remind_keywords):
        remind_time, reminder_text = parse_reminder(user_text)
        if remind_time and reminder_text:
            if "reminders" not in data[uid]:
                data[uid]["reminders"] = []
            data[uid]["reminders"].append({
                "text": reminder_text,
                "time": remind_time.strftime("%I:%M %p")
            })
            save_data(data)

            scheduler.add_job(
                send_reminder,
                "date",
                run_date=remind_time,
                args=[context.bot, chat_id, reminder_text]
            )

            await update.message.reply_text(
                f"⏰ Done! I'll remind you to *{reminder_text}* at *{remind_time.strftime('%I:%M %p')}* 😊",
                parse_mode="Markdown"
            )
            return

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
    app.add_handler(CommandHandler("reminders", show_reminders))
    app.add_handler(CommandHandler("clearnotes", clear_notes))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    scheduler.start()
    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)
