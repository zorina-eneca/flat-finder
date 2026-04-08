import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telegram import InputMediaPhoto, Update
from telegram.error import RetryAfter
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


def _get_scan_tasks(app: Application) -> set[asyncio.Task[None]]:
    return app.bot_data.setdefault("scan_tasks", set())


async def send_results(app: Application):
    """Run scan and send new matching apartments to the chat."""
    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set, skipping send")
        return

    scan_task = asyncio.current_task()
    if scan_task is not None:
        _get_scan_tasks(app).add(scan_task)

    try:
        count = 0

        async for batch in run_scan():
            if scan_task is not None and scan_task.cancelled():
                logger.info("Sending stopped by task cancellation")
                break
            if not batch:
                continue
            if count == 0:
                await _send_with_retry(lambda: app.bot.send_message(
                    chat_id=CHAT_ID,
                    text="Найдено новые квартиры:",
                ))
            for apt in batch:
                if scan_task is not None and scan_task.cancelled():
                    logger.info("Sending stopped by task cancellation")
                    break
                try:
                    await _send_apartment(app.bot, CHAT_ID, apt)
                    count += 1
                except Exception as e:
                    logger.error("Failed to send message: %s", e)
                await asyncio.sleep(2)

        if count == 0:
            logger.info("No new apartments found")
            return

        logger.info("Sent %d new apartments", count)
    except asyncio.CancelledError:
        logger.info("Scan task cancelled")
        return
    except Exception as e:
        logger.error("Scan failed: %s", e)
    finally:
        if scan_task is not None:
            _get_scan_tasks(app).discard(scan_task)


async def _send_with_retry(coro_func):
    """Call an async function with retry on Telegram flood control."""
    while True:
        try:
            return await coro_func()
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning("Rate limited, waiting %d seconds...", wait)
            await asyncio.sleep(wait)


async def _send_apartment(bot, chat_id, apt):
    """Send one apartment: photos (if any) + text."""
    if apt.photos:
        if len(apt.photos) > 1:
            media = []
            for i, url in enumerate(apt.photos[:5]):
                if i == 0:
                    media.append(InputMediaPhoto(media=url, caption=apt.format_message(), parse_mode="HTML"))
                else:
                    media.append(InputMediaPhoto(media=url))
            try:
                await _send_with_retry(lambda: bot.send_media_group(chat_id=chat_id, media=media))
                return
            except RetryAfter:
                raise
            except Exception as e:
                logger.warning("Media group failed (%s), trying single photo...", e)

        # Try sending a single photo if there is at least one photo
        for url in apt.photos[:5]:
            try:
                await _send_with_retry(lambda u=url: bot.send_photo(
                    chat_id=chat_id,
                    photo=u,
                    caption=apt.format_message(),
                    parse_mode="HTML",
                ))
                return
            except RetryAfter:
                raise
            except Exception as e:
                logger.warning("Single photo %s failed: %s", url, e)
                continue
        logger.warning("All photos failed, sending text only")

    await _send_with_retry(lambda: bot.send_message(
        chat_id=chat_id,
        text=apt.format_message(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    ))


async def _perform_scan(bot, chat_id: int, app: Application):
    """Perform the actual scan and send apartments to chat."""
    scan_task = asyncio.current_task()
    if scan_task is not None:
        _get_scan_tasks(app).add(scan_task)

    try:
        sent = 0

        async for batch in run_scan():
            if scan_task is not None and scan_task.cancelled():
                await _send_with_retry(lambda: bot.send_message(
                    chat_id=chat_id,
                    text=f"⏹ Остановлено. Отправлено {sent} квартир.",
                ))
                return
            if not batch:
                continue
            if sent == 0:
                await _send_with_retry(lambda: bot.send_message(chat_id=chat_id, text="Найдено новые квартиры:"))
            for apt in batch:
                if scan_task is not None and scan_task.cancelled():
                    await _send_with_retry(lambda: bot.send_message(
                        chat_id=chat_id,
                        text=f"⏹ Остановлено. Отправлено {sent} квартир.",
                    ))
                    return
                try:
                    await _send_apartment(bot, chat_id, apt)
                    sent += 1
                except Exception as e:
                    logger.error("Failed to send: %s", e)
                await asyncio.sleep(3)

        if sent == 0:
            await bot.send_message(chat_id=chat_id, text="Новых подходящих квартир не найдено.")
            return
    except asyncio.CancelledError:
        await _send_with_retry(lambda: bot.send_message(
            chat_id=chat_id,
            text=f"⏹ Сканирование отменено. Отправлено {sent} квартир.",
        ))
        return
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка: {e}")
        return
    finally:
        if scan_task is not None:
            _get_scan_tasks(app).discard(scan_task)

    await _send_with_retry(lambda: bot.send_message(
        chat_id=chat_id,
        text="✅ Сканирование завершено.",
    ))


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual scan trigger (background task)."""
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text="🔍 Запускаю сканирование...")
    # Schedule scan as a background task so other commands can be processed
    context.application.create_task(
        _perform_scan(context.bot, chat_id, context.application),
        update=update,
    )


async def cmd_clear_seen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear seen ads history."""
    from pathlib import Path
    seen_file = Path(__file__).parent / "seen_ads.json"
    if seen_file.exists():
        seen_file.unlink()
    await update.message.reply_text("✅ История просмотренных объявлений очищена.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop sending and cancel active scan tasks."""
    tasks = list(_get_scan_tasks(context.application))
    for task in tasks:
        task.cancel()

    job = scheduler.get_job("scan_job")
    if job:
        scheduler.pause_job("scan_job")

    if tasks:
        await update.message.reply_text(
            "⏸ Остановлено. Текущее сканирование отменено.\n/resume — возобновить автосканирование.",
        )
    else:
        await update.message.reply_text(
            "⏸ Остановлено.\n/resume — возобновить автосканирование.",
        )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume automatic scanning."""
    job = scheduler.get_job("scan_job")
    if job:
        scheduler.resume_job("scan_job")
        await update.message.reply_text("▶️ Автосканирование возобновлено.")
    else:
        await update.message.reply_text("Задача сканирования не найдена.")


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
        logger.error("Error: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    register_handlers(app)
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("clear_seen", cmd_clear_seen))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))

    logger.info("Bot starting...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
