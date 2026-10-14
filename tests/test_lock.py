"""P1-11: requirements.lock pins the exact versions `make check` is green with. The tests fail
if the lock is malformed, if it no longer satisfies the ranges in requirements*.txt, or if what
is installed differs from it (a new SDK release could silently break the calls the tests mock).
Offline and fast: only reads files and installed-package metadata."""
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements.lock"
RANGES = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt"]

FIX = (
    "To get back to the tested set: pip install -r requirements.lock\n"
    "If the new versions are intentional: run `make check` with them, then regenerate "
    "requirements.lock (command in its header) and commit it."
)


def requirement_lines(text: str):
    """(line number, text) of every requirement line: comments and blank lines dropped."""
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if line:
            yield n, line


def parse_lock(text: str) -> dict[str, str]:
    """{normalised name: version}. Every line must be an exact `name==version` pin: an editable
    install, a URL or a range would let the environment drift, so they are errors."""
    pins: dict[str, str] = {}
    for n, line in requirement_lines(text):
        try:
            req = Requirement(line)
        except InvalidRequirement as e:
            raise ValueError(f"requirements.lock line {n}: {line!r} is not a requirement ({e})") from e
        specs = list(req.specifier)
        if req.url or req.extras or req.marker or len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
            raise ValueError(f"requirements.lock line {n}: {line!r} must be a plain name==version pin")
        name = canonicalize_name(req.name)
        if name in pins:
            raise ValueError(f"requirements.lock line {n}: {req.name} is pinned twice ({pins[name]} and {specs[0].version})")
        pins[name] = specs[0].version
    return pins


def installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def drift(pins: dict[str, str], installed=installed_version) -> list[str]:
    """One line per pinned package whose installed version is not the pinned one."""
    problems = []
    for name, wanted in sorted(pins.items()):
        have = installed(name)
        if have is None:
            problems.append(f"  {name}: lock has {wanted}, not installed")
        elif have != wanted:
            problems.append(f"  {name}: lock has {wanted}, installed {have}")
    return problems


def unmet(pins: dict[str, str], requirements_text: str) -> list[str]:
    """One line per requirement (applicable here) that the lock doesn't pin, or pins outside its range."""
    problems = []
    for _, line in requirement_lines(requirements_text):
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate():
            continue
        wanted = pins.get(canonicalize_name(req.name))
        if wanted is None:
            problems.append(f"  {line}: not in the lock")
        elif not req.specifier.contains(wanted, prereleases=True):
            problems.append(f"  {line}: the lock pins {wanted}, outside that range")
    return problems


@pytest.fixture(scope="module")
def pins() -> dict[str, str]:
    return parse_lock(LOCK.read_text())


def test_lock_parses_as_exact_pins(pins):
    assert pins, "requirements.lock pins nothing"


@pytest.mark.parametrize("path", RANGES, ids=lambda p: p.name)
def test_lock_satisfies_the_requirement_ranges(pins, path):
    problems = unmet(pins, path.read_text())
    assert not problems, (
        f"requirements.lock does not satisfy {path.name}:\n" + "\n".join(problems)
        + "\nRegenerate the lock (command in its header) or fix the range."
    )


def test_installed_versions_match_the_lock(pins):
    problems = drift(pins)
    assert not problems, (
        f"{len(problems)} installed package(s) differ from requirements.lock:\n" + "\n".join(problems)
        + "\n" + FIX
    )


# --- the checks themselves, on made-up data -------------------------------------------------

def test_drift_names_each_package_that_differs():
    pins = parse_lock("# header\nanthropic==1.8.0\nDocstring_Parser==0.18.0  # name spelling differs\nmissing-pkg==1.0\nsame==2.0\n")
    have = {"anthropic": "1.9.0", "docstring-parser": "0.18.0", "same": "2.0"}
    assert drift(pins, have.get) == [
        "  anthropic: lock has 1.8.0, installed 1.9.0",
        "  missing-pkg: lock has 1.0, not installed",
    ]


@pytest.mark.parametrize("line", [
    "anthropic>=1.8",
    "anthropic~=1.8.0",
    "anthropic==1.*",
    "anthropic==1.8.0,<2",
    "anthropic",
    "-e git+https://example.com/x.git#egg=x",
    "x @ file:///tmp/x.whl",
    "anthropic[bedrock]==1.8.0",
    "anthropic==1.8.0; sys_platform == 'linux'",
])
def test_lock_rejects_anything_but_an_exact_pin(line):
    with pytest.raises(ValueError, match="requirements.lock line 1"):
        parse_lock(line)


def test_lock_rejects_a_package_pinned_twice():
    with pytest.raises(ValueError, match="pinned twice"):
        parse_lock("a-b==1.0\na_b==2.0\n")


def test_unmet_reports_missing_and_out_of_range_pins():
    pins = parse_lock("anthropic==1.8.0\nrequests==2.34.2\n")
    text = "anthropic>=1.9  # newer\nrequests>=2.31\npytest>=8\nonly-on-windows>=1; sys_platform == 'win32'\n"
    assert unmet(pins, text) == [
        "  anthropic>=1.9: the lock pins 1.8.0, outside that range",
        "  pytest>=8: not in the lock",
    ]
