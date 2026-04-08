import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import load_filters, save_filters

logger = logging.getLogger(__name__)


# --- Command handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 <b>Flat Finder Bot</b>\n\n"
        "Я ищу квартиры в аренду в Минске на Kufar, Onliner и Realt.by.\n\n"
        "<b>Команды:</b>\n"
        "/filters — текущие фильтры\n"
        "/set_rooms 1 2 — количество комнат\n"
        "/set_price 200 600 — цена в $ (мин макс, или одно значение)\n"
        "/set_area 30 80 — площадь в м² (мин макс, или одно значение)\n"
        "/set_owner on/off — только собственники\n"
        "/set_interval 30 — интервал сканирования (мин)\n"
        "/scan — запустить сканирование сейчас\n"
        "/clear_seen — очистить историю найденных квартир\n"
        "/stop — остановить автосканирование\n"
        "/resume — возобновить автосканирование\n"
        "/help — эта справка",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    f = load_filters()
    rooms_str = ", ".join(str(r) for r in f.rooms) if f.rooms else "любое"
    owner_str = "да" if f.only_owner else "нет"
    await update.message.reply_text(
        f"⚙️ <b>Текущие фильтры:</b>\n\n"
        f"🛏 Комнаты: {rooms_str}\n"
        f"💰 Цена: от {f.price_min_usd}" + (f" до {f.price_max_usd}" if f.price_max_usd is not None else "") + " $\n"
        f"📐 Площадь: от {f.area_min}" + (f" до {f.area_max}" if f.area_max is not None else "") + " м²\n"
        f"👤 Только собственники: {owner_str}\n"
        f"⏱ Интервал: {f.scan_interval_minutes} мин\n"
        f"\n🚫 Объявления с 'без животных/питомцев' — исключаются автоматически\n"
        f"🍽 Наличие посудомойки — проверяется автоматически",
        parse_mode="HTML",
    )


async def cmd_set_rooms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажите комнаты: /set_rooms 1 2 3")
        return
    try:
        rooms = [int(x) for x in context.args]
    except ValueError:
        await update.message.reply_text("Укажите числа: /set_rooms 1 2 3")
        return
    f = load_filters()
    f.rooms = rooms
    save_filters(f)
    await update.message.reply_text(f"✅ Комнаты: {', '.join(str(r) for r in rooms)}")


async def cmd_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Примеры:\n"
            "/set_price 200 600 — от 200 до 600 $\n"
            "/set_price 200 — от 200 $ (без верхней границы)\n"
            "/set_price 0 600 — до 600 $ (без нижней границы)"
        )
        return
    try:
        values = [int(x) for x in context.args]
    except ValueError:
        await update.message.reply_text("Укажите числа: /set_price 200 600")
        return
    f = load_filters()
    if len(values) == 1:
        f.price_min_usd = values[0]
        f.price_max_usd = None
        await update.message.reply_text(f"✅ Цена: от {values[0]} $")
    else:
        f.price_min_usd = values[0]
        f.price_max_usd = values[1]
        label_min = f"{values[0]}" if values[0] > 0 else "0"
        label_max = f"{values[1]}" if values[1] else "∞"
        await update.message.reply_text(f"✅ Цена: {label_min}–{label_max} $")
    save_filters(f)


async def cmd_set_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Примеры:\n"
            "/set_area 30 80 — от 30 до 80 м²\n"
            "/set_area 40 — от 40 м² (без верхней границы)\n"
            "/set_area 0 60 — до 60 м² (без нижней границы)"
        )
        return
    try:
        values = [int(x) for x in context.args]
    except ValueError:
        await update.message.reply_text("Укажите числа: /set_area 30 80")
        return
    f = load_filters()
    if len(values) == 1:
        f.area_min = values[0]
        f.area_max = None
        await update.message.reply_text(f"✅ Площадь: от {values[0]} м²")
    else:
        f.area_min = values[0]
        f.area_max = values[1]
        label_min = f"{values[0]}" if values[0] > 0 else "0"
        label_max = f"{values[1]}" if values[1] else "∞"
        await update.message.reply_text(f"✅ Площадь: {label_min}–{label_max} м²")
    save_filters(f)


async def cmd_set_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("on", "off", "да", "нет"):
        await update.message.reply_text("Укажите on/off: /set_owner on")
        return
    val = context.args[0].lower() in ("on", "да")
    f = load_filters()
    f.only_owner = val
    save_filters(f)
    status = "включено" if val else "выключено"
    await update.message.reply_text(f"✅ Только собственники: {status}")


async def cmd_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажите минуты: /set_interval 30")
        return
    try:
        minutes = int(context.args[0])
        if minutes < 5:
            await update.message.reply_text("Минимальный интервал — 5 минут")
            return
    except ValueError:
        await update.message.reply_text("Укажите число: /set_interval 30")
        return
    f = load_filters()
    f.scan_interval_minutes = minutes
    save_filters(f)
    await update.message.reply_text(f"✅ Интервал сканирования: {minutes} мин")


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("set_rooms", cmd_set_rooms))
    app.add_handler(CommandHandler("set_price", cmd_set_price))
    app.add_handler(CommandHandler("set_area", cmd_set_area))
    app.add_handler(CommandHandler("set_owner", cmd_set_owner))
    app.add_handler(CommandHandler("set_interval", cmd_set_interval))
