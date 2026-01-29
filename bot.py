import logging
import os
import threading
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
)
from flask import Flask

TOKEN = os.environ.get("TOKEN")
OFFSET_FILE = "offset.txt"

SELECT_OBJECTIVE, SELECT_DAY, SELECT_TIME = range(3)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

OBJECTIVES = {
    'bridge': "Bridge / Мост / Puente / Ponte",
    'gate': "Gate / Ворота / Puerta / Portão",
    'city': "City / Город / Ciudad / Cidade"
}

DAYS = {
    0: "Mon / Пн / Lun / Seg",
    1: "Tues / Вт / Mar / Ter",
    2: "Wed / Ср / Mié / Qua",
    3: "Thurs / Чт / Jue / Qui",
    4: "Fri / Пт / Vie / Sex",
    5: "Sat / Сб / Sáb / Sáb",
    6: "Sun / Вс / Dom / Dom"
}

def get_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r") as f:
                return float(f.read().strip())
    except Exception:
        pass
    return -2.0

def save_offset(offset_hours):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset_hours))

def get_game_time():
    offset = get_offset()
    return datetime.utcnow() + timedelta(hours=offset)

def calculate_future_real_time(target_day_idx, target_hour):
    offset = get_offset()
    now_utc = datetime.utcnow()
    now_game = now_utc + timedelta(hours=offset)
    
    target_game_dt = now_game.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    
    current_day_idx = now_game.weekday()
    days_to_add = target_day_idx - current_day_idx
    
    if days_to_add < 0 or (days_to_add == 0 and now_game.hour >= target_hour):
        days_to_add += 7
        
    target_game_dt += timedelta(days=days_to_add)
    
    target_real_dt = target_game_dt - timedelta(hours=offset)
    
    return target_real_dt

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == "private": return True
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ['administrator', 'creator']

async def sync_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return

    try:
        user_input = context.args[0]
        target_time = datetime.strptime(user_input, "%H:%M")
        
        now_utc = datetime.utcnow()
        game_dt = now_utc.replace(hour=target_time.hour, minute=target_time.minute)
        
        diff_seconds = (game_dt - now_utc).total_seconds()
        diff_hours = diff_seconds / 3600.0
        diff_hours = round(diff_hours * 2) / 2
        
        save_offset(diff_hours)
        
        await update.message.reply_text(
            f"✅ **Time Synced!**\n\n"
            f"Real Time (UTC): {now_utc.strftime('%H:%M')}\n"
            f"Game Time: {game_dt.strftime('%H:%M')}\n"
            f"Offset: {diff_hours} hours.\n\n"
            f"Timers will now be accurate."
        )
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/sync HH:MM` (e.g., `/sync 18:30`)")

async def start_timer_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⛔ Only Admins can set timers.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("Bridge / Мост", callback_data='bridge')],
        [InlineKeyboardButton("Gate / Ворота", callback_data='gate')],
        [InlineKeyboardButton("City / Город", callback_data='city')]
    ]
    topic_id = update.message.message_thread_id if update.message.is_topic_message else None
    context.user_data['topic_id'] = topic_id

    await update.message.reply_text(
        "🛠 <b>New Eden Timer</b>\nSelect Objective:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
        message_thread_id=topic_id
    )
    return SELECT_OBJECTIVE

async def objective_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['objective'] = query.data
    
    keyboard = []
    row = []
    for i in range(7):
        day_name = DAYS[i].split(" / ")[0]
        row.append(InlineKeyboardButton(day_name, callback_data=str(i)))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    await query.edit_message_text(
        text=f"Selected: {OBJECTIVES[context.user_data['objective']]}\n\n<b>Select Game Day:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    return SELECT_DAY

async def day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['day_idx'] = int(query.data)
    
    keyboard = []
    row = []
    for h in range(24):
        time_str = f"{h:02}00"
        row.append(InlineKeyboardButton(time_str, callback_data=str(h)))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    
    await query.edit_message_text(
        text=f"Day: {DAYS[context.user_data['day_idx']].split('/')[0]}\n\n<b>Select Game Time:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    return SELECT_TIME

async def time_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    target_hour = int(query.data)
    
    topic_id = context.user_data.get('topic_id')
    day_idx = context.user_data['day_idx']
    obj_key = context.user_data['objective']
    
    target_real_dt = calculate_future_real_time(day_idx, target_hour)
    seconds_until = (target_real_dt - datetime.utcnow()).total_seconds()
    warn_delay = seconds_until - (5 * 60)

    if warn_delay <= 0:
        await query.edit_message_text("⚠️ That time is too close (less than 5 mins).")
        return ConversationHandler.END

    time_str = f"{target_hour:02}00"
    
    announcement = (
        f"<b>New Eden Timer / Таймер Нового Эдема / Temporizador de Nuevo Edén / Temporizador de Novo Éden</b>\n\n"
        f"<b>[{OBJECTIVES[obj_key]}]</b>\n"
        f"<b>[{DAYS[day_idx]}]</b>\n"
        f"<b>[{time_str}]</b> (Game Time)"
    )
    await query.edit_message_text(text=announcement, parse_mode=ParseMode.HTML)
    
    context.job_queue.run_once(
        send_alert, 
        warn_delay, 
        data={
            'chat_id': chat_id, 
            'topic_id': topic_id,
            'obj_text': OBJECTIVES[obj_key],
            'time_str': time_str
        },
        name=str(chat_id)
    )
    return ConversationHandler.END

async def send_alert(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    text = (
        f"<b>Timer starting in 5 minutes / Таймер запускается через 5 минут / "
        f"Temporizador iniciando en 5 minutos / Temporizador iniciando em 5 minutos</b>\n\n"
        f"[{data['obj_text']}]\n"
        f"[{data['time_str']}]"
    )
    msg = await context.bot.send_message(
        chat_id=data['chat_id'],
        message_thread_id=data['topic_id'],
        text=text,
        parse_mode=ParseMode.HTML
    )
    try: await context.bot.pin_chat_message(chat_id=data['chat_id'], message_id=msg.message_id)
    except: pass

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

app = Flask(__name__)
@app.route('/')
def home(): return "Alive"
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("timer", start_timer_flow)],
        states={
            SELECT_OBJECTIVE: [CallbackQueryHandler(objective_selected)],
            SELECT_DAY: [CallbackQueryHandler(day_selected)],
            SELECT_TIME: [CallbackQueryHandler(time_selected)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(CommandHandler("sync", sync_time))
    application.add_handler(conv_handler)
    application.run_polling()
