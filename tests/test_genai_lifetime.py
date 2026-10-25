"""P0-8: a temporary google-genai Client closes its own HTTP client when it is collected, so
`google_client.make().models.generate_content(...)` failed with "Cannot send a request, as the
client has been closed" before anything was sent, and every still fell back to Manim (D7).

Why nothing caught it: every other test either replaces `google_client.make` with a fake or hands
the SDK an httpx client of the test's own (`httpx_client=mock`), which `Client.close()` leaves
alone. Here the real SDK builds its OWN httpx client and the fake sits below it, at
`httpx.HTTPTransport.handle_request`, above any socket. A second test scans the code for the
pattern."""
import ast
import base64
import json
import socket
from pathlib import Path

import httpx
import pytest

import config
from agents import image_agent

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic picture bytes"
SCENE = {"id": 1, "concept": "c", "narration": "n", "visual_type": "still", "visual_description": "a dark sky"}


class SocketOpened(BaseException):
    """Not an Exception: no retry loop or fallback may swallow it."""


@pytest.fixture
def own_http_client(monkeypatch):
    """The real google-genai SDK with the HTTP client it builds itself, answering with one picture.
    Yields the list of requests that reached the transport. A socket opened anywhere in the test
    fails it: the fake is below the SDK, so nothing should get that far."""
    sockets, requests = [], []

    def no_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            sockets.append(address)
            raise SocketOpened(address)

    def handle_request(self, request):
        requests.append((request.method, request.url.path))
        body = {"candidates": [{"content": {"role": "model", "parts": [{"inlineData": {
            "mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}]}, "finishReason": "STOP"}]}
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"content-type": "application/json"})

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: no_connect(self, address))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    yield requests
    assert sockets == [], "the test opened a network connection"


def test_a_still_is_requested_with_the_sdks_own_http_client(real_google, own_http_client, tmp_path):
    out = image_agent._buy(SCENE, tmp_path / "still.png")
    assert out.read_bytes() == PNG
    assert own_http_client == [("POST", f"/v1beta/models/{config.IMAGE_MODEL}:generateContent")]


def test_a_reviewed_still_reaches_the_image_model_through_the_real_sdk(
        real_google, own_http_client, stills_approved, tmp_path):
    """The whole path of a pipeline still: review log, render_still, ledger, then the request."""
    still = image_agent.reviewed_still(SCENE, tmp_path)
    assert still.read_bytes() == PNG and len(own_http_client) == 1


# --- no inline Client in the code ----------------------------------------------------------------

FACTORY_MODULE, FACTORY = "agents.google_client", "make"


def inline_uses(source: str) -> list[int]:
    """Lines where an attribute is read off the direct result of the genai Client factory
    (`google_client.make().models`, `genai.Client(...).models`): the Client is then unreferenced
    as soon as the attribute is read, and its `__del__` closes the HTTP client it owns.

    The factory is found by import (`from agents import google_client [as x]`, `import
    agents.google_client as x`, `from agents.google_client import make [as y]`, `genai.Client`,
    `from google.genai import Client`), never by the bare name `make`: other modules have their
    own. Known limit: an attribute read off a local Client that a function returns."""
    tree = ast.parse(source)
    modules, funcs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agents":
            modules |= {a.asname or a.name for a in node.names if a.name == "google_client"}
        elif isinstance(node, ast.ImportFrom) and node.module == FACTORY_MODULE:
            funcs |= {a.asname or a.name for a in node.names if a.name == FACTORY}
        elif isinstance(node, ast.ImportFrom) and node.module == "google.genai":
            funcs |= {a.asname or a.name for a in node.names if a.name == "Client"}
        elif isinstance(node, ast.ImportFrom) and node.module == "google":
            modules |= {a.asname or a.name for a in node.names if a.name == "genai"}
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == FACTORY_MODULE:
                    modules.add(a.asname or a.name)
                elif a.name == "google.genai":
                    modules.add(a.asname or a.name)

    def is_factory(call: ast.Call) -> bool:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id in funcs
        return isinstance(f, ast.Attribute) and f.attr in (FACTORY, "Client") and ast.unparse(f.value) in modules

    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Call) and is_factory(n.value)]


BAD = [
    "from agents import google_client\ngoogle_client.make().models.get(model='m')",
    "from agents import google_client as gc\nx = gc.make().files",
    "from agents.google_client import make\nmake(httpx_client=None).models",
    "from agents.google_client import make as mk\nmk().operations",
    "import agents.google_client as g\ng.make().models",
    "import agents.google_client\nagents.google_client.make().models",
    "from google import genai\ngenai.Client(api_key='k').models",
    "import google.genai as genai\ngenai.Client(api_key='k').models",
    "from google.genai import Client\nClient().models",
]
GOOD = [
    "from agents import google_client\nc = google_client.make()\nc.models.get(model='m')",
    "from agents import google_client\nwith google_client.make() as c:\n    c.models",
    "from agents import google_client\nclient = google_client.make()",
    "import assembly\nassembly.make().x",        # another module's make
    "def make():\n    return 1\nmake().real",      # a local make
    "from thumbnail import make\nmake().models",  # imported from elsewhere
]


def test_the_scanner_flags_inline_clients_and_only_those():
    assert not [s for s in BAD if not inline_uses(s)], "missed"
    assert not [s for s in GOOD if inline_uses(s)], "false alarm"


def test_no_genai_client_is_used_inline_anywhere_in_the_code():
    files = [p for d in ("agents", "scripts") for p in (ROOT / d).rglob("*.py")] + list(ROOT.glob("*.py"))
    assert files, "found no code to scan"
    hits = {str(p.relative_to(ROOT)): inline_uses(p.read_text()) for p in files}
    assert not {k: v for k, v in hits.items() if v}, "keep the Client in a variable for the whole call (P0-8)"
