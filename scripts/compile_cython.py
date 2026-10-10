"""Build a compiled copy of the backend for on-premise releases.

    python scripts/compile_cython.py --out /path/to/build-dir

Copies the backend into --out (never touching this source tree), compiles
every module under apps/ and config/ to native extensions with Cython
(.so on Linux, .pyd on Windows), then deletes the .py source of each module
that compiled — and only those. Kept as .py: package __init__.py files
(Python needs them to find packages) and Django migrations (schema only, and
their numeric names aren't valid Cython module names). Tests are removed.

Compiled files only run on the OS, CPU and Python version they were built
with: build on the same platform as the hospital server.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
PACKAGES = ("apps", "config")
COPY_IGNORE = shutil.ignore_patterns(
    ".git", "venv", ".venv", "htmlcov", "media", "staticfiles", "__pycache__", "*.pyc", "*.sqlite3",
    ".env", ".pytest_cache", "build", "*.so", "*.pyd", "*.c",
    # Platform-side and docs only: never shipped to a hospital.
    "tools", "docs", "deploy",
)
# Platform-side tooling that has no business on a customer's server.
PLATFORM_ONLY = ("apps/licensing/management/commands/generate_license_keypair.py",)
LEFTOVERS = ("conftest.py", "pytest.ini", "generate_collection.py", "onboard_demo_hospitals.py")


def is_test(path: Path) -> bool:
    return path.name in ("tests.py", "conftest.py") or path.name.startswith("test_") or "tests" in path.parts


def is_migration(path: Path) -> bool:
    return "migrations" in path.parts


def to_compile(root: Path):
    for package in PACKAGES:
        for path in sorted((root / package).rglob("*.py")):
            rel = path.relative_to(root)
            if path.name == "__init__.py" or is_test(rel) or is_migration(rel) or rel.as_posix() in PLATFORM_ONLY:
                continue
            yield rel


def compiled_sibling(path: Path):
    for ext in (".so", ".pyd"):
        found = list(path.parent.glob(f"{path.stem}.*{ext}"))
        if found:
            return found[0]
    return None


def compile_in(root: Path, jobs: int):
    """Runs the Cython build inside `root` (a separate process, cwd=root)."""
    modules = [p.as_posix() for p in to_compile(root)]
    code = f"""
import sys
from Cython.Build import cythonize
from setuptools import Extension, setup
mods = {modules!r}
setup(
    name="hms-backend",
    ext_modules=cythonize(
        [Extension(m[:-3].replace("/", "."), [m]) for m in mods],
        nthreads={jobs},
        compiler_directives={{"language_level": "3", "binding": True, "always_allow_keywords": True, "annotation_typing": False}},
    ),
    script_args=["build_ext", "--inplace", "-j", "{jobs}"],
)
"""
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)


def strip(root: Path):
    """Delete source from the build copy — only for modules that compiled."""
    root = root.resolve()
    if root == SOURCE or (root / ".git").exists():
        sys.exit(f"Refusing to strip {root}: that is a source tree, not a build copy.")
    missing = [p for p in to_compile(root) if compiled_sibling(root / p) is None]
    if missing:
        sys.exit("Not stripping: these modules have no compiled version:\n  " + "\n  ".join(map(str, missing[:20])))

    removed = 0
    for rel in to_compile(root):
        (root / rel).unlink()
        removed += 1
    for package in PACKAGES:
        for path in list((root / package).rglob("*")):
            if path.is_file() and (path.suffix == ".c" or (path.suffix == ".py" and is_test(path.relative_to(root)))):
                path.unlink()
    for rel in PLATFORM_ONLY + LEFTOVERS:
        (root / rel).unlink(missing_ok=True)
    shutil.rmtree(root / "build", ignore_errors=True)
    shutil.rmtree(root / "scripts", ignore_errors=True)
    print(f"Compiled build ready in {root} ({removed} modules compiled, source removed).")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="Empty or new folder for the compiled build (outside this project).")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--on-premise", action="store_true", help="Pin the build to on-premise mode (config/build_info.py).")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out == SOURCE or SOURCE in out.parents:
        sys.exit("--out must be outside the project folder.")
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty. Give a new or empty folder.")

    shutil.copytree(SOURCE, out, ignore=COPY_IGNORE, dirs_exist_ok=True)
    if args.on_premise:
        (out / "config" / "build_info.py").write_text('"""Fixed at build time."""\n\nFORCED_DEPLOYMENT_MODE = "on_premise"\n')
    compile_in(out, args.jobs)
    strip(out)


if __name__ == "__main__":
    main()
