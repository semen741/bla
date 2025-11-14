from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import httpx
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update,
                      WebAppInfo)
from telegram.constants import ChatAction
from telegram.ext import (Application, ApplicationBuilder, CommandHandler,
                          ContextTypes, MessageHandler, filters)

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://127.0.0.1:8000/webapp/")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Отправьте видео до 100 МБ. После загрузки появится кнопка для редактирования."
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    attachment = message.effective_attachment
    if not attachment:
        await message.reply_text("Не удалось прочитать файл")
        return
    file_size = getattr(attachment, "file_size", 0)
    if file_size and file_size > MAX_FILE_SIZE_BYTES:
        await message.reply_text("Файл больше 100 МБ, выберите другое видео")
        return

    file_id = getattr(attachment, "file_id", None)
    if not file_id:
        await message.reply_text("Не найден file_id")
        return

    context.user_data["latest_file_id"] = file_id
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="Открыть редактор", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await message.reply_text(
        "Видео принято. Настройте отрезок и нажмите \"Сделать кружок\".",
        reply_markup=keyboard,
    )


async def handle_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.web_app_data:
        return
    file_id = context.user_data.get("latest_file_id")
    if not file_id:
        await update.message.reply_text("Сначала отправьте видео")
        return

    try:
        data = json.loads(update.message.web_app_data.data)
    except json.JSONDecodeError:
        await update.message.reply_text("Не удалось разобрать данные веб-приложения")
        return

    payload = {
        "telegram_file_id": file_id,
        "start": float(data.get("start", 0)),
        "end": float(data.get("end", 0)),
        "mute": bool(data.get("mute", False)),
        "audio_only": bool(data.get("audioOnly", False)),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(f"{API_BASE_URL}/jobs", json=payload, timeout=10)
    if response.status_code != 200:
        await update.message.reply_text("Не удалось создать задачу")
        return

    job: Dict[str, Any] = response.json()
    context.user_data["last_job_id"] = job["job_id"]
    status_message = await update.message.reply_text("Принято")
    asyncio.create_task(
        poll_job(
            chat_id=update.effective_chat.id,
            message_id=status_message.message_id,
            job_id=job["job_id"],
            application=context.application,
        )
    )


async def poll_job(*, chat_id: int, message_id: int, job_id: str, application: Application) -> None:
    last_stage: Optional[str] = None
    last_position: Optional[int] = None
    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(f"{API_BASE_URL}/jobs/{job_id}", timeout=10)
            if response.status_code != 200:
                await application.bot.send_message(chat_id, "Задача потеряна")
                return
            job = response.json()
            stage = job["stage"]
            position = job.get("position")
            text = stage_to_text(stage, position)

            if stage != last_stage or position != last_position:
                try:
                    await application.bot.edit_message_text(text, chat_id, message_id)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Failed to edit message: %s", exc)
                last_stage = stage
                last_position = position

            if stage in {"done", "failed"}:
                if stage == "done":
                    result_id = job.get("result_file_id")
                    if result_id:
                        try:
                            await application.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO_NOTE)
                            await application.bot.send_video_note(chat_id=chat_id, video_note=result_id)
                        except Exception as exc:  # pragma: no cover - network call
                            await application.bot.send_message(
                                chat_id,
                                f"Готово. Файл: {result_id}. (Не удалось отправить как video note: {exc})",
                            )
                    else:
                        await application.bot.send_message(chat_id, "Готово")
                else:
                    await application.bot.send_message(chat_id, f"Ошибка: {job.get('detail', 'неизвестно')}")
                return
            await asyncio.sleep(2)


def stage_to_text(stage: str, position: Optional[int]) -> str:
    if stage == "accepted":
        return "Принято"
    if stage == "queued":
        pos = position or 1
        return f"В очереди {pos}"
    if stage == "processing":
        return "Обработка"
    if stage == "done":
        return "Готово"
    if stage == "failed":
        return "Ошибка"
    return stage


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.VIDEO_NOTE, handle_video))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))

    logger.info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()
