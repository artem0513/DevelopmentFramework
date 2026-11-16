"""Run unittest with a physical interpreter path, preserving its result.

Hosted Python installations may expose an interpreter through a symlink. Test
fixtures pin sys.executable as a trusted host executable, whose existing path
guards deliberately reject aliases. Resolve only this launcher-selected Python;
runtime validation of configured host paths remains unchanged.
"""
from pathlib import Path
import subprocess
import sys


def main():
    args = sys.argv[1:] or ["discover", "-s", "tests", "-v"]
    return subprocess.call([str(Path(sys.executable).resolve(strict=True)), "-B", "-m", "unittest", *args])


if __name__ == "__main__":
    raise SystemExit(main())
