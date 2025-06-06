"""Handler para mensajes de voz."""
import logging
import tempfile
import openai
from telegram import Update
from telegram.ext import ContextTypes
from ..registrador import responder_registrando
from .message import message_handler

logger = logging.getLogger(__name__)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarga el audio, lo transcribe y pasa el texto a ``message_handler``."""
    mensaje = update.message
    if not mensaje or not mensaje.voice:
        return

    voice = await mensaje.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
        await voice.download_to_drive(tmp.name)
        try:
            with open(tmp.name, "rb") as audio:
                transcripcion = await openai.Audio.transcriptions.create(
                    file=audio,
                    model="whisper-1"
                )
            texto = transcripcion.text.strip()
        except Exception as e:
            logger.error("Error al transcribir audio: %s", e)
            await responder_registrando(
                mensaje,
                mensaje.from_user.id,
                "[voice]",
                "No pude transcribir el audio. Reintentá más tarde.",
                "voz",
            )
            return

    # Reutilizar el manejador de mensajes como si el usuario hubiera escrito
    mensaje.text = texto
    await message_handler(update, context)
