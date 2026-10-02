"""
Friendly guidance when a helper script is run with the wrong Python.

The project's packages (python-dotenv, pandas, yfinance, ...) are installed in
its virtual environment (./venv, created per the README's Quick Start). Running
a script with the system `python3` instead fails with a bare
"ModuleNotFoundError: No module named 'dotenv'" — accurate, but it doesn't say
what to do about it. Standard library only, on purpose: this has to run BEFORE
anything that needs those packages is imported.
"""

import importlib.util
import os
import shlex
import sys


def require_project_packages(csv_hint=None):
    """Exit with a copy-pasteable fix if the project's packages aren't importable.

    csv_hint: optional path of the CSV file the user could edit instead of
    running the script at all (e.g. "portfolio/holdings.csv").
    """
    if importlib.util.find_spec("dotenv") is not None:
        return

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.basename(sys.argv[0])
    args = " ".join(shlex.quote(a) for a in sys.argv[1:])
    venv_python = os.path.join(root, "venv", "bin", "python3")

    lines = [
        "Error: this script needs the project's Python packages, which are installed in the",
        "project's virtual environment — but it was started with a different Python:",
        f"    {sys.executable}",
        "",
    ]
    if os.path.exists(venv_python):
        lines += [
            "Run it with the virtual environment's Python instead (from the project folder):",
            f"    ./venv/bin/python3 {script} {args}".rstrip(),
            "",
            "or activate the environment first:  source venv/bin/activate",
            "(./run.sh does this for you automatically.)",
        ]
    else:
        lines += [
            "There's no virtual environment yet. Create it once (see the README's Quick Start):",
            "    python3 -m venv venv",
            "    source venv/bin/activate",
            "    pip install -r requirements.txt",
        ]
    if csv_hint:
        lines += ["", f"Tip: you can skip this script entirely — add a row to {csv_hint} in a",
                  "spreadsheet app instead (see portfolio/README.md)."]
    sys.exit("\n".join(lines))
