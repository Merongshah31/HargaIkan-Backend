"""Smoke tests for the repository scaffold (task 1.6).

Covers pinned requirements, gitignored secret files, placeholder-only example
secret files, the pure-layer import/I-O rule and the home page legend.
Requirements: 1.3, 1.6, 1.7, 22.7.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "hargaikan"

FORBIDDEN_IMPORTS = {"supabase", "pinecone", "requests", "streamlit"}
FORECAST_PURE_MODULES = ("features.py", "baseline.py", "model.py", "evaluate.py")
PIPELINE_PURE_MODULES = ("cleaning.py", "bands.py")
SOURCE_LABELS = ("Harga sejarah", "Input pengguna", "Anggaran model", "Anggaran perjalanan")


# --------------------------------------------------------------------------- requirements


REQUIREMENT_FILES = ("requirements.txt", "requirements-backend.txt")


def _requirement_lines(filename: str = "requirements.txt") -> list[str]:
    lines = []
    for raw in (ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()  # drop trailing inline comments
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        lines.append(line)
    return lines


@pytest.mark.parametrize("filename", REQUIREMENT_FILES)
def test_requirements_file_has_packages(filename: str) -> None:
    assert _requirement_lines(filename), f"{filename} lists no packages"


@pytest.mark.parametrize(
    "filename,line", [(f, line) for f in REQUIREMENT_FILES for line in _requirement_lines(f)]
)
def test_requirement_is_pinned_exactly(filename: str, line: str) -> None:
    # name[extras]==version, optionally followed by an environment marker.
    pattern = r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.+!_-]+(\s*;.*)?$"
    assert re.match(pattern, line), f"{filename}: requirement not pinned with ==: {line!r}"


def _req_name(line: str) -> str:
    return re.split(r"[\[=]", line, maxsplit=1)[0].lower().replace("_", "-")


def test_backend_requirements_exclude_ui_and_ml_packages() -> None:
    names = {_req_name(line) for line in _requirement_lines("requirements-backend.txt")}
    assert not names & {"streamlit", "torch", "sentence-transformers", "pinecone", "scikit-learn", "altair", "pydeck"}


def test_backend_requirements_match_main_pins() -> None:
    """Shared packages use the same version in both files."""
    main = {_req_name(line): line.split("==", 1)[1] for line in _requirement_lines("requirements.txt")}
    for line in _requirement_lines("requirements-backend.txt"):
        name, version = _req_name(line), line.split("==", 1)[1]
        if name in main:
            assert main[name] == version, f"{name}: {version} != {main[name]}"


# --------------------------------------------------------------------------- .gitignore


@pytest.mark.parametrize("entry", [".env", ".streamlit/secrets.toml", "data/raw/"])
def test_gitignore_lists_secret_and_raw_paths(entry: str) -> None:
    entries = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert entry in entries, f".gitignore is missing {entry!r}"


# --------------------------------------------------------------------------- example secrets

REAL_KEY_PATTERNS = {
    "JWT-like token": re.compile(r"eyJ[A-Za-z0-9_\-+/=]{20,}"),
    "sk- style key": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "pcsk_ style key": re.compile(r"pcsk_[A-Za-z0-9_\-]{20,}"),
    "real Supabase project URL": re.compile(r"https://[a-z0-9]{20}\.supabase\.co"),
}
EXAMPLE_FILES = [".env.example", ".streamlit/secrets.toml.example"]


@pytest.mark.parametrize("rel_path", EXAMPLE_FILES)
def test_example_file_exists(rel_path: str) -> None:
    assert (ROOT / rel_path).is_file()


@pytest.mark.parametrize("rel_path", EXAMPLE_FILES)
@pytest.mark.parametrize("kind", list(REAL_KEY_PATTERNS))
def test_example_file_has_no_real_looking_keys(rel_path: str, kind: str) -> None:
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    match = REAL_KEY_PATTERNS[kind].search(text)
    # Report only the kind and position, never the matched value.
    assert match is None, f"{rel_path} contains a {kind} at offset {match.start() if match else -1}"


# --------------------------------------------------------------------------- pure-layer AST rule


def _pure_module_files() -> list[Path]:
    files: list[Path] = []
    core = PKG / "core"
    if core.is_dir():
        files.extend(p for p in core.rglob("*.py") if "__pycache__" not in p.parts)
    files.extend(p for name in FORECAST_PURE_MODULES if (p := PKG / "forecast" / name).is_file())
    files.extend(p for name in PIPELINE_PURE_MODULES if (p := PKG / "pipeline" / name).is_file())
    return sorted(files)


PURE_FILES = _pure_module_files()


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_pure_module_scan_is_not_vacuous() -> None:
    assert (PKG / "core" / "__init__.py").is_file()
    assert PKG / "core" / "__init__.py" in PURE_FILES


@pytest.mark.parametrize("path", PURE_FILES, ids=_rel)
def test_pure_module_has_no_forbidden_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        offenders += [f"line {node.lineno}: {n}" for n in names if n.split(".")[0] in FORBIDDEN_IMPORTS]
    assert not offenders, f"{_rel(path)} imports I/O libraries: {offenders}"


@pytest.mark.parametrize("path", PURE_FILES, ids=_rel)
def test_pure_module_does_not_call_open(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
    ]
    assert not calls, f"{_rel(path)} calls builtin open() on lines {calls}"


# --------------------------------------------------------------------------- home page


def test_home_page_runs_and_shows_four_source_labels() -> None:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, [e.message for e in at.exception]
    rendered = "\n".join(md.value for md in at.markdown)
    missing = [label for label in SOURCE_LABELS if label not in rendered]
    assert not missing, f"home page legend is missing labels: {missing}"
