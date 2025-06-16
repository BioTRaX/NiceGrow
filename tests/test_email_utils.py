# Nombre de archivo: test_email_utils.py
# Ubicación de archivo: tests/test_email_utils.py
# User-provided custom instructions
import asyncio
import importlib
import logging
import os
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

# Preparar rutas
ROOT_DIR = Path(__file__).resolve().parents[1]
import tests.telegram_stub  # Registra las clases fake de telegram

# Stub de smtplib para verificar el envío
smtp_stub = types.ModuleType("smtplib")
reg = {"sent": False, "tls": False, "login": None}


class SMTP:
    def __init__(self, host, port):
        reg["host"] = host
        reg["port"] = port

    def starttls(self):
        reg["tls"] = True

    def login(self, user, pwd):
        reg["login"] = (user, pwd)

    def send_message(self, msg):
        reg["sent"] = True
        reg["to"] = msg["To"]
        reg["subject"] = msg["Subject"]

    def quit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass


smtp_stub.SMTP = SMTP
smtp_stub.SMTP_SSL = SMTP
sys.modules["smtplib"] = smtp_stub

# Variables de entorno adicionales para email_utils
os.environ.update(
    {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "25",
        "SMTP_USER": "bot@example.com",
        "SMTP_PASSWORD": "pwd",
        "SMTP_USE_TLS": "true",
    }
)

# Base de datos en memoria para las pruebas
import sqlalchemy

orig = sqlalchemy.create_engine
sqlalchemy.create_engine = lambda *a, **k: orig("sqlite:///:memory:")
import sandybot.database as bd

sqlalchemy.create_engine = orig
bd.SessionLocal = sessionmaker(bind=bd.engine, expire_on_commit=False)
bd.Base.metadata.create_all(bind=bd.engine)

from sandybot import config as config_mod

config_mod.Config._instance = None
importlib.reload(config_mod)
config_mod = importlib.import_module("sandybot.config")
email_utils = importlib.reload(importlib.import_module("sandybot.email_utils"))


def test_enviar_excel_por_correo(tmp_path):
    ruta = tmp_path / "archivo.xlsx"
    ruta.write_text("datos")

    ok = email_utils.enviar_excel_por_correo("dest@example.com", str(ruta))
    assert ok is True
    assert reg["host"] == "smtp.example.com"
    assert reg["port"] == 25
    assert reg["sent"] is True
    assert reg["tls"] is True
    assert reg["login"] == (
        email_utils.config.SMTP_USER,
        email_utils.config.SMTP_PASSWORD,
    )


def test_enviar_excel_por_correo_compatibilidad(tmp_path, monkeypatch):
    """El envío funciona si solo se definen variables EMAIL_*"""
    reg.clear()
    # Eliminar variables SMTP para forzar el uso de EMAIL_*
    for var in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"]:
        os.environ.pop(var, None)
    os.environ.update(
        {
            "EMAIL_HOST": "smtp.legacy.com",
            "EMAIL_PORT": "26",
            "EMAIL_USER": "old@example.com",
            "EMAIL_PASSWORD": "oldpwd",
        }
    )
    import importlib

    config_mod.Config._instance = None
    importlib.reload(config_mod)
    importlib.reload(email_utils)

    ruta = tmp_path / "archivo.xlsx"
    ruta.write_text("datos")
    ok = email_utils.enviar_excel_por_correo("dest@example.com", str(ruta))
    assert ok is True
    assert reg["host"] == "smtp.legacy.com"
    assert reg["port"] == 26
    assert reg["login"] == ("old@example.com", "oldpwd")


def test_generar_nombres(tmp_path):
    email_utils.config.ARCHIVO_CONTADOR = tmp_path / "cont.json"
    nombre1 = email_utils.generar_nombre_camaras(5)
    nombre2 = email_utils.generar_nombre_camaras(5)
    hoy = datetime.now().strftime("%d%m%Y")
    n1 = int(nombre1.rsplit("_", 1)[-1])
    n2 = int(nombre2.rsplit("_", 1)[-1])
    assert nombre1.startswith(f"Camaras_5_{hoy}")
    assert nombre2.startswith(f"Camaras_5_{hoy}")
    assert n2 == n1 + 1


def test_enviar_tracking_reciente_por_correo(tmp_path):
    email_utils.config.HISTORICO_DIR = tmp_path
    (tmp_path / "tracking_7_20240101_000000.txt").write_text("a")
    reciente = tmp_path / "tracking_7_20240102_000000.txt"
    reciente.write_text("b")
    email_utils.config.ARCHIVO_CONTADOR = tmp_path / "cont.json"
    reg.clear()
    ok = email_utils.enviar_tracking_reciente_por_correo("d@x.com", 7)
    assert ok is True
    assert reg["sent"] is True


def test_procesar_correo_fecha_dia_mes(tmp_path):
    """La tarea se registra con fechas en formato dia/mes/año."""

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "02/01/2024 08:00", "fin": "02/01/2024 10:00",'
                ' "tipo": "Mant", "afectacion": null, "descripcion": null, "ids": ["1"]}'
            )

        async def procesar_json_response(self, resp, esquema):
            import json

            return json.loads(resp)

    email_utils.gpt = GPTStub()
    bd.crear_servicio(nombre="Srv1", cliente="Cli", id_servicio=1)
    (
        tarea,
        _creada,
        _,
        ruta,
        _,
        _,
    ) = asyncio.run(
        email_utils.procesar_correo_a_tarea("texto", "Cli", generar_msg=True)
    )
    assert tarea.fecha_inicio == datetime(2024, 1, 2, 8)
    assert tarea.fecha_fin == datetime(2024, 1, 2, 10)
    assert ruta.exists()


def test_enviar_correo_a_cliente(monkeypatch):
    cli = bd.Cliente(nombre="Mail", destinatarios=["m@x.com"])
    with bd.SessionLocal() as s:
        s.add(cli)
        s.commit()
        s.refresh(cli)

    class DebugSMTP:
        def __init__(self, host, port):
            reg["host"] = host
            reg["port"] = port

        def starttls(self):
            reg["tls"] = True

        def login(self, u, p):
            reg["login"] = (u, p)

        def sendmail(self, f, to, msg):
            reg["sent"] = to

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr(email_utils.smtplib, "SMTP", DebugSMTP)
    monkeypatch.setattr(email_utils.smtplib, "SMTP_SSL", DebugSMTP)

    reg.clear()
    ok = email_utils.enviar_correo("Aviso", "Hola", cli.id, host="h", port=25)
    assert ok is True
    assert reg["sent"] == ["m@x.com"]


def test_generar_archivo_msg(tmp_path):
    cli = bd.Cliente(nombre="AcmeWin", destinatarios=["x@y.com"])
    with bd.SessionLocal() as s:
        s.add(cli)
        s.commit()
        s.refresh(cli)

    carrier = bd.Carrier(nombre="Telco")
    with bd.SessionLocal() as s:
        s.add(carrier)
        s.commit()
        s.refresh(carrier)

    srv = bd.crear_servicio(
        nombre="S1", cliente="AcmeWin", cliente_id=cli.id, carrier_id=carrier.id
    )
    tarea, _ = bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8),
        datetime(2024, 1, 2, 10),
        "Mantenimiento",
        [srv.id],
        carrier_id=carrier.id,
    )

    ruta = tmp_path / "aviso.msg"
    resultado_ruta, texto = email_utils.generar_archivo_msg(
        tarea,
        cli,
        [srv],
        str(ruta),
        carrier,
    )
    assert resultado_ruta == str(ruta)
    assert ruta.exists()
    assert "Mantenimiento" in texto
    assert "Telco" in texto


def test_generar_archivo_msg_con_template(tmp_path, monkeypatch):
    plantilla = tmp_path / "Plantilla Correo.MSG"
    plantilla.write_text("Inicio\n{{CONTENIDO}}\nFin", encoding="utf-8")
    monkeypatch.setenv("MSG_TEMPLATE_PATH", str(plantilla))

    import importlib

    from sandybot import config as config_mod

    config_mod.Config._instance = None
    importlib.reload(config_mod)
    email_utils = importlib.reload(importlib.import_module("sandybot.email_utils"))

    cli = bd.Cliente(nombre="AcmeT", destinatarios=["x@y.com"])
    with bd.SessionLocal() as s:
        s.add(cli)
        s.commit()
        s.refresh(cli)

    srv = bd.crear_servicio(nombre="S1", cliente="AcmeT", cliente_id=cli.id)
    tarea, _ = bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8),
        datetime(2024, 1, 2, 10),
        "Mantenimiento",
        [srv.id],
    )

    ruta = tmp_path / "aviso.msg"
    resultado, texto = email_utils.generar_archivo_msg(
        tarea,
        cli,
        [srv],
        str(ruta),
    )

    assert resultado == str(ruta)
    assert "Inicio" in texto and "Fin" in texto


def test_generar_archivo_msg_win32(tmp_path, monkeypatch):
    """Genera el archivo usando Outlook cuando win32 está disponible."""

    import importlib

    from sandybot import config as config_mod

    config_mod.Config._instance = None
    importlib.reload(config_mod)
    email_utils = importlib.reload(importlib.import_module("sandybot.email_utils"))

    class OutlookStub:
        def __init__(self):
            self.saved = None
            self.Body = ""

        def Dispatch(self, name):
            return self

        def CreateItem(self, typ):
            return self

        def SaveAs(self, path, fmt):
            self.saved = (path, fmt)
            Path(path).write_text(self.Body or "")

    class PycomStub:
        def __init__(self):
            self.init = False
            self.uninit = False

        def CoInitialize(self):
            self.init = True

        def CoUninitialize(self):
            self.uninit = True

    outlook = OutlookStub()
    pyc = PycomStub()
    monkeypatch.setattr(email_utils, "win32", outlook)
    monkeypatch.setattr(email_utils, "pythoncom", pyc)

    cli = bd.Cliente(nombre="AcmeWin2", destinatarios=["x@y.com"])
    with bd.SessionLocal() as s:
        s.add(cli)
        s.commit()
        s.refresh(cli)

    carrier = bd.Carrier(nombre="Telco2")
    with bd.SessionLocal() as s:
        s.add(carrier)
        s.commit()
        s.refresh(carrier)

    srv = bd.crear_servicio(
        nombre="S1", cliente="AcmeWin2", cliente_id=cli.id, carrier_id=carrier.id
    )
    tarea, _ = bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8),
        datetime(2024, 1, 2, 10),
        "Mantenimiento",
        [srv.id],
        carrier_id=carrier.id,
    )

    ruta = tmp_path / "aviso.msg"
    resultado, texto = email_utils.generar_archivo_msg(
        tarea,
        cli,
        [srv],
        str(ruta),
        carrier,
    )
    assert resultado == str(ruta)
    assert outlook.saved == (str(ruta), 3)
    assert ruta.exists()
    assert "Telco2" in texto
    assert not Path(str(ruta) + ".txt").exists()


def test_generar_archivo_msg_win32_template(tmp_path, monkeypatch):
    plantilla = tmp_path / "Plantilla Correo.MSG"
    plantilla.write_text("Head\n{{CONTENIDO}}\nTail", encoding="utf-8")
    monkeypatch.setenv("MSG_TEMPLATE_PATH", str(plantilla))

    import importlib

    from sandybot import config as config_mod

    config_mod.Config._instance = None
    importlib.reload(config_mod)
    email_utils = importlib.reload(importlib.import_module("sandybot.email_utils"))

    class OutlookStub:
        def __init__(self):
            self.saved = None
            self.template = None
            self.Body = ""

        def Dispatch(self, name):
            return self

        def CreateItem(self, typ):
            return self

        def CreateItemFromTemplate(self, path):
            self.template = path
            self.Body = Path(path).read_text()
            return self

        def SaveAs(self, path, fmt):
            self.saved = (path, fmt)
            Path(path).write_text(self.Body or "")

    class PycomStub:
        def CoInitialize(self):
            pass

        def CoUninitialize(self):
            pass

    outlook = OutlookStub()
    pyc = PycomStub()
    monkeypatch.setattr(email_utils, "win32", outlook)
    monkeypatch.setattr(email_utils, "pythoncom", pyc)

    cli = bd.Cliente(nombre="AcmePlant", destinatarios=["x@y.com"])
    with bd.SessionLocal() as s:
        s.add(cli)
        s.commit()
        s.refresh(cli)

    srv = bd.crear_servicio(nombre="S1", cliente="AcmePlant", cliente_id=cli.id)
    tarea, _ = bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8),
        datetime(2024, 1, 2, 10),
        "Mant",
        [srv.id],
    )

    ruta = tmp_path / "aviso.msg"
    resultado, texto = email_utils.generar_archivo_msg(
        tarea,
        cli,
        [srv],
        str(ruta),
    )

    assert resultado == str(ruta)
    assert outlook.saved == (str(ruta), 3)
    assert outlook.template == str(plantilla)
    assert "Head" in texto and "Tail" in texto


def test_procesar_correo_sin_servicios(monkeypatch, caplog):
    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "2024-01-02T08:00:00", "fin": "2024-01-02T10:00:00", '
                '"tipo": "Mant", "afectacion": null, "descripcion": null, '
                '"ids": ["99999"]}'
            )

        async def procesar_json_response(self, resp, schema):
            import json

            return json.loads(resp)

    email_utils.gpt = GPTStub()

    tarea, _creada, ids_pend = asyncio.run(
        email_utils.procesar_correo_a_tarea("correo", "Cli", generar_msg=False)
    )
    with bd.SessionLocal() as s:
        pendiente = s.query(bd.ServicioPendiente).filter_by(tarea_id=tarea.id).first()
    assert pendiente is None
    assert ids_pend == []


def test_procesar_correo_respuesta_con_texto(monkeypatch):
    """Extrae JSON aunque venga acompañado de texto."""

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                "Aqui tienes:\n"
                '{"inicio": "2024-01-02 08:00", "fin": "2024-01-02 10:00", '
                '"tipo": "Mant", "afectacion": null, "descripcion": null, "ids": ["1"]}'
            )

        async def procesar_json_response(self, resp, esquema):
            import json

            return json.loads(resp)

    email_utils.gpt = GPTStub()
    bd.crear_servicio(nombre="SrvX", cliente="Cli", id_servicio=1)

    (
        tarea,
        _creada,
        _,
        _,
        _,
        _,
    ) = asyncio.run(
        email_utils.procesar_correo_a_tarea("texto", "Cli", generar_msg=True)
    )
    assert tarea.fecha_inicio == datetime(2024, 1, 2, 8)


def test_procesar_correo_id_con_prefijo(monkeypatch):
    """Reconoce IDs con guiones o prefijos."""

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "2024-01-02T08:00:00", "fin": "2024-01-02T10:00:00", '
                '"tipo": "Mant", "afectacion": null, "descripcion": null, '
                '"ids": ["CRT-1"]}'
            )

        async def procesar_json_response(self, resp, esquema):
            import json

            return json.loads(resp)

    email_utils.gpt = GPTStub()
    bd.crear_servicio(nombre="SrvPref", cliente="Cli", id_carrier="1")

    (
        tarea,
        _creada,
        _,
        _,
        _,
        _,
    ) = asyncio.run(
        email_utils.procesar_correo_a_tarea("texto", "Cli", generar_msg=True)
    )
    assert tarea.fecha_inicio == datetime(2024, 1, 2, 8)


def test_detectar_carrier_por_remitente():
    assert email_utils.detectar_carrier_por_remitente("noc@telxius.com") == "TELXIUS"
    assert email_utils.detectar_carrier_por_remitente("otro@ejemplo.com") is None
