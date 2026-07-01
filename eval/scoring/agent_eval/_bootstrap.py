"""Auto-activate the eval harness venv if available.

Imported by __init__.py before any third-party deps. Uses only stdlib.

For script invocations (python3 script.py), replaces the process with
the venv python via os.execv(). For inline code (python3 -c "..."),
patches sys.path so third-party imports resolve from the venv.
"""
import glob
import os
import sys


def _activate():
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_root = os.path.dirname(pkg_dir)
    venv_dir = os.path.join(plugin_root, '.eval-venv')
    venv_python = os.path.join(venv_dir, 'bin', 'python3')
    if not os.path.isfile(venv_python):
        # uv venvs may use versioned name (e.g., python3.14)
        v = sys.version_info
        venv_python = os.path.join(venv_dir, 'bin', f'python{v.major}.{v.minor}')

    if not os.path.isfile(venv_python):
        return

    site_dirs = glob.glob(os.path.join(
        venv_dir, 'lib', 'python*', 'site-packages'))

    # Already activated (venv site-packages on sys.path)
    if site_dirs and all(d in sys.path for d in site_dirs):
        return

    # For -c invocations, we can't re-exec (the inline code isn't in
    # sys.argv). Patch sys.path instead so third-party imports resolve.
    if sys.argv[:1] == ['-c']:
        for d in site_dirs:
            if d not in sys.path:
                sys.path.insert(0, d)
        return

    os.execv(venv_python, [venv_python] + sys.argv)


_activate()
