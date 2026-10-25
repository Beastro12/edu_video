"""P3-6: scripts/preflight.py finds a blocked host, a wrong model name, a bad key, a free-tier key, a
missing FFmpeg filter or a full disk before money is spent. Every provider is mocked: the tests
assert which calls reach each one (the offline guard in conftest blocks any that slips through)."""
import json
import re
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
import requests
from conftest import PaidCallInOfflineTest
from google.genai import errors as genai_errors

import config
import ledger
from agents import google_client, llm, voice
from scripts import preflight

KEYS = {"ANTHROPIC_API_KEY": "sk-ant-test-AAAA1111", "ELEVENLABS_API_KEY": "xi-test-BBBB2222",
        "ELEVENLABS_VOICE_ID": "voice-abc123", "GOOGLE_API_KEY": "AIza-test-CCCC3333"}

# Excerpts of what FFmpeg 6.1.1 prints for `ffmpeg -hide_banner -h filter=<name>` (and encoder=libx264):
# the same layout, the same option lines, trimmed to the first options (ebur128: up to `peak`).
REAL_HELP = {
    "loudnorm": """\
Filter loudnorm
  EBU R128 loudness normalization
    Inputs:
       #0: default (audio)
    Outputs:
       #0: default (audio)
loudnorm AVOptions:
   I                 <double>     ..F.A...... set integrated loudness target (from -70 to -5) (default -24)
   i                 <double>     ..F.A...... set integrated loudness target (from -70 to -5) (default -24)
   LRA               <double>     ..F.A...... set loudness range target (from 1 to 50) (default 7)
   lra               <double>     ..F.A...... set loudness range target (from 1 to 50) (default 7)
   TP                <double>     ..F.A...... set maximum true peak (from -9 to 0) (default -2)
   tp                <double>     ..F.A...... set maximum true peak (from -9 to 0) (default -2)
   measured_I        <double>     ..F.A...... measured IL of input file (from -99 to 0) (default 0)
""",
    "alimiter": """\
Filter alimiter
  Audio lookahead limiter.
    Inputs:
       #0: main (audio)
    Outputs:
       #0: default (audio)
alimiter AVOptions:
   level_in          <double>     ..F.A....T. set input level (from 0.015625 to 64) (default 1)
   level_out         <double>     ..F.A....T. set output level (from 0.015625 to 64) (default 1)
   limit             <double>     ..F.A....T. set limit (from 0.0625 to 1) (default 1)
   attack            <double>     ..F.A....T. set attack (from 0.1 to 80) (default 5)
   release           <double>     ..F.A....T. set release (from 1 to 8000) (default 50)
   asc               <boolean>    ..F.A....T. enable asc (default false)
   asc_level         <double>     ..F.A....T. set asc level (from 0 to 1) (default 0.5)
   level             <boolean>    ..F.A....T. auto level (default true)
   latency           <boolean>    ..F.A....T. compensate delay (default false)

This filter has support for timeline through the 'enable' option.
""",
    "sidechaincompress": """\
Filter sidechaincompress
  Sidechain compressor.
    Inputs:
       #0: main (audio)
       #1: sidechain (audio)
    Outputs:
       #0: default (audio)
acompressor/sidechaincompress AVOptions:
   level_in          <double>     ..F.A....T. set input gain (from 0.015625 to 64) (default 1)
   mode              <int>        ..F.A....T. set mode (from 0 to 1) (default downward)
     downward        0            ..F.A....T.
     upward          1            ..F.A....T.
   threshold         <double>     ..F.A....T. set threshold (from 0.000976563 to 1) (default 0.125)
   ratio             <double>     ..F.A....T. set ratio (from 1 to 20) (default 2)
   attack            <double>     ..F.A....T. set attack (from 0.01 to 2000) (default 20)
   release           <double>     ..F.A....T. set release (from 0.01 to 9000) (default 250)
""",
    "xfade": """\
Filter xfade
  Cross fade one video with another video.
    slice threading supported
    Inputs:
       #0: main (video)
       #1: xfade (video)
    Outputs:
       #0: default (video)
xfade AVOptions:
   transition        <int>        ..FV....... set cross fade transition (from -1 to 57) (default fade)
     custom          -1           ..FV....... custom transition
     fade            0            ..FV....... fade transition
     wipeleft        1            ..FV....... wipe left transition
     wiperight       2            ..FV....... wipe right transition
     wipeup          3            ..FV....... wipe up transition
     wipedown        4            ..FV....... wipe down transition
""",
    "acrossfade": """\
Filter acrossfade
  Cross fade two input audio streams.
    Inputs:
       #0: crossfade0 (audio)
       #1: crossfade1 (audio)
    Outputs:
       #0: default (audio)
acrossfade AVOptions:
   nb_samples        <int>        ..F.A...... set number of samples for cross fade duration (from 1 to 2.14748e+08) (default 44100)
   ns                <int>        ..F.A...... set number of samples for cross fade duration (from 1 to 2.14748e+08) (default 44100)
   duration          <duration>   ..F.A...... set cross fade duration (default 0)
   d                 <duration>   ..F.A...... set cross fade duration (default 0)
   overlap           <boolean>    ..F.A...... overlap 1st stream end with 2nd stream start (default true)
   o                 <boolean>    ..F.A...... overlap 1st stream end with 2nd stream start (default true)
   curve1            <int>        ..F.A...... set fade curve type for 1st stream (from -1 to 22) (default tri)
     nofade          -1           ..F.A...... no fade; keep audio as-is
""",
    "ebur128": """\
Filter ebur128
  EBU R128 scanner.
    Inputs:
       #0: default (audio)
    Outputs:
        dynamic (depending on the options)
ebur128 AVOptions:
   video             <boolean>    ..FV....... set video output (default false)
   size              <image_size> ..FV....... set video size (default "640x480")
   meter             <int>        ..FV....... set scale meter (+9 to +18) (from 9 to 18) (default 9)
   framelog          <int>        ..FVA...... force frame logging level (from INT_MIN to INT_MAX) (default -1)
     quiet           -8           ..FVA...... logging disabled
     info            32           ..FVA...... information logging level
     verbose         40           ..FVA...... verbose logging level
   metadata          <boolean>    ..FVA...... inject metadata in the filtergraph (default false)
   peak              <flags>      ..F.A...... set peak mode (default 0)
     none                         ..F.A...... disable any peak mode
     sample                       ..F.A...... enable peak-sample mode
     true                         ..F.A...... enable true-peak mode
""",
    "libx264": """\
Encoder libx264 [libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10]:
    General capabilities: dr1 delay threads
    Threading capabilities: other
    Supported pixel formats: yuv420p yuvj420p yuv422p yuvj422p yuv444p yuvj444p nv12 nv16 nv21 yuv420p10le yuv422p10le yuv444p10le nv20le gray gray10le
libx264 AVOptions:
  -preset            <string>     E..V....... Set the encoding preset (cf. x264 --fullhelp) (default "medium")
  -tune              <string>     E..V....... Tune the encoding params (cf. x264 --fullhelp)
  -profile           <string>     E..V....... Set profile restrictions (cf. x264 --fullhelp)
  -fastfirstpass     <boolean>    E..V....... Use fast settings when encoding first pass (default true)
  -level             <string>     E..V....... Specify level (as defined by Annex A)
""",
}

UNKNOWN_FILTER = "Unknown filter '{name}'.\n"  # what FFmpeg 6.1.1 prints, with exit code 0
UNKNOWN_ENCODER = "Codec '{name}' is not recognized by FFmpeg.\n"
ALIMITER_WITHOUT_LATENCY = REAL_HELP["alimiter"].replace(
    "   latency           <boolean>    ..F.A....T. compensate delay (default false)\n", "")
EBUR128_WITHOUT_TRUE = REAL_HELP["ebur128"].replace(
    "     true                         ..F.A...... enable true-peak mode\n", "")
EBUR128_WITHOUT_PEAK = re.sub(r"   peak .*?(?=\Z)", "", REAL_HELP["ebur128"], flags=re.S)
MANIM_VERSION = "Manim Community v0.19.0\n"


class FakeResponse:
    def __init__(self, status, body=None, content=b"", text=None):
        self.status_code, self._body, self.content = status, body, content
        self.text = text if text is not None else json.dumps(body)
        self.headers = {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def refusal(status, word, message):
    """An ElevenLabs error answer: {"detail": {"status", "message"}}."""
    return FakeResponse(status, {"detail": {"status": word, "message": message}})


class World:
    """Every provider faked, answering PASS to everything; a test changes one knob and runs main().
    Counts what reached each provider."""

    def __init__(self, monkeypatch, tmp_path):
        self.mp, self.tmp = monkeypatch, tmp_path
        for key, value in KEYS.items():
            monkeypatch.setattr(config, key, value)
        monkeypatch.setattr(config, "BUDGET_EUR", 10.0)
        monkeypatch.setattr(config, "TTS_MODEL", "eleven_multilingual_v2")
        # hosts and ElevenLabs GETs
        self.get_urls = []
        self.host = {}       # host -> exception to raise, or a status to answer
        self.eleven = {      # path -> answer
            "/v1/models": FakeResponse(200, [{"model_id": "eleven_multilingual_v2"}, {"model_id": "eleven_v4"}]),
            f"/v1/voices/{KEYS['ELEVENLABS_VOICE_ID']}": FakeResponse(200, {"voice_id": "x", "name": "Calm"}),
            "/v1/user/subscription": FakeResponse(200, {"tier": "creator", "character_count": 1_000,
                                                        "character_limit": 100_000}),
        }
        monkeypatch.setattr(requests, "get", self.get)
        # Claude
        self.claude_clients = []
        self.retrieve_error = None
        self.claude_error = None
        self.claude_stop = "tool_use"
        self.claude_calls, self.retrieve_calls = [], []
        monkeypatch.setattr(preflight, "_anthropic_client", self.anthropic_client)
        # Google
        self.google_get, self.google_error, self.generate_calls = [], {}, []
        self.image_error = None
        monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(models=SimpleNamespace(
            get=self.models_get, generate_content=self.generate_content)))
        # ElevenLabs narration
        self.posts, self.post_error, self.post_status = [], None, 200
        monkeypatch.setattr(requests, "post", self.post)
        self.seconds = 14.0
        monkeypatch.setattr(preflight, "duration", lambda path: self.seconds)
        # local tools
        self.tools = {**{("filter", n): t for n, t in REAL_HELP.items() if n != "libx264"},
                      ("encoder", "libx264"): REAL_HELP["libx264"]}
        self.manim = (0, MANIM_VERSION, "")
        self.tool_calls = []
        monkeypatch.setattr(preflight, "_tool", self.tool)
        self.free_gb, self.disk_paths = 50.0, []
        monkeypatch.setattr(preflight.shutil, "disk_usage", self.disk_usage)

    # --- fakes ---
    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.get_urls.append((url, timeout))
        if url.startswith("https://api.elevenlabs.io/v1/"):
            assert headers == {"xi-api-key": config.ELEVENLABS_API_KEY}
            outcome = self.eleven[url.removeprefix("https://api.elevenlabs.io")]
        else:
            outcome = self.host.get(url.split("/")[2], 404)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome) if isinstance(outcome, int) else outcome

    def anthropic_client(self):
        self.claude_clients.append(object())

        def retrieve(model):
            self.retrieve_calls.append(model)
            if self.retrieve_error:
                raise self.retrieve_error
            return SimpleNamespace(id=model, display_name="Claude Test")

        def create(**kwargs):
            self.claude_calls.append(kwargs)
            if self.claude_error:
                raise self.claude_error
            return SimpleNamespace(stop_reason=self.claude_stop, content=[],
                                   usage=SimpleNamespace(input_tokens=61, output_tokens=9))
        return SimpleNamespace(models=SimpleNamespace(retrieve=retrieve), messages=SimpleNamespace(create=create))

    def models_get(self, model, config):
        self.google_get.append((model, config))
        if model in self.google_error:
            raise self.google_error[model]
        return SimpleNamespace(name=f"models/{model}", display_name=f"Display {model}")

    def generate_content(self, model, contents, config):
        self.generate_calls.append((model, contents, config))
        if self.image_error:
            raise self.image_error
        image = SimpleNamespace(data=b"\x89PNG fake picture", mime_type="image/png")
        return SimpleNamespace(parts=[SimpleNamespace(inline_data=image, thought=None)])

    def post(self, url, headers, json, timeout):
        self.posts.append((url, json))
        if self.post_error:
            raise self.post_error
        return FakeResponse(self.post_status, content=b"mp3 bytes", text="refused" if self.post_status != 200 else "")

    def tool(self, cmd):
        self.tool_calls.append(cmd)
        if cmd[0] == "manim":
            return self.manim
        kind, name = cmd[-1].split("=")
        text = self.tools.get((kind, name))
        if isinstance(text, BaseException):
            raise text
        if text is None:  # FFmpeg answers an unknown name with text on stdout and exit code 0
            text = (UNKNOWN_FILTER if kind == "filter" else UNKNOWN_ENCODER).format(name=name)
        return 0, text, ""

    def disk_usage(self, path):
        self.disk_paths.append(Path(path))
        return SimpleNamespace(free=self.free_gb * 1e9)

    # --- running ---
    def run(self, capsys, *flags):
        code = preflight.main(list(flags))
        out = capsys.readouterr()
        self.out, self.err = out.out, out.err
        self.lines = {}
        for line in self.out.splitlines():
            m = re.match(r"(PASS|WARN|SKIP|FAIL) ([^:]+): (.*)$", line)
            if m:
                self.lines.setdefault(m[2], []).append((m[1], m[3]))
        return code

    def status(self, check):
        return self.lines[check][0][0]

    def detail(self, check):
        return self.lines[check][0][1]

    @property
    def paid_calls(self):
        return {"claude": len(self.claude_calls), "image": len(self.generate_calls), "tts": len(self.posts)}


@pytest.fixture
def world(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


def ledger_rows():
    return ledger.entries()


CHECKS = ["host api.anthropic.com", "host api.elevenlabs.io", "host generativelanguage.googleapis.com",
          "claude model", "claude call", "elevenlabs models", "elevenlabs voice", "elevenlabs subscription",
          "google image model", "google veo model",
          *(f"ffmpeg filter {f}" for f in ("loudnorm", "alimiter", "sidechaincompress", "xfade", "acrossfade",
                                           "ebur128")),
          "ffmpeg encoder libx264", "manim", "disk"]


# --- 1. everything passes ----------------------------------------------------------------------

def test_everything_passes_and_exits_zero(world, capsys):
    assert world.run(capsys) == 0
    assert list(world.lines) == CHECKS
    assert {s for lines in world.lines.values() for s, _ in lines} == {"PASS"}
    last = world.out.splitlines()[-1]
    assert last.startswith(f"preflight: {len(CHECKS)} PASS, 0 WARN, 0 SKIP, 0 FAIL") and last.endswith("ready")
    assert "plan creator, 99,000 of 100,000 characters left" in world.detail("elevenlabs subscription")
    assert "stop_reason tool_use" in world.detail("claude call")


def test_each_line_is_status_check_detail(world, capsys):
    world.run(capsys)
    body = world.out.splitlines()[:-1]
    assert len(body) == len(CHECKS)
    assert all(re.match(r"(PASS|WARN|SKIP|FAIL) [a-z0-9. ]+: \S", line) for line in body), body


# --- 2. each FAIL type --------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    requests.ConnectionError("Max retries exceeded: name resolution failed"),
    requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden"),
    requests.Timeout("Read timed out"),
], ids=lambda e: type(e).__name__)
def test_host_that_cannot_be_reached_fails(world, capsys, error):
    world.host["api.elevenlabs.io"] = error
    assert world.run(capsys) == 1
    assert world.status("host api.elevenlabs.io") == "FAIL"
    assert type(error).__name__ in world.detail("host api.elevenlabs.io")
    assert world.status("host api.anthropic.com") == "PASS" == world.status("host generativelanguage.googleapis.com")
    assert world.out.splitlines()[-1].startswith(f"preflight: {len(CHECKS) - 1} PASS, 0 WARN, 0 SKIP, 1 FAIL")


@pytest.mark.parametrize("status", [200, 301, 401, 403, 404, 405, 421, 500, 503])
def test_any_http_status_means_the_host_is_reachable(world, capsys, status):
    world.host.update({h: status for h in preflight.HOSTS})
    assert world.run(capsys) == 0
    assert all(world.status(f"host {h}") == "PASS" and f"HTTP {status}" in world.detail(f"host {h}")
               for h in preflight.HOSTS)


def test_host_check_uses_the_environments_proxy_settings_and_a_short_timeout(world, capsys, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:3128")
    monkeypatch.setenv("NO_PROXY", "api.anthropic.com")
    world.run(capsys)
    assert "(direct)" in world.detail("host api.anthropic.com")
    assert "(via proxy)" in world.detail("host api.elevenlabs.io")
    hosts = [(u, t) for u, t in world.get_urls if "/v1/" not in u]
    assert [t for _, t in hosts] == [config.PREFLIGHT_TIMEOUT_S] * 3
    assert all(t == config.PREFLIGHT_TIMEOUT_S for _, t in world.get_urls), "no probe waits longer"


def test_claude_404_for_the_model_fails_and_skips_the_paid_call(world, capsys):
    world.retrieve_error = anthropic.NotFoundError(
        "model: claude-nope", response=httpx2.Response(404, request=httpx2.Request("GET", "https://x/")),
        body={"type": "error", "error": {"type": "not_found_error", "message": "model: claude-nope"}})
    assert world.run(capsys) == 1
    assert world.status("claude model") == "FAIL"
    assert "404" in world.detail("claude model") and "CLAUDE_MODEL" in world.detail("claude model")
    assert world.status("claude call") == "SKIP"
    assert world.paid_calls["claude"] == 0 and ledger_rows() == []


def test_claude_bad_key_fails_both_claude_checks_and_spends_nothing(world, capsys):
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=httpx2.Request("GET", "https://x/")), body=None)
    world.retrieve_error = world.claude_error = error
    assert world.run(capsys) == 1
    assert "401" in world.detail("claude model") or "invalid x-api-key" in world.detail("claude model")
    assert world.status("claude call") == "FAIL"
    assert ledger_rows() == [], "a refused call is not recorded"


def test_elevenlabs_401_fails_every_elevenlabs_check(world, capsys):
    bad = refusal(401, "invalid_api_key", "Invalid API key")
    world.eleven = {path: bad for path in world.eleven}
    assert world.run(capsys) == 1
    for check in ("elevenlabs models", "elevenlabs voice", "elevenlabs subscription"):
        assert world.status(check) == "FAIL" and "401" in world.detail(check), check
    assert "invalid_api_key" in world.detail("elevenlabs models")


def test_elevenlabs_voice_not_found_fails_by_voice(world, capsys):
    world.eleven[f"/v1/voices/{KEYS['ELEVENLABS_VOICE_ID']}"] = refusal(
        404, "voice_not_found", "A voice with the voice_id was not found.")
    assert world.run(capsys) == 1
    assert world.status("elevenlabs voice") == "FAIL"
    assert KEYS["ELEVENLABS_VOICE_ID"] in world.detail("elevenlabs voice")
    assert "not found" in world.detail("elevenlabs voice") and "ELEVENLABS_VOICE_ID" in world.detail("elevenlabs voice")
    assert world.status("elevenlabs models") == "PASS" == world.status("elevenlabs subscription")


def test_elevenlabs_model_not_listed_fails(world, capsys, monkeypatch):
    monkeypatch.setattr(config, "TTS_MODEL", "eleven_v9")
    assert world.run(capsys) == 1
    assert "eleven_v9 is not listed" in world.detail("elevenlabs models")
    assert "TTS_MODEL" in world.detail("elevenlabs models")


def test_elevenlabs_free_plan_warns_and_does_not_fail(world, capsys):
    world.eleven["/v1/user/subscription"] = FakeResponse(
        200, {"tier": "free", "character_count": 2_500, "character_limit": 10_000})
    assert world.run(capsys) == 0
    assert world.status("elevenlabs subscription") == "WARN"
    assert "plan free, 7,500 of 10,000 characters left" in world.detail("elevenlabs subscription")
    assert world.out.splitlines()[-1].startswith(f"preflight: {len(CHECKS) - 1} PASS, 1 WARN, 0 SKIP, 0 FAIL")


def test_elevenlabs_key_without_user_read_has_its_own_text(world, capsys):
    world.eleven["/v1/user/subscription"] = refusal(401, "missing_permissions", "The API key is missing user_read")
    assert world.run(capsys) == 1
    assert world.detail("elevenlabs subscription") == "key lacks user_read; TTS may still work"
    assert world.status("elevenlabs models") == "PASS" == world.status("elevenlabs voice")


def test_google_404_fails_that_model_only(world, capsys):
    world.google_error[config.IMAGE_MODEL] = genai_errors.ClientError(
        404, {"error": {"code": 404, "message": "models/x is not found", "status": "NOT_FOUND"}})
    assert world.run(capsys) == 1
    assert world.status("google image model") == "FAIL"
    assert "404" in world.detail("google image model") and "IMAGE_MODEL" in world.detail("google image model")
    assert world.status("google veo model") == "PASS"
    assert [m for m, _ in world.google_get] == [config.IMAGE_MODEL, config.VEO_MODEL]


def test_google_other_errors_fail_with_their_text(world, capsys):
    world.google_error[config.VEO_MODEL] = genai_errors.ClientError(
        403, {"error": {"code": 403, "message": "API key not valid", "status": "PERMISSION_DENIED"}})
    assert world.run(capsys) == 1
    assert "403" in world.detail("google veo model") and "API key not valid" in world.detail("google veo model")


def test_google_probe_makes_one_attempt_with_a_short_timeout(world, capsys):
    world.run(capsys)
    assert len(world.google_get) == 2
    for _, cfg in world.google_get:
        assert cfg.http_options.retry_options.attempts == 1
        assert cfg.http_options.timeout == config.PREFLIGHT_TIMEOUT_S * 1000


@pytest.mark.parametrize("name", ["loudnorm", "alimiter", "sidechaincompress", "xfade", "acrossfade", "ebur128"])
def test_missing_filter_fails_though_ffmpeg_exits_zero(world, capsys, name):
    del world.tools[("filter", name)]  # the fake prints FFmpeg's "Unknown filter" text and exits 0
    assert world.run(capsys) == 1
    assert world.status(f"ffmpeg filter {name}") == "FAIL"
    assert f"no filter {name}" in world.detail(f"ffmpeg filter {name}")
    assert "Unknown filter" in world.detail(f"ffmpeg filter {name}")
    assert [world.status(c) for c in CHECKS if c.startswith("ffmpeg filter") and not c.endswith(name)] == ["PASS"] * 5


def test_alimiter_without_latency_fails(world, capsys):
    world.tools[("filter", "alimiter")] = ALIMITER_WITHOUT_LATENCY
    assert world.run(capsys) == 1
    assert "latency" in world.detail("ffmpeg filter alimiter")


@pytest.mark.parametrize("text, words", [(EBUR128_WITHOUT_TRUE, "`true` value"), (EBUR128_WITHOUT_PEAK, "`peak` option")])
def test_ebur128_without_true_peak_fails(world, capsys, text, words):
    world.tools[("filter", "ebur128")] = text
    assert world.run(capsys) == 1
    assert words in world.detail("ffmpeg filter ebur128")


def test_a_peak_option_elsewhere_does_not_pass_ebur128(world, capsys):
    """`true` must be a value of `peak`, not any word `true` in the text (true_peak is a metadata key)."""
    text = EBUR128_WITHOUT_TRUE + "   true_peak         <double>     ..F.A.XR... true peak (dBFS) (default 0)\n"
    world.tools[("filter", "ebur128")] = text
    assert world.run(capsys) == 1


def test_missing_x264_encoder_fails(world, capsys):
    del world.tools[("encoder", "libx264")]
    assert world.run(capsys) == 1
    assert "no encoder libx264" in world.detail("ffmpeg encoder libx264")


def test_ffmpeg_not_installed_fails_every_ffmpeg_check_without_crashing(world, capsys):
    gone = RuntimeError("ffmpeg is not installed (not on PATH)")
    world.tools = {key: gone for key in world.tools}
    assert world.run(capsys) == 1
    assert [world.status(c) for c in CHECKS if c.startswith("ffmpeg")] == ["FAIL"] * 7
    assert "not installed" in world.detail("ffmpeg filter loudnorm")


def test_manim_missing_or_broken_fails(world, capsys):
    world.manim = (1, "", "ModuleNotFoundError: No module named 'manim'")
    assert world.run(capsys) == 1
    assert "exited 1" in world.detail("manim") and "ModuleNotFoundError" in world.detail("manim")


def test_manim_version_is_shown(world, capsys):
    world.run(capsys)
    assert world.detail("manim") == "Manim Community v0.19.0"


def test_low_disk_fails_and_enough_passes(world, capsys):
    world.free_gb = 4.9
    assert world.run(capsys) == 1
    assert world.status("disk") == "FAIL" and "4.9 GB" in world.detail("disk") and "5 GB" in world.detail("disk")
    world.free_gb = 5.0
    assert world.run(capsys) == 0
    assert world.status("disk") == "PASS"


def test_disk_is_measured_where_build_will_be_even_if_it_does_not_exist_yet(world):
    build = Path(config.BUILD_DIR)
    assert not build.exists()
    assert preflight._disk()[0] == "PASS"
    assert world.disk_paths == [build.parent], "the nearest parent of build/ that exists"
    build.mkdir()
    preflight._disk()
    assert world.disk_paths[-1] == build


def test_minimum_free_disk_is_a_setting(world, capsys, monkeypatch):
    monkeypatch.setattr(config, "PREFLIGHT_MIN_FREE_GB", 80)
    assert world.run(capsys) == 1 and "80 GB" in world.detail("disk")


@pytest.mark.parametrize("key, checks", [
    ("ANTHROPIC_API_KEY", ["claude model", "claude call"]),
    ("ELEVENLABS_API_KEY", ["elevenlabs models", "elevenlabs voice", "elevenlabs subscription"]),
    ("ELEVENLABS_VOICE_ID", ["elevenlabs voice"]),
    ("GOOGLE_API_KEY", ["google image model", "google veo model"]),
])
def test_a_missing_key_fails_its_checks_by_name_and_calls_nothing(world, capsys, monkeypatch, key, checks):
    monkeypatch.setattr(config, key, None)
    assert world.run(capsys) == 1
    failed = [c for c in CHECKS if world.status(c) == "FAIL"]
    assert failed == checks
    assert all(f"missing {key}" in world.detail(c) for c in checks)
    # the providers of that key were never called for those checks
    if key == "ANTHROPIC_API_KEY":
        assert world.retrieve_calls == [] and world.claude_calls == []
    if key == "GOOGLE_API_KEY":
        assert world.google_get == []
    if key == "ELEVENLABS_VOICE_ID":
        assert not any("/voices/" in u for u, _ in world.get_urls)


def test_with_no_keys_at_all_every_provider_check_fails_and_no_provider_is_called(world, capsys, monkeypatch):
    for key in KEYS:
        monkeypatch.setattr(config, key, None)
    assert world.run(capsys) == 1
    assert [world.status(c) for c in CHECKS if c.startswith(("claude", "elevenlabs", "google"))] == ["FAIL"] * 7
    assert world.status("host api.anthropic.com") == "PASS" and world.status("disk") == "PASS"
    assert world.retrieve_calls == [] and world.google_get == [] and world.paid_calls == {"claude": 0, "image": 0, "tts": 0}
    assert [u for u, _ in world.get_urls if "/v1/" in u] == []
    assert ledger_rows() == []


# --- the 16-token Claude call --------------------------------------------------------------------

def test_claude_call_is_a_forced_tool_call_of_16_tokens_through_the_ledger(world, capsys):
    world.run(capsys)
    [call] = world.claude_calls
    assert call["model"] == config.CLAUDE_MODEL and call["max_tokens"] == 16
    assert call["tool_choice"] == {"type": "tool", "name": "ping"} and call["tools"][0]["name"] == "ping"
    [row] = ledger_rows()
    assert row["provider"] == "anthropic" and row["model"] == config.CLAUDE_MODEL
    assert row["units"] == {"input_tokens": 61, "output_tokens": 9}
    assert row["est_cost_eur"] == pytest.approx(ledger.claude_eur(config.CLAUDE_MODEL, 61, 9), abs=1e-6)
    assert 0 < row["est_cost_eur"] < 0.001


def test_a_200_cut_off_at_max_tokens_is_a_pass(world, capsys):
    world.claude_stop = "max_tokens"
    assert world.run(capsys) == 0
    assert world.status("claude call") == "PASS" and "stop_reason max_tokens" in world.detail("claude call")


def test_the_claude_client_does_not_retry_and_has_a_short_timeout(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", KEYS["ANTHROPIC_API_KEY"])
    client = preflight._anthropic_client()
    assert client.max_retries == 0
    assert client.timeout == config.PREFLIGHT_TIMEOUT_S


def test_llm_create_uses_the_given_client_and_still_the_module_client_by_default(monkeypatch):
    seen = []

    def create(label):
        def fn(**kwargs):
            seen.append((label, kwargs["model"], kwargs["max_tokens"]))
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        return fn

    monkeypatch.setattr(llm._client.messages, "create", create("module"))
    other = SimpleNamespace(messages=SimpleNamespace(create=create("given")))
    llm._create(max_tokens=5, messages=[])
    llm._create(client=other, max_tokens=6, messages=[])
    llm._create(5, other, max_tokens=7, messages=[])  # positional order est_in, client
    assert seen == [("module", config.CLAUDE_MODEL, 5), ("given", config.CLAUDE_MODEL, 6),
                    ("given", config.CLAUDE_MODEL, 7)]
    assert len(ledger_rows()) == 3, "a given client is budgeted and recorded like the module's"


def test_the_offline_guard_blocks_models_retrieve():
    """Even with the guard line gone this test must not reach the network: the client's transport fails
    the test instead of sending."""
    def unreachable(request):
        pytest.fail("reached the transport: the offline guard on Models.retrieve is gone")

    client = anthropic.Anthropic(api_key="offline-test", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(unreachable)))
    with pytest.raises(PaidCallInOfflineTest):
        client.models.retrieve("claude-sonnet-5")


class SocketOpened(BaseException):
    """Not an Exception: no retry loop or fallback may swallow it."""


@pytest.fixture
def sdk_transport(monkeypatch):
    """The real google-genai SDK building its OWN httpx client (so Client.close() really closes it, P0-8),
    with the fake below it, at httpx.HTTPTransport.handle_request. Yields a control: `.seen` is the
    requests that reached the transport, `.missing` the model names that answer 404, `.status` an
    override for every answer. A socket opened anywhere in the test fails it."""
    ctl = SimpleNamespace(seen=[], missing=set(), status=None, sockets=[])

    def no_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            ctl.sockets.append(address)
            raise SocketOpened(address)

    def handle_request(self, request):
        name = request.url.path.rsplit("/", 1)[-1]
        ctl.seen.append((request.method, request.url.path))
        status = ctl.status or (404 if name in ctl.missing else 200)
        body = {"name": f"models/{name}", "displayName": f"Display {name}", "supportedActions": ["generateContent"]} \
            if status == 200 else {"error": {"code": status, "message": f"models/{name} is unavailable", "status": "X"}}
        return httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json"})

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: no_connect(self, address))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "offline-test")
    yield ctl
    assert ctl.sockets == [], "the test opened a network connection"


def run_google_checks(capsys):
    preflight.check_google(preflight.Report())
    out = capsys.readouterr().out
    return [tuple(line.split(": ", 1)) for line in out.splitlines()]


def test_the_google_checks_work_with_the_real_sdk_and_its_own_http_client(real_google, sdk_transport, capsys):
    """P0-8 in preflight (P3-6 review C1): `google_client.make().models.get(...)` inline lost its Client to the
    garbage collector, whose __del__ closed the SDK's own HTTP client: 'Cannot send a request, as the client
    has been closed'. Every other test gave the SDK a client of its own making (which close() leaves alone) or
    replaced make() altogether."""
    lines = run_google_checks(capsys)
    assert lines == [("PASS google image model", f"{config.IMAGE_MODEL} exists (Display {config.IMAGE_MODEL})"),
                     ("PASS google veo model", f"{config.VEO_MODEL} exists (Display {config.VEO_MODEL})")]
    assert sdk_transport.seen == [("GET", f"/v1beta/models/{config.IMAGE_MODEL}"),
                                  ("GET", f"/v1beta/models/{config.VEO_MODEL}")]


def test_a_404_from_the_real_sdk_names_the_setting(real_google, sdk_transport, capsys):
    sdk_transport.missing = {config.VEO_MODEL}
    lines = run_google_checks(capsys)
    assert lines[0][0] == "PASS google image model"
    assert lines[1] == ("FAIL google veo model", f"model {config.VEO_MODEL} not found (404); fix VEO_MODEL in .env")


def test_one_google_attempt_really_means_one_request(real_google, sdk_transport, monkeypatch, capsys):
    """The shared client retries a 503 four times; the probe's per-request options must stop that."""
    monkeypatch.setattr(config, "RETRY_BASE_S", 0.001)
    monkeypatch.setattr(config, "RETRY_JITTER_S", 0.001)
    sdk_transport.status = 503
    lines = run_google_checks(capsys)
    assert [name for name, _ in lines] == ["FAIL google image model", "FAIL google veo model"]
    assert "503" in lines[0][1] and len(sdk_transport.seen) == 2, "one request for each of the two models"


# --- 3. --image ---------------------------------------------------------------------------------

def test_image_buys_one_picture_through_the_ledger_and_saves_it(world, capsys):
    assert world.run(capsys, "--image") == 0
    out = Path(config.BUILD_DIR) / "preflight" / "image.png"
    assert out.read_bytes() == b"\x89PNG fake picture"
    [(model, prompt, cfg)] = world.generate_calls
    assert model == config.IMAGE_MODEL and config.STYLES[config.STYLE]["still"] in prompt
    assert cfg.image_config.image_size == "1K" == config.IMAGE_SIZE
    assert world.status("image") == "PASS" and str(out) in world.detail("image")
    rows = [r for r in ledger_rows() if r["provider"] == "google"]
    assert len(rows) == 1 and rows[0]["units"] == {"images": 1}
    assert rows[0]["est_cost_eur"] == pytest.approx(ledger.image_eur())
    assert f"€{ledger.image_eur():.4f} recorded" in world.detail("image")


def test_image_is_never_cached_each_run_buys_one(world, capsys):
    world.run(capsys, "--image")
    world.run(capsys, "--image")
    assert len(world.generate_calls) == 2
    assert len([r for r in ledger_rows() if r["provider"] == "google"]) == 2
    out = Path(config.BUILD_DIR) / "preflight" / "image.png"
    out.unlink()
    world.run(capsys, "--image")  # a deleted picture is bought again
    assert len(world.generate_calls) == 3 and out.exists()


def test_image_billing_refusal_fails_with_the_error_redacted(world, capsys):
    world.image_error = genai_errors.ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": f"Quota exceeded for free tier; billing is not enabled for key {KEYS['GOOGLE_API_KEY']}"}})
    assert world.run(capsys, "--image") == 1
    assert world.status("image") == "FAIL"
    assert "429" in world.detail("image") and "free tier" in world.detail("image")
    assert KEYS["GOOGLE_API_KEY"] not in world.out + world.err and "***" in world.detail("image")
    assert [r for r in ledger_rows() if r["provider"] == "google"] == [], "a refused picture is not recorded"
    assert not (Path(config.BUILD_DIR) / "preflight" / "image.png").exists()


def test_image_without_a_picture_in_the_answer_fails(world, capsys, monkeypatch):
    monkeypatch.setattr(world, "generate_content", lambda **kw: SimpleNamespace(parts=[]))
    monkeypatch.setattr(google_client, "make", lambda: SimpleNamespace(models=SimpleNamespace(
        get=world.models_get, generate_content=world.generate_content)))
    assert world.run(capsys, "--image") == 1
    assert "no image" in world.detail("image")


def test_image_with_no_google_key_fails_by_name_and_buys_nothing(world, capsys, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    assert world.run(capsys, "--image") == 1
    assert "missing GOOGLE_API_KEY" in world.detail("image") and world.generate_calls == []


def test_image_makes_one_attempt_whatever_the_retry_setting(world, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(google_client, "make", lambda: (seen.append(config.MAX_RETRIES), SimpleNamespace(
        models=SimpleNamespace(get=world.models_get, generate_content=world.generate_content)))[1])
    world.run(capsys, "--image")
    assert seen == [config.MAX_RETRIES, config.MAX_RETRIES, 0], "the two free probes use the shared client; the picture 0 retries"
    assert config.MAX_RETRIES == 4, "put back afterwards"


# --- 4. --tts-sample ------------------------------------------------------------------------------

def test_tts_sample_makes_one_ledger_entry_writes_the_mp3_and_prints_the_pace(world, capsys):
    world.seconds = 14.0  # 28 words
    assert world.run(capsys, "--tts-sample") == 0
    [(url, body)] = world.posts
    assert body["text"] == voice.SAMPLE_LINE and body["model_id"] == config.TTS_MODEL
    assert KEYS["ELEVENLABS_VOICE_ID"] in url
    mp3 = voice.cache_path(voice.SAMPLE_LINE, Path(config.BUILD_DIR) / "preflight")
    assert mp3.read_bytes() == b"mp3 bytes"
    rows = [r for r in ledger_rows() if r["provider"] == "elevenlabs"]
    assert len(rows) == 1 and rows[0]["units"] == {"characters": len(voice.SAMPLE_LINE)}
    assert rows[0]["est_cost_eur"] == pytest.approx(ledger.tts_eur(len(voice.SAMPLE_LINE))) and rows[0]["est_cost_eur"] <= 0.02
    detail = world.detail("tts sample")
    assert str(mp3) in detail and "14.0 s" in detail and "28 words" in detail and "120 words per minute" in detail
    assert "tts pace" not in world.lines, "120 is inside 100-160"


@pytest.mark.parametrize("seconds, wpm", [(20.0, 84), (8.0, 210), (28 * 60 / 100.0, 100), (28 * 60 / 160.0, 160)])
def test_pace_outside_100_to_160_warns(world, capsys, seconds, wpm):
    world.seconds = seconds
    assert world.run(capsys, "--tts-sample") == 0, "a WARN is not a FAIL"
    assert f"{wpm} words per minute" in world.detail("tts sample")
    if wpm in (100, 160):
        assert "tts pace" not in world.lines, "the limits themselves are inside"
    else:
        assert world.status("tts pace") == "WARN" and f"{wpm} words per minute is outside 100-160" in world.detail("tts pace")
        assert world.lines["tts sample"][0][0] == "PASS"


def test_tts_sample_rerun_is_cached_and_buys_nothing(world, capsys):
    world.run(capsys, "--tts-sample")
    world.run(capsys, "--tts-sample")
    assert len(world.posts) == 1 and len([r for r in ledger_rows() if r["provider"] == "elevenlabs"]) == 1
    assert "cached, nothing bought" in world.detail("tts sample")
    voice.cache_path(voice.SAMPLE_LINE, Path(config.BUILD_DIR) / "preflight").unlink()
    world.run(capsys, "--tts-sample")  # a deleted file is bought again
    assert len(world.posts) == 2 and len([r for r in ledger_rows() if r["provider"] == "elevenlabs"]) == 2


def test_tts_sample_failure_is_a_fail_with_nothing_recorded_and_no_retry(world, capsys):
    world.post_status = 429
    assert world.run(capsys, "--tts-sample") == 1
    assert world.status("tts sample") == "FAIL" and "429" in world.detail("tts sample")
    assert len(world.posts) == 1, "a transient refusal is reported at once, not retried for minutes"
    assert [r for r in ledger_rows() if r["provider"] == "elevenlabs"] == []
    assert config.MAX_RETRIES == 4


def test_a_crash_after_the_purchase_is_recorded_and_the_rerun_makes_the_file(world, capsys, monkeypatch):
    """voice._buy records before it writes (D10): a failed write loses the file, never the record."""
    monkeypatch.setattr(world, "post", lambda url, headers, json, timeout: FakeResponse(200, content="not bytes"))
    monkeypatch.setattr(requests, "post", world.post)
    assert world.run(capsys, "--tts-sample") == 1
    folder = Path(config.BUILD_DIR) / "preflight"
    mp3 = voice.cache_path(voice.SAMPLE_LINE, folder)
    assert world.status("tts sample") == "FAIL" and not mp3.exists() and not list(folder.glob("*.partial-*"))
    assert len([r for r in ledger_rows() if r["provider"] == "elevenlabs"]) == 1, "the money was spent: recorded"
    monkeypatch.setattr(requests, "post", lambda url, headers, json, timeout: FakeResponse(200, content=b"mp3 bytes"))
    assert world.run(capsys, "--tts-sample") == 0
    assert mp3.read_bytes() == b"mp3 bytes" and "cached" not in world.detail("tts sample")
    assert len([r for r in ledger_rows() if r["provider"] == "elevenlabs"]) == 2


def test_tts_sample_with_the_real_length_reader(world, capsys, monkeypatch, media):
    """The file's length comes from ffprobe: a 3 s tone read as 28 words is 560 words per minute."""
    from utils import duration
    monkeypatch.setattr(preflight, "duration", duration)
    monkeypatch.setattr(world, "post", lambda url, headers, json, timeout: FakeResponse(
        200, content=(media / "n_long.mp3").read_bytes()))
    monkeypatch.setattr(requests, "post", world.post)
    assert world.run(capsys, "--tts-sample") == 0
    assert "3.0 s" in world.detail("tts sample") and "28 words" in world.detail("tts sample")
    assert world.status("tts pace") == "WARN"


def test_tts_sample_with_missing_voice_id_fails_by_name(world, capsys, monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", None)
    assert world.run(capsys, "--tts-sample") == 1
    assert "missing ELEVENLABS_VOICE_ID" in world.detail("tts sample") and world.posts == []


def test_both_paid_options_together(world, capsys):
    assert world.run(capsys, "--image", "--tts-sample") == 0
    assert world.paid_calls == {"claude": 1, "image": 1, "tts": 1}
    assert sorted(r["provider"] for r in ledger_rows()) == ["anthropic", "elevenlabs", "google"]
    total = sum(r["est_cost_eur"] for r in ledger_rows())
    assert total <= 0.10, "BACKLOG: the paid probes total at most EUR 0.10"
    assert f"spent €{total:.4f} this run" in world.out.splitlines()[-1]


# --- 5. no key in the output ---------------------------------------------------------------------

def test_a_key_in_a_provider_error_never_reaches_the_output(world, capsys):
    k = KEYS
    world.host["api.elevenlabs.io"] = requests.ConnectionError(f"failed for https://x/?key={k['ELEVENLABS_API_KEY']}")
    world.retrieve_error = RuntimeError(f"bad key {k['ANTHROPIC_API_KEY']}")
    world.claude_error = RuntimeError(f"bad key {k['ANTHROPIC_API_KEY']}")
    world.eleven["/v1/models"] = refusal(401, "invalid_api_key", f"key {k['ELEVENLABS_API_KEY']} is not valid")
    world.eleven["/v1/user/subscription"] = FakeResponse(500, text=f"oops {k['ELEVENLABS_API_KEY']}")
    world.google_error = {config.IMAGE_MODEL: RuntimeError(f"denied for {k['GOOGLE_API_KEY']}"),
                          config.VEO_MODEL: genai_errors.ClientError(403, {"error": {
                              "code": 403, "message": f"{k['GOOGLE_API_KEY']} rejected", "status": "DENIED"}})}
    world.image_error = RuntimeError(f"billing off for {k['GOOGLE_API_KEY']}")
    world.post_error = RuntimeError(f"voice refused {k['ELEVENLABS_API_KEY']} {k['ANTHROPIC_API_KEY']}")
    world.tools[("filter", "xfade")] = RuntimeError(f"weird {k['GOOGLE_API_KEY']}")
    assert world.run(capsys, "--image", "--tts-sample") == 1
    everything = world.out + world.err
    assert not [v for key, v in k.items() if key != "ELEVENLABS_VOICE_ID" and v in everything]
    assert everything.count("***") >= 8, "the errors are shown, redacted, not dropped"
    assert "bad key ***" in world.detail("claude model") and "invalid_api_key" in world.detail("elevenlabs models")


# --- 6. which calls are paid ----------------------------------------------------------------------

def test_without_flags_exactly_one_paid_call_the_16_token_claude_call(world, capsys):
    assert world.run(capsys) == 0
    assert world.paid_calls == {"claude": 1, "image": 0, "tts": 0}
    assert [c["max_tokens"] for c in world.claude_calls] == [16]
    assert len(ledger_rows()) == 1
    # and the free probes are all reads: GETs of models, one voice and the subscription
    assert world.posts == [] and world.generate_calls == []
    assert [m for m, _ in world.google_get] == [config.IMAGE_MODEL, config.VEO_MODEL]


def test_a_second_run_buys_one_more_claude_call_and_nothing_else(world, capsys):
    world.run(capsys)
    world.run(capsys)
    assert len(ledger_rows()) == 2 and world.paid_calls == {"claude": 2, "image": 0, "tts": 0}
    world.run(capsys, "--tts-sample")
    world.run(capsys)  # the flag left out: back to the free probes and the Claude call
    assert world.paid_calls == {"claude": 4, "image": 0, "tts": 1}


# --- invariants -----------------------------------------------------------------------------------

def test_a_spent_budget_fails_every_paid_probe_and_calls_nothing(world, capsys, monkeypatch):
    monkeypatch.setattr(config, "BUDGET_EUR", 0.0)
    assert world.run(capsys, "--image", "--tts-sample") == 1
    for check in ("claude call", "image", "tts sample"):
        assert world.status(check) == "FAIL" and "BUDGET_EUR" in world.detail(check), check
    assert world.paid_calls == {"claude": 0, "image": 0, "tts": 0}
    assert ledger_rows() == []
    free = [c for c in CHECKS if c != "claude call"]
    assert all(world.status(c) == "PASS" for c in free), "the free probes still run"


def test_the_budget_runs_out_between_the_paid_probes(world, capsys, monkeypatch):
    monkeypatch.setattr(config, "BUDGET_EUR", 0.07)  # the Claude call and the picture fit; the narration does not
    assert world.run(capsys, "--image", "--tts-sample") == 1
    assert world.status("claude call") == "PASS" == world.status("image")
    assert world.status("tts sample") == "FAIL" and "BUDGET_EUR" in world.detail("tts sample")
    assert world.paid_calls == {"claude": 1, "image": 1, "tts": 0}


def test_checks_catch_exception_only_so_the_offline_guard_and_ctrl_c_propagate(world, capsys):
    world.host["api.anthropic.com"] = PaidCallInOfflineTest("unmocked")
    with pytest.raises(PaidCallInOfflineTest):
        preflight.main([])
    world.host["api.anthropic.com"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        preflight.main([])


def test_nothing_is_written_outside_build_preflight_and_the_ledger(world, capsys, tmp_path):
    before = {p for p in tmp_path.rglob("*")}
    world.run(capsys, "--image", "--tts-sample")
    new = {p for p in tmp_path.rglob("*") if p not in before and p.is_file()}
    build = Path(config.BUILD_DIR)
    assert new, "something was written"
    assert all(p == build / "ledger.jsonl" or build / "preflight" in p.parents for p in new), sorted(new)
    assert not list((build / "preflight").glob("*.partial-*")), "no half-written file left"


def test_a_free_run_writes_only_the_ledger(world, capsys, tmp_path):
    world.run(capsys)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files == [Path(config.BUILD_DIR) / "ledger.jsonl"]


def test_a_failing_run_exits_one_and_a_clean_run_zero_and_the_summary_comes_last(world, capsys):
    assert world.run(capsys) == 0
    world.free_gb = 1
    assert world.run(capsys) == 1
    lines = world.out.splitlines()
    assert lines[-1].startswith("preflight: ") and "1 FAIL" in lines[-1] and "fix the FAIL lines" in lines[-1]
    assert not any(line.startswith("preflight:") for line in lines[:-1])


def test_the_sample_line_is_shared_with_the_opt_in_live_test():
    import test_live_optin
    assert not hasattr(test_live_optin, "VOICE_LINE")
    assert len(voice.SAMPLE_LINE.split()) == 28


# --- the real FFmpeg --------------------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_the_real_ffmpeg_passes_every_filter_and_x264_and_rejects_an_unknown_one():
    for name in preflight.FILTERS:
        assert preflight._filter(name)[0] == "PASS", name
    assert preflight._encoder(preflight.ENCODER)[0] == "PASS"
    status, detail = preflight._filter("nosuchfilter")
    assert status == "FAIL" and "Unknown filter" in detail
    assert preflight._encoder("nosuchencoder")[0] == "FAIL"


# --- P3-6 review W1: redact before you cut ----------------------------------------------------------

STRADDLE_KEY = "K3Y5TART-" + "a1b2c3d4e5f6g7h8i9j0" * 2 + "ZZ"  # 51 characters, as long as a real key
assert len(STRADDLE_KEY) == 51


def starts_to_show(text: str) -> bool:
    """True if any 8-character run of the key is in `text`: the partial key a cut-then-redact leaves."""
    return any(STRADDLE_KEY[i:i + 8] in text for i in range(len(STRADDLE_KEY) - 7))


def key_across(cut: int, lead: int = 0) -> str:
    """A text whose `cut`th character falls in the middle of the key (about 30 of its characters before it)."""
    return "A" * (cut - 30 - lead) + STRADDLE_KEY


def test_the_helper_would_see_a_partial_key():
    assert starts_to_show("xx" + STRADDLE_KEY[:20]) and not starts_to_show("xx" + STRADDLE_KEY[:7])


@pytest.mark.parametrize("site, set_up", [
    ("an ElevenLabs body that is not JSON (cut 200)",
     lambda w: w.eleven.update({"/v1/models": FakeResponse(500, text=key_across(200))})),
    ("a detail message (cut 200)",
     lambda w: w.eleven.update({"/v1/models": refusal(401, "x", key_across(200))})),
    ("a detail given as a string (cut 200)",
     lambda w: w.eleven.update({"/v1/models": FakeResponse(400, {"detail": key_across(200)})})),
    ("a detail status word (cut 60)",
     lambda w: w.eleven.update({"/v1/models": refusal(401, key_across(60), "m")})),
    ("an unexpected models answer (cut 100)",
     lambda w: w.eleven.update({"/v1/models": FakeResponse(200, {"error": key_across(100, lead=11)})})),
    ("a plan name (cut 40)",
     lambda w: w.eleven.update({"/v1/user/subscription": FakeResponse(200, {"tier": key_across(40)})})),
    ("an exception (cut 300)",
     lambda w: setattr(w, "retrieve_error", RuntimeError(key_across(300)))),
    ("FFmpeg's first line (cut 100)",
     lambda w: w.tools.update({("filter", "xfade"): "Unknown filter '" + key_across(100, lead=16) + "'.\n"})),
    ("manim's first line (cut 100)",
     lambda w: setattr(w, "manim", (1, "", key_across(100)))),
], ids=lambda v: v if isinstance(v, str) else "")
def test_a_key_straddling_a_cut_never_prints_even_its_first_characters(world, capsys, monkeypatch, site, set_up):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", STRADDLE_KEY)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", STRADDLE_KEY)
    set_up(world)
    world.run(capsys)
    assert not starts_to_show(world.out + world.err), site
    assert "***" in world.out or "FFmpeg's" in site or "manim's" in site


def test_the_tts_sample_surfaces_voice_py_errors_without_a_partial_key(world, capsys, monkeypatch):
    """voice.py cuts a transient error at 200 characters and any other at 500: redact() first."""
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", STRADDLE_KEY)
    for status, cut in ((429, 200), (400, 500)):
        world.post_status = status
        monkeypatch.setattr(world, "post", lambda url, headers, json, timeout, s=status, c=cut: FakeResponse(
            s, content=b"", text=key_across(c)))
        monkeypatch.setattr(requests, "post", world.post)
        assert world.run(capsys, "--tts-sample") == 1
        assert not starts_to_show(world.out + world.err), status
        assert str(status) in world.detail("tts sample")
        assert status != 429 or "***" in world.detail("tts sample"), "the detail shows 300 characters: the 500 cut is past that"


@pytest.mark.parametrize("status, cut", [(429, 200), (503, 200), (400, 500), (401, 500)])
def test_voice_narrate_redacts_a_key_before_it_cuts_the_error(monkeypatch, tmp_path, status, cut):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", STRADDLE_KEY)
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "voice-1")
    monkeypatch.setattr(config, "MAX_RETRIES", 0)
    monkeypatch.setattr(requests, "post", lambda url, headers, json, timeout: FakeResponse(
        status, content=b"", text=key_across(cut)))
    with pytest.raises(RuntimeError) as caught:  # retries.TransientError is a RuntimeError too
        voice.narrate("A calm line.", tmp_path)
    assert str(status) in str(caught.value) and "***" in str(caught.value)
    assert not starts_to_show(str(caught.value))


# --- P3-6 review W2: the free tier ----------------------------------------------------------------------

@pytest.mark.parametrize("tier", ["free", "Free", "FREE", "free_tier", "free_trial"])
def test_any_free_tier_name_warns(world, capsys, tier):
    world.eleven["/v1/user/subscription"] = FakeResponse(200, {"tier": tier, "character_count": 0, "character_limit": 10_000})
    assert world.run(capsys) == 0
    assert world.status("elevenlabs subscription") == "WARN"
    assert f"plan {tier}, 10,000 of 10,000 characters left" in world.detail("elevenlabs subscription")


@pytest.mark.parametrize("body", [{"character_count": 5, "character_limit": 100}, {"tier": None, "character_limit": 100},
                                  {"tier": "", "character_count": 5, "character_limit": 100}], ids=["absent", "null", "empty"])
def test_a_subscription_that_names_no_plan_warns(world, capsys, body):
    world.eleven["/v1/user/subscription"] = FakeResponse(200, body)
    assert world.run(capsys) == 0
    assert world.status("elevenlabs subscription") == "WARN"
    assert "names no plan" in world.detail("elevenlabs subscription")


@pytest.mark.parametrize("tier", ["starter", "creator", "pro", "scale", "business", "enterprise"])
def test_a_paid_tier_passes(world, capsys, tier):
    world.eleven["/v1/user/subscription"] = FakeResponse(200, {"tier": tier, "character_count": 1, "character_limit": 100})
    world.run(capsys)
    assert world.status("elevenlabs subscription") == "PASS" and f"plan {tier}," in world.detail("elevenlabs subscription")


# --- P3-6 review W3: a key without a permission ------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"detail": {"status": "missing_permissions", "message": "x"}},
    {"detail": {"code": "missing_permissions", "message": "x"}},
    {"detail": {"message": "The API key you used is missing the permission user_read to execute this operation."}},
    {"detail": "The API key you used is missing the permission user_read to execute this operation."},
], ids=["status", "code", "message", "string"])
def test_every_wording_of_a_missing_permission_gets_its_own_text(world, capsys, body):
    world.eleven["/v1/user/subscription"] = FakeResponse(401, body)
    assert world.run(capsys) == 1
    assert world.detail("elevenlabs subscription") == "key lacks user_read; TTS may still work"


def test_a_different_401_is_not_a_missing_permission(world, capsys):
    world.eleven["/v1/user/subscription"] = refusal(401, "invalid_api_key", "Invalid API key")
    world.run(capsys)
    assert world.detail("elevenlabs subscription") == "HTTP 401 invalid_api_key: Invalid API key"


def test_a_missing_permission_text_on_another_status_is_not_that_failure(world, capsys):
    world.eleven["/v1/user/subscription"] = refusal(500, "missing_permissions", "x")
    world.run(capsys)
    assert world.detail("elevenlabs subscription").startswith("HTTP 500")


# --- review suggestions ---------------------------------------------------------------------------------

def test_an_empty_stdout_shows_the_first_line_of_stderr(world, capsys):
    world.tool_calls.clear()
    original = world.tool

    def tool(cmd):
        if cmd[-1] == "filter=xfade":
            return 1, "", "\nffmpeg: error while loading shared libraries: libx.so\nmore\n"
        if cmd[-1] == "encoder=libx264":
            return 1, "  \n", ""
        return original(cmd)
    world.mp.setattr(preflight, "_tool", tool)
    assert world.run(capsys) == 1
    assert "(ffmpeg: error while loading shared libraries: libx.so)" in world.detail("ffmpeg filter xfade")
    assert "(no output)" in world.detail("ffmpeg encoder libx264")


def test_help_quotes_the_prices_from_the_ledgers_own_functions(capsys, monkeypatch):
    with pytest.raises(SystemExit):
        preflight.main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert f"one {config.IMAGE_SIZE} picture (~EUR {ledger.image_eur():.3f} on {config.IMAGE_MODEL})" in text
    assert f"~EUR {ledger.tts_eur(len(voice.SAMPLE_LINE)):.3f} on {config.TTS_MODEL}" in text
    monkeypatch.setitem(config.IMAGE_USD_PER_IMAGE, "4K", 0.5)
    monkeypatch.setattr(config, "IMAGE_SIZE", "4K")
    with pytest.raises(SystemExit):
        preflight.main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert f"one 4K picture (~EUR {0.5 * config.USD_TO_EUR:.3f} on {config.IMAGE_MODEL})" in text
    assert "0.062" not in text
