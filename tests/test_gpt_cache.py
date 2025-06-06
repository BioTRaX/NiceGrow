import sys
from types import ModuleType
from pathlib import Path
import importlib
import asyncio

# Preparar rutas para importar el paquete
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "Sandy bot"))

# Stub de openai para evitar llamadas reales
openai_stub = ModuleType("openai")
llamadas = {"n": 0}
class CompletionStub:
    async def create(self, *args, **kwargs):
        llamadas["n"] += 1
        class Resp:
            def __init__(self):
                self.choices = [type("msg", (), {"message": type("m", (), {"content": "respuesta"})()})]
        return Resp()
class AsyncOpenAI:
    def __init__(self, api_key=None):
        self.chat = type("c", (), {"completions": CompletionStub()})()
openai_stub.AsyncOpenAI = AsyncOpenAI
sys.modules.setdefault("openai", openai_stub)

# Stub para jsonschema utilizado por GPTHandler
jsonschema_stub = ModuleType("jsonschema")
class ValidationError(Exception):
    pass
def validate(*args, **kwargs):
    return None
jsonschema_stub.validate = validate
jsonschema_stub.ValidationError = ValidationError
sys.modules.setdefault("jsonschema", jsonschema_stub)

# Stub del paquete dotenv requerido por config
dotenv_stub = ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", dotenv_stub)

# Variables de entorno mínimas para instanciar Config
import os
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("NOTION_TOKEN", "x")
os.environ.setdefault("NOTION_DATABASE_ID", "x")
os.environ.setdefault("DB_USER", "x")
os.environ.setdefault("DB_PASSWORD", "x")

# Importar módulos de SandyBot
config_mod = importlib.import_module("sandybot.config")


def test_persistencia_cache(tmp_path):
    cache_file = tmp_path / "gpt_cache.json"
    config_mod.config.GPT_CACHE_FILE = cache_file

    gpt_module = importlib.reload(importlib.import_module("sandybot.gpt_handler"))
    handler = gpt_module.GPTHandler()
    asyncio.run(handler.consultar_gpt("hola"))

    assert llamadas["n"] == 1
    assert cache_file.exists()

    gpt_module = importlib.reload(gpt_module)
    handler2 = gpt_module.GPTHandler()
    asyncio.run(handler2.consultar_gpt("hola"))

    assert llamadas["n"] == 1

