"""`make smoke` runs the smoke film only, never an opt-in paid check (P1-13 review: with a
module-wide `live` mark, `-m live` also ran the Veo clip and the voice A/B). Read from the
Makefile, so this checks what `make smoke` really selects."""
import os
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPT_IN = {"live_veo": "tests/test_live_optin.py::test_veo_model_makes_one_clip",
          "live_voice": "tests/test_live_optin.py::test_voice_models_side_by_side"}


def smoke_commands() -> list[list[str]]:
    """Every pytest command in the Makefile's `smoke` recipe, as its arguments."""
    lines = open(os.path.join(ROOT, "Makefile")).read().splitlines()
    recipe = []
    for line in lines[lines.index("smoke:") + 1:]:
        if not line.startswith("\t"):
            break
        recipe.append(shlex.split(line))
    return [cmd[cmd.index("pytest") + 1:] for cmd in recipe if "pytest" in cmd]


def collect(args: list[str]) -> set[str]:
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", *args],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, f"collection failed: {result.stdout[-800:]} {result.stderr[-800:]}"
    return {line for line in result.stdout.splitlines() if "::" in line}


def collected(marker: str) -> set[str]:
    return collect(["-q", "-m", marker, "tests"])


def test_make_smoke_selects_no_opt_in_paid_check():
    commands = smoke_commands()
    assert commands, "no pytest command under `smoke:` in the Makefile"
    smoke = set().union(*(collect(args) for args in commands))
    assert smoke, "make smoke selects nothing"
    assert not smoke & set(OPT_IN.values()), f"make smoke would also run {smoke & set(OPT_IN.values())}"
    assert all(test.startswith("tests/test_live.py::") for test in smoke)


def test_each_opt_in_check_runs_by_its_own_marker():
    for marker, test in OPT_IN.items():
        assert collected(marker) == {test}
