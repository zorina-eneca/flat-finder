import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telegram import InputMediaPhoto, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import register_handlers
from config import load_filters
from scanner import run_scan

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

scheduler = AsyncIOScheduler()


async def send_results(app: Application):
    """Run scan and send new matching apartments to the chat."""
    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set, skipping send")
        return

    try:
        apartments = await run_scan()
    except Exception as e:
        logger.error("Scan failed: %s", e)
        return

    if not apartments:
        logger.info("No new apartments found")
        return

    logger.info("Sending %d new apartments", len(apartments))

    for apt in apartments:
        try:
            await _send_apartment(app.bot, CHAT_ID, apt)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error("Failed to send message: %s", e)


async def _send_apartment(bot, chat_id, apt):
    """Send one apartment: photos (if any) + text."""
    if apt.photos:
        media = []
        for i, url in enumerate(apt.photos[:5]):
            if i == 0:
                media.append(InputMediaPhoto(media=url, caption=apt.format_message(), parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(media=url))
        try:
            await bot.send_media_group(chat_id=chat_id, media=media)
            return
        except Exception:
            # Fallback to text if photos fail
            pass

    await bot.send_message(
        chat_id=chat_id,
        text=apt.format_message(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual scan trigger."""
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text="🔍 Запускаю сканирование...")
    try:
        apartments = await run_scan()
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка: {e}")
        return

    if not apartments:
        await context.bot.send_message(chat_id=chat_id, text="Новых подходящих квартир не найдено.")
        return

    await context.bot.send_message(chat_id=chat_id, text=f"Найдено {len(apartments)} квартир:")
    for apt in apartments:
        try:
            await _send_apartment(context.bot, chat_id, apt)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error("Failed to send: %s", e)

    await context.bot.send_message(chat_id=chat_id, text="✅ Сканирование завершено.")


async def cmd_clear_seen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear seen ads history."""
    from pathlib import Path
    seen_file = Path(__file__).parent / "seen_ads.json"
    if seen_file.exists():
        seen_file.unlink()
    await update.message.reply_text("✅ История просмотренных объявлений очищена.")


async def post_init(app: Application):
    """Called after bot initialization."""
    filters = load_filters()
    interval = filters.scan_interval_minutes

    scheduler.add_job(
        send_results,
        "interval",
        minutes=interval,
        args=[app],
        id="scan_job",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started, scanning every %d minutes", interval)


def main():
    if not BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    register_handlers(app)
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("clear_seen", cmd_clear_seen))

    logger.info("Bot starting...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
