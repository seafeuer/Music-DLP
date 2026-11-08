"""Version bump, build, smoke test, checksums, and changelog in one run.

The release steps used to live in someone's memory: change the version in
two files and the README, rebuild, start the exe once, write a checksum,
and rename the changelog heading. Any of those could be skipped silently.
This script does them in order and refuses to call a release ready when a
step fails.

Usage:
    python release.py --version 2.12.0
    python release.py --version 2.12.0 --skip-tests
    python release.py --no-build          # checksums + changelog only

The smoke test runs the packaged exe on a temporary home, with the real
%APPDATA% hidden, so it cannot touch the user's library or Spotify panel.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
INIT = ROOT / "musicdlp" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
DIST = ROOT / "dist" / "MusicDLP"
EXE = DIST / "MusicDLP.exe"
CHECKSUMS = DIST / "SHA256SUMS.txt"


def current_version() -> str:
    sys.path.insert(0, str(ROOT))
    from musicdlp import __version__

    return __version__


def bump_init(text: str, version: str) -> str:
    """Rewrite ``__version__ = "..."``, keeping its comment and position."""
    import re

    pattern = re.compile(r'(__version__\s*=\s*")[^"]*(")')
    if not pattern.search(text):
        raise SystemExit("Could not find __version__ in musicdlp/__init__.py.")
    return pattern.sub(rf"\g<1>{version}\g<2>", text, count=1)


def bump_pyproject(text: str, version: str) -> str:
    import re

    pattern = re.compile(r'(?m)^(version\s*=\s*")[^"]*(")')
    if not pattern.search(text):
        raise SystemExit("Could not find the project version in pyproject.toml.")
    return pattern.sub(rf"\g<1>{version}\g<2>", text, count=1)


def bump_readme(text: str, version: str) -> str:
    import re

    pattern = re.compile(r"(\*\*Version )([^*]+)( — working\.\*\*)")
    if not pattern.search(text):
        raise SystemExit("Could not find the README status line.")
    return pattern.sub(rf"\g<1>{version}\g<3>", text, count=1)


def bump_changelog(text: str, version: str, today: Optional[str] = None) -> str:
    """Put this version at the top, keeping the old entries below it."""
    today = today or date.today().isoformat()
    heading = f"## {version} — {today}"
    if heading in text:
        return text
    if "## Unreleased" in text:
        return text.replace("## Unreleased", heading, 1)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## "):
            lines.insert(index, heading)
            lines.insert(index + 1, "")
            lines.insert(index + 2, "- ")
            lines.insert(index + 3, "")
            return "\n".join(lines) + "\n"
    return text.rstrip() + f"\n\n{heading}\n\n- \n"


def set_version(version: str) -> None:
    for path, update in (
        (INIT, bump_init),
        (PYPROJECT, bump_pyproject),
        (README, bump_readme),
        (CHANGELOG, bump_changelog),
    ):
        text = path.read_text(encoding="utf-8")
        path.write_text(update(text, version), encoding="utf-8")
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"Updated {shown}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(app_dir: Path = DIST) -> Path:
    """A SHA256 file for every file a download contains."""
    lines = []
    for path in sorted(app_dir.rglob("*")):
        if not path.is_file() or path.name == CHECKSUMS.name:
            continue
        relative = path.relative_to(app_dir).as_posix()
        lines.append(f"{sha256(path)}  {relative}")
    target = app_dir / CHECKSUMS.name
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {target} ({len(lines)} file(s))")
    return target


def write_archive(app_dir: Path = DIST, version: Optional[str] = None) -> Path:
    """The zip a user downloads: one top-level MusicDLP folder.

    GitHub release assets are files, not folders. Zipping the built folder
    with its own name at the root means extracting it produces exactly the
    layout INSTALL.txt describes, instead of loose files in Downloads.
    """
    version = version or current_version()
    target = app_dir.parent / f"MusicDLP-{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path(app_dir.name) / path.relative_to(app_dir))
    print(f"Wrote {target} ({target.stat().st_size // (1024 * 1024)} MB)")
    return target


def _smoke_home() -> Path:
    """An isolated home with the wizard already done and the bridge off."""
    home = Path(tempfile.mkdtemp(prefix="musicdlp-release-"))
    (home / "data").mkdir(parents=True)
    (home / "data" / "config.json").write_text(
        '{\n  "first_run_done": true,\n  "bridge_enabled": false,\n'
        '  "notifications": false\n}\n',
        encoding="utf-8",
    )
    return home


def smoke_test(app_dir: Path = DIST, timeout: float = 40.0) -> str:
    """Start the packaged exe and wait for its first log line.

    A windowed build has no console: if it dies before logging, the user
    sees nothing. Running it once here turns that into a failed release.
    """
    exe = app_dir / "MusicDLP.exe"
    if not exe.is_file():
        raise SystemExit(f"The packaged exe is missing: {exe}")

    home = _smoke_home()
    appdata = home / "appdata"
    appdata.mkdir()
    env = os.environ.copy()
    env["MUSICDLP_HOME"] = str(home)
    env["APPDATA"] = str(appdata)
    env["LOCALAPPDATA"] = str(appdata)
    # The single-instance socket is named after USERNAME; a distinct one
    # keeps the smoke run from being mistaken for the user's open app.
    env["USERNAME"] = "MusicDLPReleaseTest"

    log = home / "local" / "logs" / "musicdlp.log"
    process = subprocess.Popen(
        [str(exe)],
        env=env,
        cwd=str(app_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if log.is_file():
                text = log.read_text(encoding="utf-8", errors="replace")
                if "Starting Music DLP" in text:
                    break
            if process.poll() is not None:
                raise SystemExit(
                    "The packaged app exited before it finished starting. "
                    f"See {log}."
                )
            time.sleep(0.25)
        else:
            raise SystemExit(
                f"The packaged app did not start within {timeout:.0f}s. "
                f"See {log}."
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(home, ignore_errors=True)

    print("Smoke test: the packaged app started and logged its version.")
    return text


def run_tests() -> None:
    print("Running the Python test suite...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q"],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise SystemExit("Tests failed; no release was made.")

    node = shutil.which("node")
    if node:
        print("Running the panel tests...")
        result = subprocess.run(
            [node, "--test", "spicetify/musicdlp-panel/test/panel.test.mjs"],
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            raise SystemExit("Panel tests failed; no release was made.")


def build() -> None:
    sys.path.insert(0, str(ROOT))
    import build as build_module

    print("Building dist/MusicDLP...")
    build_module.build()


def main() -> int:
    parser = argparse.ArgumentParser(description="Cut a Music DLP release")
    parser.add_argument("--version", help="new version, e.g. 2.12.0")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--no-build", action="store_true",
                        help="checksums and changelog only")
    args = parser.parse_args()

    if args.version:
        version = args.version
    else:
        version = current_version()
        print(f"Releasing the current version {version}.")

    # A failed test, build or smoke test must not leave the repository
    # half-bumped with a changelog entry for a release that never existed.
    originals = {
        path: path.read_text(encoding="utf-8")
        for path in (INIT, PYPROJECT, README, CHANGELOG)
    }
    bumped = False
    try:
        if not args.skip_tests:
            run_tests()

        if args.version:
            # Mark this before the write: a failure halfway through
            # set_version must still restore every file it touched.
            bumped = True
            set_version(args.version)

        if not args.no_build and not args.skip_build:
            build()
            smoke_test()
            write_checksums()
            write_archive(DIST, version)
        elif DIST.is_dir():
            write_checksums()
            write_archive(DIST, version)
        else:
            print("No dist folder; skipped checksums.")
    except BaseException:
        if bumped:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
            print("Restored the version files; no release was made.")
        raise

    print(f"\nMusic DLP {version} is ready in {DIST}.")
    print("Upload the zip next to dist/ as the release asset; dist/ is gitignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())