"""Shared pytest fixtures: one offscreen Qt application plus store/link builders."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402
from qtpy import QtCore, QtWidgets  # noqa: E402

from tests.helpers import pump, signal_args, signal_count  # noqa: E402,F401  (re-exported for tests)


@pytest.fixture(scope="session")
def qapp() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture()
def store(qapp):
    from uclf.results import ResultStore

    return ResultStore()


@pytest.fixture()
def link(qapp):
    from uclf.links import SimulatedLink

    return SimulatedLink()


@pytest.fixture()
def settings(qapp, tmp_path):
    """A throwaway ini-file QSettings, fresh for every test."""
    return QtCore.QSettings(str(tmp_path / "uclf.conf"), QtCore.QSettings.Format.IniFormat)
