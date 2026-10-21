"""Shared test configuration."""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Never let a test touch the real user configuration, library, or logs.
# This must be set before anything imports musicdlp.core.paths.
_home = tempfile.mkdtemp(prefix="musicdlp-tests-")
os.environ["MUSICDLP_HOME"] = _home
atexit.register(shutil.rmtree, _home, True)
