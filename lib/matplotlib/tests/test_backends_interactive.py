import importlib
import os
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

import matplotlib as mpl


# Minimal smoke-testing of the backends for which the dependencies are
# PyPI-installable on Travis.  They are not available for all tested Python
# versions so we don't fail on missing backends.

def _get_testable_interactive_backends():
    backends = []
    # gtk3agg fails on Travis, needs to be investigated.
    for deps, backend in [  # (["cairocffi", "pgi"], "gtk3agg"),
                          (["cairocffi", "pgi"], "gtk3cairo"),
                          (["PyQt5"], "qt5agg"),
                          (["tkinter"], "tkagg"),
                          (["wx"], "wx"),
                          (["wx"], "wxagg")]:
        reason = None
        if not os.environ.get("DISPLAY"):
            reason = "No $DISPLAY"
        elif any(importlib.util.find_spec(dep) is None for dep in deps):
            reason = "Missing dependency"
        elif "wx" in deps and sys.platform == "darwin":
            reason = "wx backends known not to work on OSX"
        backends.append(pytest.mark.skip(reason=reason)(backend) if reason
                        else backend)
    return backends


# Using a timer not only allows testing of timers (on other backends), but is
# also necessary on gtk3 and wx, where a direct call to key_press_event("q")
# from draw_event causes breakage due to the canvas widget being deleted too
# early.  Also, gtk3 redefines key_press_event with a different signature, so
# we directly invoke it from the superclass instead.
_test_script = """\
import importlib
import sys
from unittest import TestCase

import matplotlib as mpl
from matplotlib import pyplot as plt, rcParams
from matplotlib.backend_bases import FigureCanvasBase
rcParams.update({
    "webagg.open_in_browser": False,
    "webagg.port_retries": 1,
})
backend = plt.rcParams["backend"].lower()
assert_equal = TestCase().assertEqual
assert_raises = TestCase().assertRaises

if backend.endswith("agg") and not backend.startswith(("gtk3", "web")):
    # Force interactive framework setup.
    plt.figure()

    # Check that we cannot switch to a backend using another interactive
    # framework, but can switch to a backend using cairo instead of agg, or a
    # non-interactive backend.  In the first case, we use tkagg as the "other"
    # interactive backend as it is (essentially) guaranteed to be present.
    # Moreover, don't test switching away from gtk3 as Gtk.main_level() is
    # not set up at this point yet, and webagg, which uses no interactive
    # framework.

    if backend != "tkagg":
        with assert_raises(ImportError):
            mpl.use("tkagg", force=True)

    def check_alt_backend(alt_backend):
        mpl.use(alt_backend, force=True)
        fig = plt.figure()
        assert_equal(
            type(fig.canvas).__module__,
            "matplotlib.backends.backend_{}".format(alt_backend))

    if importlib.util.find_spec("cairocffi"):
        check_alt_backend(backend[:-3] + "cairo")
    check_alt_backend("svg")

mpl.use(backend, force=True)

fig, ax = plt.subplots()
assert_equal(
    type(fig.canvas).__module__,
    "matplotlib.backends.backend_{}".format(backend))

ax.plot([0, 1], [2, 3])

timer = fig.canvas.new_timer(1)
timer.add_callback(FigureCanvasBase.key_press_event, fig.canvas, "q")
# Trigger quitting upon draw.
fig.canvas.mpl_connect("draw_event", lambda event: timer.start())

plt.show()
"""
_test_timeout = 10  # Empirically, 1s is not enough on Travis.


@pytest.mark.parametrize("backend", _get_testable_interactive_backends())
@pytest.mark.flaky(reruns=3)
def test_interactive_backend(backend):
    if subprocess.run([sys.executable, "-c", _test_script],
                      env={**os.environ, "MPLBACKEND": backend},
                      timeout=_test_timeout).returncode:
        pytest.fail("The subprocess returned an error.")


@pytest.mark.skipif(os.name == "nt", reason="Cannot send SIGINT on Windows.")
def test_webagg():
    pytest.importorskip("tornado")
    proc = subprocess.Popen([sys.executable, "-c", _test_script],
                            env={**os.environ, "MPLBACKEND": "webagg"})
    url = "http://{}:{}".format(
        mpl.rcParams["webagg.address"], mpl.rcParams["webagg.port"])
    timeout = time.perf_counter() + _test_timeout
    while True:
        try:
            conn = urllib.request.urlopen(url)
            break
        except urllib.error.URLError:
            if time.perf_counter() > timeout:
                pytest.fail("Failed to connect to the webagg server.")
            else:
                continue
    conn.close()
    proc.send_signal(signal.SIGINT)
    assert proc.wait(timeout=_test_timeout) == 0
