"""Run manually/CI against a built wheel, outside the checkout and editable environment."""

import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


def main():
    wheel = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "pyscoped/migrations/0001_initial.py" in names
        assert not any(name.startswith(("legacy/", "scoped/", "tests/")) for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        metadata = archive.read(next(n for n in names if n.endswith("/METADATA"))).decode()
        assert "License-Expression: MIT" in metadata
        assert "requires-dist: django" in metadata.lower()
    with tempfile.TemporaryDirectory(prefix="pyscoped-wheel-smoke-") as directory:
        work = Path(directory)
        environment = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = dict(os.environ, PYSCOPED_EXAMPLE_DB=str(work / "demo.sqlite3"))
        env.pop("PYTHONPATH", None)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            cwd=work,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        example = work / "existing_app"
        shutil.copytree(
            root / "examples" / "existing_app",
            example,
            ignore=shutil.ignore_patterns("__pycache__", "*.sqlite3", "*.pyc"),
        )
        subprocess.run(
            [
                str(python),
                "-c",
                "import pyscoped; from pathlib import Path; "
                f"assert Path(pyscoped.__file__).resolve().is_relative_to("
                f"Path({str(environment)!r}).resolve()), pyscoped.__file__",
            ],
            cwd=work,
            env=env,
            check=True,
        )
        for command in [("check",), ("migrate", "--noinput"), ("demo",)]:
            subprocess.run([str(python), "manage.py", *command], cwd=example, env=env, check=True)
    print("PASS: built wheel contents, isolated install, migrations, and adoption workflow.")


if __name__ == "__main__":
    main()
