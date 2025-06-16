# Nombre de archivo: test_identificador_tarea.py
# Ubicación de archivo: tests/test_identificador_tarea.py
# User-provided custom instructions
import asyncio
import importlib
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]

from tests.telegram_stub import Document, Message, Update

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

# Stub de extract_msg para leer texto
extract_stub = ModuleType("extract_msg")


class Msg:
    def __init__(self, path):
        self.body = Path(path).read_text()
        self.subject = "asunto"


extract_stub.Message = Msg
sys.modules.setdefault("extract_msg", extract_stub)

# Base de datos en memoria
import sqlalchemy

orig_engine = sqlalchemy.create_engine
sqlalchemy.create_engine = lambda *a, **k: orig_engine("sqlite:///:memory:")
import sandybot.database as bd

sqlalchemy.create_engine = orig_engine
bd.SessionLocal = sessionmaker(bind=bd.engine, expire_on_commit=False)
bd.Base.metadata.create_all(bind=bd.engine)

TEMP_DIR = None


def test_identificador_tarea(tmp_path):
    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = lambda: str(TEMP_DIR)

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg

    mod_name = f"{pkg}.identificador_tarea"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "identificador_tarea.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    servicio = bd.crear_servicio(nombre="Srv", cliente="Cli")

    import sandybot.email_utils as email_utils

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "2024-01-02T08:00:00", "fin": "2024-01-02T10:00:00", '
                '"tipo": "Mant", "afectacion": "1h", "ids": [' + str(servicio.id) + "]}"
            )

    email_utils.gpt = GPTStub()

    doc = Document(file_name="aviso.msg", content="dummy")
    msg = Message("Cli Telco", document=doc)
    update = Update(message=msg)
    ctx = SimpleNamespace(args=[], user_data={})

    with bd.SessionLocal() as s:
        prev_tareas = s.query(bd.TareaProgramada).count()
        prev_rels = s.query(bd.TareaServicio).count()

    asyncio.run(mod.procesar_identificador_tarea(update, ctx))

    with bd.SessionLocal() as s:
        tareas_list = s.query(bd.TareaProgramada).all()
        tareas = len(tareas_list)
        rels = s.query(bd.TareaServicio).count()

    tempfile.gettempdir = orig_tmp

    assert tareas == prev_tareas + 1
    assert rels == prev_rels
    assert msg.sent is None


def test_identificador_tarea_heuristicas(tmp_path):
    """Detecta carrier en el cuerpo y lee .msg sin body."""

    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = lambda: str(TEMP_DIR)

    class Msg2:
        def __init__(self, path):
            self.body = ""
            self.subject = ""

    extract_stub.Message = Msg2

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg

    mod_name = f"{pkg}.identificador_tarea"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "identificador_tarea.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    servicio = bd.crear_servicio(nombre="Srv2", cliente="Cli")

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
    archivo = tmp_path / "mail.msg"
    archivo.write_text(cuerpo)

    doc = Document(file_name="mail.msg", content=cuerpo)
    msg = Message("Cli", document=doc)
    update = Update(message=msg)
    ctx = SimpleNamespace(args=[], user_data={})

    with bd.SessionLocal() as s:
        prev_tareas = s.query(bd.TareaProgramada).count()

    asyncio.run(mod.procesar_identificador_tarea(update, ctx))

    with bd.SessionLocal() as s:
        tarea = (
            s.query(bd.TareaProgramada).order_by(bd.TareaProgramada.id.desc()).first()
        )
        srv = s.get(bd.Servicio, servicio.id)

    tempfile.gettempdir = orig_tmp

    assert tarea.id == prev_tareas + 1
    assert srv.carrier_id is None


def test_identificador_tarea_telxius(tmp_path):
    """Caso real con servicio faltante."""

    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = lambda: str(TEMP_DIR)

    class MsgTelxius:
        def __init__(self, path):
            self.body = Path(path).read_text()
            self.subject = "TELXIUS - METROTEL - EMERGENCY"
            self.sender = "noc@telxius.com"
            self.sender_name = "Telxius"

    extract_stub.Message = MsgTelxius

    pkg = "sandybot.handlers"
    dummy = ModuleType(f"{pkg}.identificador_tarea")
    dummy.iniciar_identificador_tarea = lambda *a, **k: None
    dummy.procesar_identificador_tarea = lambda *a, **k: None
    sys.modules[f"{pkg}.identificador_tarea"] = dummy
    importlib.import_module(pkg)
    mod_name = f"{pkg}.identificador_tarea"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "identificador_tarea.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    archivo = ROOT_DIR / "tests" / "data" / "telxius.msg"
    doc = Document(file_name="telxius.msg", content=archivo.read_text())
    msg = Message("Cli", document=doc)
    update = Update(message=msg)
    ctx = SimpleNamespace(args=[], user_data={})

    asyncio.run(mod.procesar_identificador_tarea(update, ctx))

    with bd.SessionLocal() as s:
        tarea = (
            s.query(bd.TareaProgramada).order_by(bd.TareaProgramada.id.desc()).first()
        )
        pendiente = s.query(bd.ServicioPendiente).filter_by(tarea_id=tarea.id).first()

    tempfile.gettempdir = orig_tmp

    assert pendiente.id_carrier == "CRT-008785"
    with bd.SessionLocal() as s:
        pendientes = [
            p.id_carrier
            for p in s.query(bd.ServicioPendiente).filter_by(tarea_id=tarea.id)
        ]
    assert "CRT-008785" in pendientes
    assert msg.sent is None


def test_identificador_tarea_duplicada(tmp_path):
    """No duplica la tarea si el mismo correo se procesa dos veces."""

    global TEMP_DIR
    TEMP_DIR = tmp_path
    orig_tmp = tempfile.gettempdir
    tempfile.gettempdir = lambda: str(TEMP_DIR)

    pkg = "sandybot.handlers"
    if pkg not in sys.modules:
        handlers_pkg = ModuleType(pkg)
        handlers_pkg.__path__ = [str(ROOT_DIR / "Sandy bot" / "sandybot" / "handlers")]
        sys.modules[pkg] = handlers_pkg

    mod_name = f"{pkg}.identificador_tarea"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        ROOT_DIR / "Sandy bot" / "sandybot" / "handlers" / "identificador_tarea.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    servicio = bd.crear_servicio(nombre="SrvDup", cliente="Cli")

    import sandybot.email_utils as email_utils

    class GPTStub(email_utils.gpt.__class__):
        async def consultar_gpt(self, mensaje: str, cache: bool = True) -> str:
            return (
                '{"inicio": "2024-01-02T08:00:00", "fin": "2024-01-02T10:00:00", '
                '"tipo": "Mant", "afectacion": "1h", "ids": [' + str(servicio.id) + "]}"
            )

    email_utils.gpt = GPTStub()

    content = f"Carrier: Telco\nID001\nInicio: 02/01 08:00\nFin: 02/01 10:00\nServicios: {servicio.id}"
    doc = Document(file_name="dup.msg", content=content)
    msg = Message("Cli", document=doc)

    capt_texts = []

    async def cap_reply(text, *a, **k):
        capt_texts.append(text)

    msg.reply_text = cap_reply

    update = Update(message=msg)
    ctx = SimpleNamespace(args=[], user_data={})

    flags = []

    real_proc = email_utils.procesar_correo_a_tarea

    async def wrap(*a, **k):
        res = await real_proc(*a, **k)
        flags.append(res[1])
        return res

    email_utils.procesar_correo_a_tarea = wrap
    mod.procesar_correo_a_tarea = wrap

    with bd.SessionLocal() as s:
        prev = s.query(bd.TareaProgramada).count()

    asyncio.run(mod.procesar_identificador_tarea(update, ctx))
    asyncio.run(mod.procesar_identificador_tarea(update, ctx))

    with bd.SessionLocal() as s:
        total = s.query(bd.TareaProgramada).count()

    tempfile.gettempdir = orig_tmp

    assert total == prev + 1
    assert flags == [True, False]
    assert capt_texts[-1].startswith("🔄")
