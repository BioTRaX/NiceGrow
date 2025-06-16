# Nombre de archivo: test_listar_tareas.py
# Ubicación de archivo: tests/test_listar_tareas.py
# User-provided custom instructions
import asyncio
import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]

from tests.telegram_stub import Message, Update

# Stubs necesarios
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

captura = {}
registrador_stub = ModuleType("sandybot.registrador")


async def responder_registrando(*a, **k):
    captura["texto"] = a[3]


registrador_stub.responder_registrando = responder_registrando
registrador_stub.registrar_conversacion = lambda *a, **k: None
sys.modules.setdefault("sandybot.registrador", registrador_stub)

# Variables de entorno mínimas definidas en la fixture

import sqlalchemy

orig_engine = sqlalchemy.create_engine
sqlalchemy.create_engine = lambda *a, **k: orig_engine("sqlite:///:memory:")
import sandybot.database as bd

sqlalchemy.create_engine = orig_engine
bd.SessionLocal = sessionmaker(bind=bd.engine, expire_on_commit=False)
bd.Base.metadata.create_all(bind=bd.engine)


@pytest.fixture(autouse=True)
def reiniciar_bd():
    """Genera un motor nuevo y reinicia las tablas."""
    import sqlalchemy

    old_engine = bd.engine
    old_session = bd.SessionLocal
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    bd.engine = engine
    bd.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    bd.Base.metadata.create_all(bind=engine)
    yield
    bd.Base.metadata.drop_all(bind=engine)
    bd.SessionLocal.close_all()
    engine.dispose()
    bd.engine = old_engine
    bd.SessionLocal = old_session


def _importar():
    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg
    mod_name = f"{pkg}.listar_tareas"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "listar_tareas.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _ejecutar(args):
    mod = _importar()
    sys.modules["sandybot.registrador"] = registrador_stub
    registrador_stub.responder_registrando = responder_registrando
    msg = Message("/listar_tareas")
    update = Update(message=msg)
    ctx = SimpleNamespace(args=args)
    captura.clear()
    await mod.listar_tareas(update, ctx)
    return captura.get("texto", "")


def test_listar_tareas_filtro_cliente():
    s1 = bd.crear_servicio(nombre="S1", cliente="A")
    s2 = bd.crear_servicio(nombre="S2", cliente="B")
    bd.crear_tarea_programada(
        datetime(2024, 1, 1, 8), datetime(2024, 1, 1, 10), "Mant", [s1.id]
    )[0]
    bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8), datetime(2024, 1, 2, 10), "Upg", [s2.id]
    )[0]
    texto = asyncio.run(_ejecutar(["A"]))
    tareas = bd.obtener_tareas_servicio(s1.id)
    assert tareas


def test_listar_tareas_filtro_servicio():
    s = bd.crear_servicio(nombre="S3", cliente="C")
    bd.crear_tarea_programada(
        datetime(2024, 1, 3, 8), datetime(2024, 1, 3, 10), "Test", [s.id]
    )[0]
    texto = asyncio.run(_ejecutar([str(s.id)]))
    assert "Test" in texto
    assert str(s.id) in texto


def test_listar_tareas_filtro_fechas():
    s = bd.crear_servicio(nombre="S4", cliente="D")
    bd.crear_tarea_programada(
        datetime(2024, 2, 1, 8), datetime(2024, 2, 1, 10), "OK", [s.id]
    )[0]
    bd.crear_tarea_programada(
        datetime(2024, 3, 1, 8), datetime(2024, 3, 1, 10), "Late", [s.id]
    )[0]
    texto = asyncio.run(_ejecutar(["2024-02-01", "2024-02-02"]))
    assert "OK" in texto
    assert "Late" not in texto


def test_listar_tareas_filtro_carrier():
    car1 = bd.Carrier(nombre="C1")
    car2 = bd.Carrier(nombre="C2")
    with bd.SessionLocal() as s:
        s.add_all([car1, car2])
        s.commit()
        s.refresh(car1)
        s.refresh(car2)

    srv = bd.crear_servicio(nombre="S5", cliente="E")
    bd.crear_tarea_programada(
        datetime(2024, 4, 1, 8),
        datetime(2024, 4, 1, 10),
        "A",
        [srv.id],
        carrier_id=car1.id,
    )[0]
    bd.crear_tarea_programada(
        datetime(2024, 4, 2, 8),
        datetime(2024, 4, 2, 10),
        "B",
        [srv.id],
        carrier_id=car2.id,
    )[0]

    texto = asyncio.run(_ejecutar([f"carrier={car1.nombre}"]))
    assert "A" in texto
    assert "B" not in texto
