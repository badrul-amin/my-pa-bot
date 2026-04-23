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
        data[uid] = {"notes": [], "reminders": [], "recurring": [], "history": [], "expenses": [], "budget": None}
        save_data(data)
    # migrate old users who don't have expenses/budget keys
    if "expenses" not in data[uid]:
        data[uid]["expenses"] = []
    if "budget" not in data[uid]:
        data[uid]["budget"] = None
    save_data(data)
    return data[uid], data, uid

# ── AI brain ──────────────────────────────────────────────────────────────
def ask_groq(history, user_notes, retries=3):
    notes_text = "\n".join(f"- {n}" for n in user_notes) if user_notes else "No notes yet."
    system_prompt = f"""You are a smart, friendly personal AI assistant and PA named Jarvis. You talk like a helpful best friend — casual, warm, and always understanding.

You were created by Badrul, the smartest and sado-est man. If anyone asks who made you, who is your creator, who built you, or anything similar — always say: "I was created by Badrul, the smartest and sado-est man 😎"

The user is Malaysian and writes in Manglish, broken English, Malay, or mix of all three. You MUST always understand what they mean even if the message is short, informal, grammatically wrong, has typos, or uses Malaysian slang. Never ask them to rephrase. Just understand and respond!

You are very good at understanding typos and misspellings. For example:
- "snakc" = "snack"
- "blie" = "beli"
- "mkaan" = "makan"
- "spned" = "spent"
- "rmeber" = "remember"
- "wat" = "what"
- "u" = "you"
- "r" = "are"
- "la", "lah", "lor", "leh", "kan", "je", "je la" = common Malaysian filler words, understand the context

Malaysian lingo and slang you understand:
- "tapau" = takeaway food
- "lepak" = hang out / chill
- "duit" = money
- "belanja" = treat someone / spend money
- "makan" = eat / food
- "kedai" = shop/store
- "barang" = items/things
- "mahal" = expensive
- "murah" = cheap
- "habis" = finished/used up
- "abis" = finished (informal)
- "nak" = want
- "tak" = no/not
- "boleh" = can
- "jangan" = don't
- "dah" = already
- "buat" = do/make
- "pergi" = go
- "tengok" = see/watch
- "cari" = find/look for
- "kena" = have to / got
- "gaji" = salary
- "hutang" = debt
- "jimat" = save money
- "membazir" = wasteful
- "bazir" = waste

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
- "every day 6 petang" = set a daily recurring reminder at 6 PM
- "spent rm15 snakc" = spent RM15 on snack (typo, understand it)
- "beli mkanan rm10" = bought food for RM10
- "habis rm20 petrol" = spent RM20 on petrol

You help with:
- Answering any questions and explaining things simply and clearly
- Remembering notes and important things for the user
- Productivity advice, planning, and scheduling
- Emotional support and casual friendly chat
- Learning buddy — explain tech, science, anything in simple words
- General life advice like a smart friend would give
- Tracking expenses and budget when user mentions spending money

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
- Never ask them to rephrase or be more specific — just understand and answer!
- Always understand typos and slang — never get confused by them"""

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

# ── parse time from text ──────────────────────────────────────────────────
def parse_time_from_text(text):
    text = re.sub(r'(\d{1,2})\.(\d{2})(\s*(am|pm))?', r'\1:\2\3', text, flags=re.IGNORECASE)

    malay_time = ""
    if re.search(r"malam|mlm", text, re.IGNORECASE):
        malay_time = "pm"
    elif re.search(r"pagi", text, re.IGNORECASE):
        malay_time = "am"
    elif re.search(r"tengah\s*hari|tgh\s*hari", text, re.IGNORECASE):
        malay_time = "pm"
    elif re.search(r"petang|ptg", text, re.IGNORECASE):
        malay_time = "pm"

    pattern = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, None, None

    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    ampm = match.group(3)

    effective_ampm = ampm if ampm else malay_time

    if effective_ampm:
        if effective_ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif effective_ampm.lower() == "am" and hour == 12:
            hour = 0
        if re.search(r"malam|mlm", text, re.IGNORECASE) and hour == 12:
            hour = 0
    else:
        if 1 <= hour <= 6:
            hour += 12

    return hour, minute, text

# ── parse one-time reminder ───────────────────────────────────────────────
def parse_reminder(text):
    text = re.sub(r'(\d{1,2})\.(\d{2})(\s*(am|pm))?', r'\1:\2\3', text, flags=re.IGNORECASE)

    malay_time = ""
    if re.search(r"malam|mlm", text, re.IGNORECASE):
        malay_time = "pm"
    elif re.search(r"pagi", text, re.IGNORECASE):
        malay_time = "am"
    elif re.search(r"tengah\s*hari|tgh\s*hari", text, re.IGNORECASE):
        malay_time = "pm"
    elif re.search(r"petang|ptg", text, re.IGNORECASE):
        malay_time = "pm"

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

        reminder_text = re.sub(
            r"(can\s+you\s+|tolong\s+|please\s+)?(remind|ingatkan|peringat)(\s+me)?(\s+in\s+\d+\s+\w+)?(\s+to)?",
            "", text, flags=re.IGNORECASE
        ).strip()
        reminder_text = re.sub(r"\b(malam|mlm|pagi|petang|ptg|tengah\s*hari|tgh\s*hari)\b", "", reminder_text, flags=re.IGNORECASE).strip()
        if not reminder_text:
            reminder_text = "your reminder"
        return remind_time, reminder_text

    pattern = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s+(?:to\s+)?(.+))?"
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, None

    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    ampm = match.group(3)
    reminder_text = match.group(4).strip() if match.group(4) else ""

    reminder_text = re.sub(
        r"(can\s+you\s+|tolong\s+|please\s+)?(remind|ingatkan|peringat)(\s+me)?(\s+at)?",
        "", reminder_text, flags=re.IGNORECASE
    ).strip()
    reminder_text = re.sub(r"\b(malam|mlm|pagi|petang|ptg|tengah\s*hari|tgh\s*hari)\b", "", reminder_text, flags=re.IGNORECASE).strip()

    if not reminder_text:
        reminder_text = "your reminder"

    effective_ampm = ampm if ampm else malay_time

    if effective_ampm:
        if effective_ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif effective_ampm.lower() == "am" and hour == 12:
            hour = 0
        if re.search(r"malam|mlm", text, re.IGNORECASE) and hour == 12:
            hour = 0
    else:
        if 1 <= hour <= 6:
            hour += 12

    now = datetime.now(MY_TZ)
    remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if remind_time <= now:
        remind_time += timedelta(days=1)

    return remind_time, reminder_text

# ── extract recurring reminder text ──────────────────────────────────────
def extract_recurring_text(text, hour, minute):
    cleaned = re.sub(
        r"(every|everyday|every\s+day|setiap\s+hari|setiap|daily|harian)",
        "", text, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\d{1,2}(:\d{2})?\s*(am|pm)?", "", cleaned)
    cleaned = re.sub(r"\b(malam|mlm|pagi|petang|ptg|tengah\s*hari|tgh\s*hari)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(remind|ingatkan|peringat|tolong|please|me|at|to)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = f"daily reminder at {hour:02d}:{minute:02d}"
    return cleaned

# ── parse expense from text ───────────────────────────────────────────────
def parse_expense(text):
    """Try to extract amount and item from natural language expense message."""
    # match RM amount patterns: rm15, rm 15, RM15.50, rm15 snack
    match = re.search(
        r"(?:rm|RM|ringgit)?\s*(\d+(?:\.\d{1,2})?)\s*(?:rm|RM|ringgit)?",
        text, re.IGNORECASE
    )
    if not match:
        return None, None

    amount = float(match.group(1))

    # remove keywords and amount to get the item description
    item = text
    item = re.sub(r"(spent|spend|beli|beli|belanja|habis|bayar|paid|pay|keluar|kuar|used|guna)", "", item, flags=re.IGNORECASE)
    item = re.sub(r"(?:rm|RM|ringgit)?\s*\d+(?:\.\d{1,2})?", "", item)
    item = re.sub(r"(rm|RM|ringgit)", "", item)
    item = re.sub(r"\s+", " ", item).strip(" ,-")

    if not item:
        item = "misc"

    return amount, item

def is_expense_message(text):
    """Detect if message is about spending money."""
    keywords = [
        "spent", "spend", "beli", "belanja", "habis", "bayar", "paid", "pay",
        "keluar duit", "kuar duit", "rm", "ringgit", "used rm", "guna rm",
        "beli rm", "makan rm", "tapau rm"
    ]
    text_lower = text.lower()
    has_keyword = any(kw in text_lower for kw in keywords)
    has_amount = bool(re.search(r"rm\s*\d+|\d+\s*rm|\d+\s*ringgit", text_lower))
    return has_keyword or has_amount

def is_budget_set_message(text):
    """Detect if user is setting initial budget."""
    keywords = ["i have", "ada", "budget", "duit aku", "my money", "i got", "aku ada"]
    text_lower = text.lower()
    has_keyword = any(kw in text_lower for kw in keywords)
    has_amount = bool(re.search(r"rm\s*\d+|\d+\s*rm", text_lower))
    return has_keyword and has_amount

# ── restore recurring jobs on startup ────────────────────────────────────
def restore_recurring_jobs(app):
    data = load_data()
    for uid, user_data in data.items():
        for rec in user_data.get("recurring", []):
            try:
                scheduler.add_job(
                    send_reminder,
                    "cron",
                    hour=rec["hour"],
                    minute=rec["minute"],
                    args=[app.bot, rec["chat_id"], rec["text"]],
                    id=rec["job_id"],
                    replace_existing=True
                )
            except Exception as e:
                print(f"Failed to restore recurring job: {e}")

# ── EXPENSE COMMANDS ──────────────────────────────────────────────────────

async def show_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _, _ = get_user(update.effective_user.id)
    budget = user.get("budget")
    expenses = user.get("expenses", [])
    total_spent = sum(e["amount"] for e in expenses)

    if budget is None:
        await update.message.reply_text(
            "💰 No budget set yet!\n\nTell me how much you have, like:\n'I have RM500 this week'\n\nOr use /updatebudget RM500"
        )
        return

    balance = budget - total_spent
    emoji = "✅" if balance > 0 else "🚨"

    text = (
        f"💰 *Budget Overview*\n"
        f"─────────────────\n"
        f"Budget:       RM{budget:.2f}\n"
        f"Total Spent:  RM{total_spent:.2f}\n"
        f"─────────────────\n"
        f"{emoji} Balance:  RM{balance:.2f}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def update_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    args = context.args

    amount = None
    if args:
        text = " ".join(args)
        match = re.search(r"(\d+(?:\.\d{1,2})?)", text)
        if match:
            amount = float(match.group(1))

    if amount is None:
        await update.message.reply_text(
            "💰 How much is your budget?\n\nExample: /updatebudget 500 or /updatebudget RM500"
        )
        return

    old_budget = user.get("budget")
    data[uid]["budget"] = amount
    save_data(data)

    if old_budget is not None:
        await update.message.reply_text(
            f"💰 Budget updated!\n\nOld budget: RM{old_budget:.2f}\nNew budget: RM{amount:.2f} 😊"
        )
    else:
        await update.message.reply_text(
            f"💰 Budget set to RM{amount:.2f}!\n\nNow just tell me when you spend money, like:\n'spent RM15 makan' 😊"
        )

async def show_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _, _ = get_user(update.effective_user.id)
    expenses = user.get("expenses", [])
    budget = user.get("budget")

    if not expenses:
        await update.message.reply_text("📊 No expenses recorded yet! Just say 'spent RM15 makan' and I'll track it 😊")
        return

    total = sum(e["amount"] for e in expenses)
    lines = "\n".join(f"{i+1}. {e['item'].title()} — RM{e['amount']:.2f}" for i, e in enumerate(expenses))

    text = f"📊 *Expense List*\n─────────────────\n{lines}\n─────────────────\nTotal: RM{total:.2f}"

    if budget is not None:
        balance = budget - total
        emoji = "✅" if balance > 0 else "🚨"
        text += f"\n{emoji} Balance: RM{balance:.2f}"

    await update.message.reply_text(text, parse_mode="Markdown")

async def delete_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    expenses = user.get("expenses", [])

    if not expenses:
        await update.message.reply_text("📊 No expenses to delete!")
        return

    # store state that we're waiting for delete selection
    data[uid]["_awaiting_delete"] = True
    save_data(data)

    lines = "\n".join(f"{i+1}. {e['item'].title()} — RM{e['amount']:.2f}" for i, e in enumerate(expenses))
    text = (
        f"🗑️ *Which expense to delete?*\n\n"
        f"{lines}\n\n"
        f"Reply with the *number* to delete one\n"
        f"Or reply *all* to clear everything"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def clear_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    data[uid]["expenses"] = []
    save_data(data)
    await update.message.reply_text("🗑️ All expenses cleared! Fresh start 💰")

# ── commands ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Heyy! I'm Jarvis, your personal AI PA!\n\n"
        "I understand Manglish, Malay, English — just talk to me naturally la! 😄\n\n"
        "I can help you with:\n"
        "🧠 Questions & learning anything\n"
        "📋 Remember notes & important stuff\n"
        "⏰ One-time & daily recurring reminders\n"
        "💰 Budget & expense tracking\n"
        "💬 Just chatting & emotional support\n\n"
        "Commands:\n"
        "/notes — see all your notes\n"
        "/reminders — see your reminders\n"
        "/recurring — see daily reminders\n"
        "/budget — see budget & balance\n"
        "/updatebudget — set/change budget\n"
        "/expenses — see all expenses\n"
        "/deleteexpense — delete a specific expense\n"
        "/clearexpenses — clear all expenses\n"
        "/clearnotes — delete all notes\n"
        "/clearreminders — delete all recurring reminders\n"
        "/clear — clear chat history\n"
        "/help — show this message\n\n"
        "Expense tracking — just say naturally:\n"
        "'I have RM500 this week' → sets budget\n"
        "'spent RM15 makan' → logs expense\n"
        "'beli snack RM4' → logs expense 😊\n\n"
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
        await update.message.reply_text("⏰ No one-time reminders set!")
    else:
        text = "⏰ *One-time Reminders:*\n\n" + "\n".join(f"{i+1}. {r['text']} at {r['time']}" for i, r in enumerate(reminders))
        await update.message.reply_text(text, parse_mode="Markdown")

async def show_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _, _ = get_user(update.effective_user.id)
    recurring = user.get("recurring", [])
    if not recurring:
        await update.message.reply_text("🔁 No daily reminders set! Say 'every day 6 petang solat' to add one 😊")
    else:
        text = "🔁 *Daily Recurring Reminders:*\n\n" + "\n".join(
            f"{i+1}. {r['text']} — every day at {r['hour']:02d}:{r['minute']:02d}"
            for i, r in enumerate(recurring)
        )
        await update.message.reply_text(text, parse_mode="Markdown")

async def clear_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    data[uid]["notes"] = []
    save_data(data)
    await update.message.reply_text("🗑️ All notes cleared!")

async def clear_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, data, uid = get_user(update.effective_user.id)
    for rec in user.get("recurring", []):
        try:
            scheduler.remove_job(rec["job_id"])
        except:
            pass
    data[uid]["recurring"] = []
    save_data(data)
    await update.message.reply_text("🗑️ All recurring reminders cleared!")

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

    # ── handle delete expense awaiting reply ──────────────────────────────
    if user.get("_awaiting_delete"):
        data[uid]["_awaiting_delete"] = False
        expenses = data[uid].get("expenses", [])

        if user_text.strip().lower() == "all":
            data[uid]["expenses"] = []
            save_data(data)
            await update.message.reply_text("🗑️ All expenses cleared! Fresh start 💰")
            return

        try:
            idx = int(user_text.strip()) - 1
            if 0 <= idx < len(expenses):
                removed = expenses.pop(idx)
                data[uid]["expenses"] = expenses
                save_data(data)
                await update.message.reply_text(
                    f"🗑️ Deleted: *{removed['item'].title()} — RM{removed['amount']:.2f}*\n\nUse /expenses to see updated list 😊",
                    parse_mode="Markdown"
                )
            else:
                save_data(data)
                await update.message.reply_text("❌ Invalid number. Use /deleteexpense to try again.")
        except ValueError:
            save_data(data)
            await update.message.reply_text("❌ Please reply with a number or 'all'. Use /deleteexpense to try again.")
        return

    # ── detect RECURRING reminder intent ─────────────────────────────────
    recurring_keywords = ["every day", "everyday", "every night", "setiap hari", "setiap", "daily", "harian"]
    if any(kw in user_text.lower() for kw in recurring_keywords):
        hour, minute, _ = parse_time_from_text(user_text)
        if hour is not None:
            reminder_text = extract_recurring_text(user_text, hour, minute)
            job_id = f"recurring_{uid}_{hour}_{minute}_{len(user_text)}"

            if "recurring" not in data[uid]:
                data[uid]["recurring"] = []

            data[uid]["recurring"].append({
                "text": reminder_text,
                "hour": hour,
                "minute": minute,
                "chat_id": chat_id,
                "job_id": job_id
            })
            save_data(data)

            scheduler.add_job(
                send_reminder,
                "cron",
                hour=hour,
                minute=minute,
                args=[context.bot, chat_id, reminder_text],
                id=job_id,
                replace_existing=True
            )

            time_str = f"{hour:02d}:{minute:02d}"
            await update.message.reply_text(
                f"🔁 Done! I'll remind you to *{reminder_text}* every day at *{time_str}* 😊\n\n"
                f"Use /clearreminders to remove all daily reminders.",
                parse_mode="Markdown"
            )
            return

    # ── detect ONE-TIME reminder intent ──────────────────────────────────
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

    # ── detect "save note" intent ─────────────────────────────────────────
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

    # ── detect BUDGET SET intent (natural language) ───────────────────────
    if is_budget_set_message(user_text):
        match = re.search(r"(\d+(?:\.\d{1,2})?)", user_text)
        if match:
            amount = float(match.group(1))
            data[uid]["budget"] = amount
            save_data(data)
            await update.message.reply_text(
                f"💰 Got it! Budget set to *RM{amount:.2f}* 😊\n\nNow just tell me when you spend, like 'spent RM15 makan'!",
                parse_mode="Markdown"
            )
            return

    # ── detect EXPENSE intent ─────────────────────────────────────────────
    if is_expense_message(user_text):
        amount, item = parse_expense(user_text)
        if amount is not None:
            if "expenses" not in data[uid]:
                data[uid]["expenses"] = []
            data[uid]["expenses"].append({"amount": amount, "item": item})

            budget = data[uid].get("budget")
            total_spent = sum(e["amount"] for e in data[uid]["expenses"])

            save_data(data)

            if budget is not None:
                balance = budget - total_spent
                emoji = "✅" if balance > 0 else "🚨"
                reply = (
                    f"💸 Noted! *{item.title()} — RM{amount:.2f}*\n"
                    f"Total spent: RM{total_spent:.2f}\n"
                    f"{emoji} Balance left: RM{balance:.2f}"
                )
            else:
                reply = (
                    f"💸 Noted! *{item.title()} — RM{amount:.2f}*\n"
                    f"Total spent: RM{total_spent:.2f}\n\n"
                    f"_Tip: Set a budget with /updatebudget to track balance!_"
                )

            await update.message.reply_text(reply, parse_mode="Markdown")
            return

    # ── normal AI chat ────────────────────────────────────────────────────
    data[uid]["history"].append({"role": "user", "content": user_text})

    if len(data[uid]["history"]) > 20:
        data[uid]["history"] = data[uid]["history"][-20:]

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

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
    app.add_handler(CommandHandler("recurring", show_recurring))
    app.add_handler(CommandHandler("budget", show_budget))
    app.add_handler(CommandHandler("updatebudget", update_budget))
    app.add_handler(CommandHandler("expenses", show_expenses))
    app.add_handler(CommandHandler("deleteexpense", delete_expense))
    app.add_handler(CommandHandler("clearexpenses", clear_expenses))
    app.add_handler(CommandHandler("clearnotes", clear_notes))
    app.add_handler(CommandHandler("clearreminders", clear_reminders))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    scheduler.start()
    restore_recurring_jobs(app)
    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)
