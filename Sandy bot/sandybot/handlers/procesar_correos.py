# Nombre de archivo: procesar_correos.py
# Ubicación de archivo: Sandy bot/sandybot/handlers/procesar_correos.py
# User-provided custom instructions
"""Procesamiento masivo de correos .msg para registrar tareas."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..email_utils import enviar_correo, procesar_correo_a_tarea
from ..registrador import responder_registrando
from ..utils import obtener_mensaje

logger = logging.getLogger(__name__)


# ────────────────────────── UTILIDAD LOCAL ──────────────────────────
def _leer_msg(ruta: str) -> str:
    """Devuelve «asunto + cuerpo» del archivo MSG, o '' si falla.

    Se intenta importar ``extract_msg`` en cada llamada para permitir que el
    handler funcione aunque la dependencia sea opcional. Si la librería no está
    instalada, se registra el error y se retorna una cadena vacía.
    """

    msg = None
    try:
        try:
            import extract_msg
        except ModuleNotFoundError as exc:
            logger.error("No se encontró la librería 'extract-msg': %s", exc)
            return ""

        msg = extract_msg.Message(ruta)
        asunto = msg.subject or ""
        remitente = getattr(msg, "sender", None) or getattr(msg, "sender_email", None)
        remitente_nom = getattr(msg, "sender_name", None)

        # 👉 1A) Usamos .body y, si está vacío, htmlBody o rtfBody
        cuerpo = msg.body or getattr(msg, "htmlBody", "") or getattr(msg, "rtfBody", "")

        # Si viene como bytes convertimos a texto para evitar errores
        if isinstance(cuerpo, bytes):
            try:
                cuerpo = cuerpo.decode()
            except Exception:
                cuerpo = cuerpo.decode("latin-1", "ignore")
        if isinstance(asunto, bytes):
            try:
                asunto = asunto.decode()
            except Exception:
                asunto = asunto.decode("latin-1", "ignore")

        # 👉 1B) Convertimos HTML a texto si es necesario
        if "<html" in cuerpo.lower():
            try:
                from bs4 import BeautifulSoup

                cuerpo = BeautifulSoup(cuerpo, "html.parser").get_text("\n")
            except ModuleNotFoundError:
                logger.warning("beautifulsoup4 no instalado; continúo con HTML crudo")
        cuerpo = cuerpo.strip()

        encabezado = []
        if remitente:
            encabezado.append(f"From: {remitente}")
        if remitente_nom:
            encabezado.append(f"Name: {remitente_nom}")
        texto = "\n".join(encabezado + [asunto, cuerpo]).strip()

        if not texto:
            try:
                texto = Path(ruta).read_text(encoding="utf-8", errors="ignore")
            except Exception as err:  # pragma: no cover - error inusual
                logger.error("Error leyendo texto plano de %s: %s", ruta, err)
                texto = ""

        return texto
    except Exception as exc:  # pragma: no cover
        logger.error("Error leyendo MSG %s: %s", ruta, exc)
        return ""
    finally:
        if msg and hasattr(msg, "close"):
            msg.close()


# ────────────────────────── HANDLER PRINCIPAL ───────────────────────
async def procesar_correos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa archivos `.msg` adjuntos y registra las tareas encontradas."""
    mensaje = obtener_mensaje(update)
    if not mensaje:
        return

    user_id = update.effective_user.id

    # Sintaxis: /procesar_correos <cliente> [carrier]
    cliente_nombre = context.args[0] if context.args else "METROTEL"
    carrier_nombre = context.args[1] if len(context.args) > 1 else None

    # Colectar documentos
    docs: list = []
    if getattr(mensaje, "document", None):
        docs.append(mensaje.document)
    docs.extend(getattr(mensaje, "documents", []))
    if not docs:
        return

    first_name = getattr(docs[0], "file_name", "")
    tareas: list[str] = []
    rutas_msg: list[Path] = []

    for doc in docs:
        # Descarga temporal del .msg recibido
        archivo = await doc.get_file()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            await archivo.download_to_drive(tmp.name)
            ruta_tmp = tmp.name

        try:
            contenido = _leer_msg(ruta_tmp)
            if not contenido:
                await responder_registrando(
                    mensaje,
                    user_id,
                    doc.file_name,
                    "Instalá la librería 'extract-msg' para procesar correos .MSG.",
                    "tareas",
                )
                os.remove(ruta_tmp)
                return

            # Procesar correo → registrar tarea → generar .msg final
            (
                tarea,
                _creada_nueva,
                cliente,
                ruta_msg,
                cuerpo,
                _,
            ) = await procesar_correo_a_tarea(
                contenido, cliente_nombre, carrier_nombre, generar_msg=True
            )

        except ValueError as err:  # pragma: no cover
            logger.error("Fallo procesando correo %s: %s", doc.file_name, err)
            await responder_registrando(
                mensaje,
                user_id,
                doc.file_name,
                str(err),
                "tareas",
            )
            os.remove(ruta_tmp)
            continue
        except Exception as e:  # pragma: no cover
            logger.error("Fallo procesando correo %s: %s", doc.file_name, e)
            os.remove(ruta_tmp)
            continue
        finally:
            if os.path.exists(ruta_tmp):
                os.remove(ruta_tmp)

        # Aviso por correo a destinatarios del cliente
        enviar_correo(
            f"Aviso de tarea programada - {cliente.nombre}",
            cuerpo,
            cliente.id,
            carrier_nombre,
        )

        if ruta_msg.exists():
            rutas_msg.append(ruta_msg)

        tareas.append(str(tarea.id))

    # Resumen final
    if tareas:
        await responder_registrando(
            mensaje,
            user_id,
            first_name,
            f"Tareas registradas: {', '.join(tareas)}",
            "tareas",
        )

    if rutas_msg:
        if len(rutas_msg) >= 5:
            zip_path = Path(tempfile.gettempdir()) / "tareas.zip"
            with zipfile.ZipFile(zip_path, "w") as zipf:
                for p in rutas_msg:
                    zipf.write(p, arcname=p.name)
            with open(zip_path, "rb") as f:
                await mensaje.reply_document(f, filename=zip_path.name)
            for p in rutas_msg:
                os.remove(p)
            os.remove(zip_path)
        else:
            for p in rutas_msg:
                with open(p, "rb") as f:
                    await mensaje.reply_document(f, filename=p.name)
                os.remove(p)
