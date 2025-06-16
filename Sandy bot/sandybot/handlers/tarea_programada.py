# Nombre de archivo: tarea_programada.py
# Ubicación de archivo: Sandy bot/sandybot/handlers/tarea_programada.py
# User-provided custom instructions
import os
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..database import (
    Carrier,
    Cliente,
    Servicio,
    SessionLocal,
    crear_tarea_programada,
    obtener_cliente_por_nombre,
)
from ..email_utils import enviar_correo, generar_archivo_msg
from ..registrador import responder_registrando
from ..utils import obtener_mensaje


async def registrar_tarea_programada(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Registra una tarea programada de forma sencilla."""

    mensaje = obtener_mensaje(update)
    if not mensaje:
        return

    user_id = update.effective_user.id
    if len(context.args) < 5:
        await responder_registrando(
            mensaje,
            user_id,
            mensaje.text or "registrar_tarea_programada",
            "Us\u00e1: /registrar_tarea <cliente> <inicio> <fin> <tipo> <id1,id2> [carrier]",
            "tareas",
        )
        return

    cliente_nombre = context.args[0]
    try:
        fecha_inicio = datetime.fromisoformat(context.args[1])
        fecha_fin = datetime.fromisoformat(context.args[2])
    except ValueError:
        await responder_registrando(
            mensaje,
            user_id,
            mensaje.text,
            "Fechas con formato inv\u00e1lido. Us\u00e1 AAAA-MM-DD.",
            "tareas",
        )
        return
    tipo_tarea = context.args[3]
    ids = [int(i) for i in context.args[4].split(",") if i.isdigit()]
    carrier_nombre = context.args[5] if len(context.args) > 5 else None

    with SessionLocal() as session:
        cliente = obtener_cliente_por_nombre(cliente_nombre)
        if not cliente:
            cliente = Cliente(nombre=cliente_nombre)
            session.add(cliente)
            session.commit()
            session.refresh(cliente)
        carrier = None
        if carrier_nombre:
            carrier = (
                session.query(Carrier).filter(Carrier.nombre == carrier_nombre).first()
            )
            if not carrier:
                carrier = Carrier(nombre=carrier_nombre)
                session.add(carrier)
                session.commit()
                session.refresh(carrier)

        tarea, creada_nueva = crear_tarea_programada(
            fecha_inicio,
            fecha_fin,
            tipo_tarea,
            ids,
            carrier_id=carrier.id if carrier else None,
        )
        servicios = [session.get(Servicio, i) for i in ids]
        if carrier:
            for s in servicios:
                if s:
                    s.carrier_id = carrier.id
                    s.carrier = carrier.nombre
            session.commit()

        nombre_arch = f"tarea_{tarea.id}.msg"
        ruta_path = Path(tempfile.gettempdir()) / nombre_arch
        _, cuerpo = generar_archivo_msg(
            tarea,
            cliente,
            [s for s in servicios if s],
            str(ruta_path),
            carrier,
        )

        enviar_correo(
            f"Aviso de tarea programada - {cliente.nombre}",
            cuerpo,
            cliente.id,
            carrier.nombre if carrier else None,
        )

        if ruta_path.exists():
            with open(ruta_path, "rb") as f:
                await mensaje.reply_document(f, filename=nombre_arch)
            os.remove(ruta_path)

    detalle = (
        f"✅ Tarea {tarea.id} registrada."
        if creada_nueva
        else f"🔄 La tarea {tarea.id_interno or 'N/D'} ya existía (ID BD: {tarea.id})."
    )
    if tarea.id_interno:
        detalle += f"\nID Carrier: {tarea.id_interno}"
    await responder_registrando(
        mensaje,
        user_id,
        mensaje.text or "registrar_tarea_programada",
        detalle,
        "tareas",
    )
