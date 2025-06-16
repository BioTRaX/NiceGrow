# Nombre de archivo: test_detectar_tarea_mail.py
# Ubicación de archivo: tests/test_detectar_tarea_mail.py
# User-provided custom instructions
import asyncio
import importlib
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]

from tests.telegram_stub import (  # Registra las clases fake de telegram
    Document,
    Message,
    Update,
)

# Stubs de openai y jsonschema
openai_stub = ModuleType("openai")


class AsyncOpenAI:
    def __init__(self, api_key=None):
        self.chat = type(
            "c",
            (),
            {"completions": type("comp", (), {"create": lambda *a, **k: None})()},
        )()


openai_stub.AsyncOpenAI = AsyncOpenAI
sys.modules.setdefault("openai", openai_stub)

jsonschema_stub = ModuleType("jsonschema")
jsonschema_stub.validate = lambda *a, **k: None
jsonschema_stub.ValidationError = type("ValidationError", (Exception,), {})
sys.modules.setdefault("jsonschema", jsonschema_stub)

# Variables de entorno necesarias se establecen en la fixture global

# Base de datos en memoria
import sqlalchemy

orig_engine = sqlalchemy.create_engine
sqlalchemy.create_engine = lambda *a, **k: orig_engine("sqlite:///:memory:")
import sandybot.database as bd

sqlalchemy.create_engine = orig_engine
bd.SessionLocal = sessionmaker(bind=bd.engine, expire_on_commit=False)
bd.Base.metadata.create_all(bind=bd.engine)

TEMP_DIR = None


def test_detectar_tarea_mail(tmp_path, monkeypatch):
    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir

    def _tmpdir():
        return str(TEMP_DIR)

    tempfile.gettempdir = _tmpdir

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg

    mod_name = f"{pkg}.detectar_tarea_mail"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "detectar_tarea_mail.py",
    )
    tarea_mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = tarea_mod
    spec.loader.exec_module(tarea_mod)

    # Crear servicio previo
    servicio = bd.crear_servicio(nombre="Srv", cliente="Cli")

    # Contar registros antes de ejecutar
    with bd.SessionLocal() as s:
        prev_tareas = s.query(bd.TareaProgramada).count()
        prev_rels = s.query(bd.TareaServicio).count()

    # Stub GPT para devolver JSON
    import sandybot.email_utils as email_utils

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "2024-01-02T08:00:00", "fin": "2024-01-02T10:00:00", '
                '"tipo": "Mant", "afectacion": "1h", "ids": [' + str(servicio.id) + "]}"
            )

    email_utils.gpt = GPTStub()

    msg = Message("/detectar_tarea Cliente ejemplo correo")
    update = Update(message=msg)
    ctx = SimpleNamespace(args=["Cliente", "ejemplo"])

    asyncio.run(tarea_mod.detectar_tarea_mail(update, ctx))

    with bd.SessionLocal() as s:
        tareas = s.query(bd.TareaProgramada).all()
        rels = s.query(bd.TareaServicio).all()

    tempfile.gettempdir = orig_tmp

    assert len(tareas) == prev_tareas + 1
    assert len(rels) == prev_rels
    tarea = tareas[-1]
    ruta = tmp_path / f"tarea_{tarea.id}.msg"
    assert ruta.exists()
    assert msg.sent == ruta.name


def test_cuerpo_sin_carrier(tmp_path):
    """El texto enviado a procesar_correo_a_tarea no incluye el carrier."""

    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir

    tempfile.gettempdir = lambda: str(TEMP_DIR)

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg

    mod_name = f"{pkg}.detectar_tarea_mail"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "detectar_tarea_mail.py",
    )
    tarea_mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = tarea_mod
    spec.loader.exec_module(tarea_mod)

    import sandybot.email_utils as email_utils

    capturado = {}

    async def stub_procesar(texto, cliente, carrier=None, *, generar_msg=False):
        capturado["texto"] = texto
        ruta = tmp_path / "dummy.msg"
        ruta.write_text("x")
        return (
            SimpleNamespace(id=1, id_interno="ID001"),
            True,
            SimpleNamespace(nombre=cliente),
            ruta,
            "ok",
            [],
        )

    email_utils.procesar_correo_a_tarea = stub_procesar
    tarea_mod.procesar_correo_a_tarea = stub_procesar

    msg = Message("/detectar_tarea Cli Telco cuerpo mail")
    update = Update(message=msg)
    ctx = SimpleNamespace(args=["Cli", "Telco"])

    asyncio.run(tarea_mod.detectar_tarea_mail(update, ctx))

    tempfile.gettempdir = orig_tmp

    assert capturado["texto"] == "cuerpo mail"


def test_detectar_tarea_carrier_en_correo(tmp_path):
    """Obtiene carrier del correo y parsea fechas sin año."""

    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = lambda: str(TEMP_DIR)

    extract_stub = ModuleType("extract_msg")

    class Msg:
        def __init__(self, path):
            self.body = ""
            self.subject = ""

    extract_stub.Message = Msg
    sys.modules["extract_msg"] = extract_stub

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg

    mod_name = f"{pkg}.detectar_tarea_mail"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "detectar_tarea_mail.py",
    )
    tarea_mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = tarea_mod
    spec.loader.exec_module(tarea_mod)

    servicio = bd.crear_servicio(nombre="SrvX", cliente="Cli")

    import sandybot.email_utils as email_utils

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "02/01 08:00", "fin": "02/01 10:00", '
                '"tipo": "Mant", "afectacion": "1h", "ids": [' + str(servicio.id) + "]}"
            )

        async def procesar_json_response(self, resp, esquema):
            import json

            return json.loads(resp)

    email_utils.gpt = GPTStub()

    cuerpo = (
        "Carrier: Telco\nInicio: 02/01 08:00\nFin: 02/01 10:00\n"
        f"Servicios: {servicio.id}"
    )
    doc = Document(file_name="aviso.msg", content=cuerpo)
    msg = Message("/detectar_tarea Cli", document=doc)
    update = Update(message=msg)
    ctx = SimpleNamespace(args=["Cli"])

    asyncio.run(tarea_mod.detectar_tarea_mail(update, ctx))

    with bd.SessionLocal() as s:
        tarea = (
            s.query(bd.TareaProgramada).order_by(bd.TareaProgramada.id.desc()).first()
        )

    tempfile.gettempdir = orig_tmp

    assert tarea is not None
