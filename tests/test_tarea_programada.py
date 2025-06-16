# Nombre de archivo: test_tarea_programada.py
# Ubicación de archivo: tests/test_tarea_programada.py
# User-provided custom instructions
import asyncio
import importlib
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy.orm import sessionmaker

# Preparar ruta del paquete
ROOT_DIR = Path(__file__).resolve().parents[1]

from tests.telegram_stub import Message, Update  # Registra las clases fake de telegram

# Stubs de openai y jsonschema para importar gpt_handler sin dependencias
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

# Stub del registrador para evitar llamadas reales a la base
registrador_stub = ModuleType("sandybot.registrador")


async def responder_registrando(*a, **k):
    pass


registrador_stub.responder_registrando = responder_registrando
registrador_stub.registrar_conversacion = lambda *a, **k: None
sys.modules.setdefault("sandybot.registrador", registrador_stub)

# Stub de dotenv
dotenv_stub = ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", dotenv_stub)
# Variables mínimas definidas en la fixture

# Base de datos en memoria
import sqlalchemy

orig_engine = sqlalchemy.create_engine
sqlalchemy.create_engine = lambda *a, **k: orig_engine("sqlite:///:memory:")
import sandybot.database as bd

sqlalchemy.create_engine = orig_engine
bd.SessionLocal = sessionmaker(bind=bd.engine, expire_on_commit=False)
bd.Base.metadata.create_all(bind=bd.engine)

# Parchar directorio temporal para capturar el .msg
TEMP_DIR = None


def _tmpdir():
    return str(TEMP_DIR)


def test_registrar_tarea_programada(tmp_path):
    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = _tmpdir

    # Importar handler de forma aislada para evitar dependencias de otros módulos
    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg
    mod_name = f"{pkg}.tarea_programada"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "tarea_programada.py",
    )
    tarea_mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = tarea_mod
    spec.loader.exec_module(tarea_mod)

    enviados = {}

    def fake_enviar(asunto, cuerpo, cid, carrier=None, **k):
        enviados["asunto"] = asunto
        enviados["cuerpo"] = cuerpo
        enviados["cid"] = cid
        return True

    tarea_mod.enviar_correo = fake_enviar

    # Crear servicio previo
    servicio = bd.crear_servicio(nombre="Srv", cliente="Cli")

    msg = Message("/registrar_tarea")
    update = Update(message=msg)
    ctx = SimpleNamespace(
        args=[
            "Cli",
            "2024-01-02T08:00:00",
            "2024-01-02T10:00:00",
            "Mantenimiento",
            str(servicio.id),
        ]
    )

    with bd.SessionLocal() as s:
        prev_tareas = s.query(bd.TareaProgramada).count()
        prev_rels = s.query(bd.TareaServicio).count()

    asyncio.run(tarea_mod.registrar_tarea_programada(update, ctx))

    with bd.SessionLocal() as s:
        tareas = s.query(bd.TareaProgramada).all()
        rels = s.query(bd.TareaServicio).all()
        cli = s.query(bd.Cliente).filter_by(nombre="Cli").first()

    tempfile.gettempdir = orig_tmp

    assert len(tareas) == prev_tareas + 1
    assert tareas[-1].tipo_tarea == "Mantenimiento"
    assert len(rels) == prev_rels + 1
    assert rels[-1].tarea_id == tareas[-1].id
    assert rels[-1].servicio_id == servicio.id
    ruta = tmp_path / f"tarea_{tareas[-1].id}.msg"
    assert not ruta.exists()
    assert msg.documento == ruta.name
    assert enviados["cid"] == cli.id
    assert "Mantenimiento" in enviados["cuerpo"]


def test_reenviar_aviso(tmp_path):
    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = _tmpdir

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg
    mod_name = f"{pkg}.reenviar_aviso"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "reenviar_aviso.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    enviados = {}

    def fake_enviar(asunto, cuerpo, cid, carrier=None, **k):
        enviados["asunto"] = asunto
        enviados["cuerpo"] = cuerpo
        enviados["cid"] = cid
        return True

    mod.enviar_correo = fake_enviar

    cli = bd.Cliente(nombre="Cli2", destinatarios=["d@x.com"])
    with bd.SessionLocal() as s:
        s.add(cli)
        s.commit()
        s.refresh(cli)

    servicio = bd.crear_servicio(nombre="Srv", cliente="Cli2", cliente_id=cli.id)
    tarea, _ = bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8),
        datetime(2024, 1, 2, 10),
        "Mantenimiento",
        [servicio.id],
    )

    msg = Message("/reenviar_aviso")
    update = Update(message=msg)
    ctx = SimpleNamespace(args=[str(tarea.id)])

    asyncio.run(mod.reenviar_aviso(update, ctx))

    tempfile.gettempdir = orig_tmp

    ruta = tmp_path / f"tarea_{tarea.id}.msg"
    assert not ruta.exists()
    assert msg.documento == ruta.name
    assert enviados["cid"] == cli.id
    assert "Mantenimiento" in enviados["cuerpo"]
