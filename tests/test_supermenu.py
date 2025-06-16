# Nombre de archivo: test_supermenu.py
# Ubicación de archivo: tests/test_supermenu.py
# User-provided custom instructions
import asyncio
import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]

import sqlalchemy

from tests.telegram_stub import Message, Update

# Variables mínimas configuradas en la fixture global

orig_engine = sqlalchemy.create_engine
sqlalchemy.create_engine = lambda *a, **k: orig_engine("sqlite:///:memory:")
import sandybot.database as bd

sqlalchemy.create_engine = orig_engine
bd.SessionLocal = sessionmaker(bind=bd.engine, expire_on_commit=False)
bd.Base.metadata.create_all(bind=bd.engine)

captura = {}
registrador_stub = ModuleType("sandybot.registrador")


async def responder_registrando(*a, **k):
    captura["texto"] = a[3]
    captura["markup"] = k.get("reply_markup")


registrador_stub.responder_registrando = responder_registrando
registrador_stub.registrar_conversacion = lambda *a, **k: None
sys.modules["sandybot.registrador"] = registrador_stub


def _importar():
    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg
    mod_name = f"{pkg}.supermenu"
    sys.modules["sandybot.registrador"] = registrador_stub
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "supermenu.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _run(func, args):
    mod = _importar()
    msg = Message(f"/{func}")
    update = Update(message=msg)
    ctx = SimpleNamespace(args=args)
    captura.clear()
    await getattr(mod, func)(update, ctx)
    return captura


def test_supermenu_teclado():
    res = asyncio.run(_run("supermenu", ["Bio123"]))
    assert res["markup"].keyboard[0] == [
        "/CDB_Servicios",
        "/CDB_Reclamos",
        "/CDB_Camaras",
        "/Depurar_Duplicados",
        "/CDB_Clientes",
        "/CDB_Carriers",
        "/CDB_Conversaciones",
        "/CDB_Ingresos",
        "/CDB_Tareas",
        "/CDB_TareasServicio",
    ]


def test_listar_descendente():
    bd.Base.metadata.drop_all(bind=bd.engine)
    bd.Base.metadata.create_all(bind=bd.engine)
    s1 = bd.crear_servicio(nombre="S1", cliente="A")
    s2 = bd.crear_servicio(nombre="S2", cliente="B")
    bd.crear_reclamo(s1.id, "R1")
    bd.crear_reclamo(s2.id, "R2")
    bd.crear_camara("C1", s1.id)
    bd.crear_camara("C2", s2.id)
    texto_serv = asyncio.run(_run("listar_servicios", []))["texto"]
    assert (
        texto_serv.splitlines()[1].startswith("1. ")
        and str(s2.id) in texto_serv.splitlines()[1]
    )
    texto_rec = asyncio.run(_run("listar_reclamos", []))["texto"]
    assert (
        texto_rec.splitlines()[1].startswith("1. ")
        and "R2" in texto_rec.splitlines()[1]
    )
    texto_cam = asyncio.run(_run("listar_camaras", []))["texto"]
    assert (
        texto_cam.splitlines()[1].startswith("1. ")
        and "C2" in texto_cam.splitlines()[1]
    )

    # Datos extra para las nuevas tablas
    with bd.SessionLocal() as s:
        cli1 = bd.Cliente(nombre="Cli1")
        cli2 = bd.Cliente(nombre="Cli2")
        car1 = bd.Carrier(nombre="Car1")
        car2 = bd.Carrier(nombre="Car2")
        s.add_all([cli1, cli2, car1, car2])
        s.commit()
        s.refresh(cli1)
        s.refresh(cli2)
        s.refresh(car1)
        s.refresh(car2)

    with bd.SessionLocal() as s:
        s.add_all(
            [
                bd.Conversacion(user_id="1", mensaje="hola", respuesta="hi", modo="t"),
                bd.Conversacion(user_id="2", mensaje="chau", respuesta="bye", modo="t"),
            ]
        )
        s.commit()
    bd.crear_ingreso(s1.id, "Cam1", usuario="u1")
    bd.crear_ingreso(s2.id, "Cam2", usuario="u2")
    bd.crear_tarea_programada(
        datetime(2024, 1, 1, 8), datetime(2024, 1, 1, 9), "A", [s1.id]
    )[0]
    t2, _ = bd.crear_tarea_programada(
        datetime(2024, 1, 2, 8), datetime(2024, 1, 2, 9), "B", [s2.id]
    )

    texto_cli = asyncio.run(_run("listar_clientes", []))["texto"]
    assert (
        texto_cli.splitlines()[1].startswith("1. ")
        and "Cli2" in texto_cli.splitlines()[1]
    )
    texto_car = asyncio.run(_run("listar_carriers", []))["texto"]
    assert (
        texto_car.splitlines()[1].startswith("1. ")
        and "Car2" in texto_car.splitlines()[1]
    )
    texto_conv = asyncio.run(_run("listar_conversaciones", []))["texto"]
    assert (
        texto_conv.splitlines()[1].startswith("1. ")
        and "chau" in texto_conv.splitlines()[1]
    )
    texto_ing = asyncio.run(_run("listar_ingresos", []))["texto"]
    assert (
        texto_ing.splitlines()[1].startswith("1. ")
        and "Cam2" in texto_ing.splitlines()[1]
    )
    texto_tareas = asyncio.run(_run("listar_tareas_programadas", []))["texto"]
    assert (
        texto_tareas.splitlines()[1].startswith("1. ")
        and "B" in texto_tareas.splitlines()[1]
    )
    texto_rel = asyncio.run(_run("listar_tareas_servicio", []))["texto"]
    assert (
        texto_rel.splitlines()[1].startswith("1. ")
        and f"{t2.id}-{s2.id}" in texto_rel.splitlines()[1]
    )


def test_depurar_duplicados():
    bd.Base.metadata.drop_all(bind=bd.engine)
    bd.Base.metadata.create_all(bind=bd.engine)
    s1 = bd.crear_servicio(nombre="Dup", cliente="X")
    bd.crear_servicio(nombre="Dup", cliente="X")
    bd.crear_reclamo(s1.id, "R10")
    bd.crear_reclamo(bd.crear_servicio(nombre="Otro", cliente="X").id, "R10")
    texto = asyncio.run(_run("depurar_duplicados", []))["texto"]
    assert "Servicios eliminados: 1" in texto
    assert "Reclamos eliminados: 1" in texto
