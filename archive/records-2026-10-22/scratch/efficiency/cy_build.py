"""Compiles catanatron modules with Cython, source unchanged, into a copy of the
package (the worktree's source tree and .venv stay untouched).

usage: cy_build.py <out_dir> <arm>    arm: engine | all
Then put <out_dir> first on sys.path (cy_time.py does) to import the build.
"""

import glob
import os
import shutil
import sys
import time

WT = "/home/owen/colonist-ai-wt/efficiency"
sys.path.insert(0, f"{WT}/.pkgs")  # Cython, installed with pip --target
# The coordinator has gcc only as aarch64-linux-gnu-gcc-13 (no unsuffixed name).
for cc in ("aarch64-linux-gnu-gcc-13", "x86_64-linux-gnu-gcc-13", "gcc"):
    if shutil.which(cc):
        os.environ["CC"] = cc
        os.environ["LDSHARED"] = f"{cc} -shared"
        break

ENGINE = [
    "game.py",
    "state.py",
    "state_functions.py",
    "apply_action.py",
    "models/actions.py",
    "models/board.py",
    "models/dice.py",
    "models/decks.py",
    "models/map.py",
    "models/player.py",
]
COLONIST = [
    "colonist/mcts.py",
    "colonist/encode.py",
    "colonist/graph.py",
    "colonist/selfplay.py",
]

out_dir, arm = sys.argv[1], sys.argv[2]
files = ENGINE + (COLONIST if arm == "all" else [])
dst = os.path.join(out_dir, "catanatron")
shutil.rmtree(out_dir, ignore_errors=True)
shutil.copytree(
    f"{WT}/catanatron/catanatron", dst, ignore=shutil.ignore_patterns("__pycache__")
)

from Cython.Build import cythonize  # noqa: E402
from setuptools import Distribution  # noqa: E402
from setuptools.command.build_ext import build_ext  # noqa: E402

import Cython  # noqa: E402

t0 = time.perf_counter()
os.chdir(out_dir)
exts = cythonize(
    [f"catanatron/{f}" for f in files],
    compiler_directives={
        "language_level": 3,
        "annotation_typing": False,
        "binding": True,
    },
    nthreads=os.cpu_count() or 1,
    quiet=True,
)
dist = Distribution({"ext_modules": exts})
cmd = build_ext(dist)
cmd.inplace = True
cmd.parallel = os.cpu_count() or 1
cmd.ensure_finalized()
cmd.run()
built = sorted(glob.glob("catanatron/**/*.so", recursive=True))
print(f"Cython {Cython.__version__}, CC {os.environ.get('CC')}")
print(f"built {len(built)} of {len(files)} modules in {time.perf_counter() - t0:.0f}s")
for b in built:
    print(" ", b)
