"""Find captures whose opponent's name contains a substring (case-insensitive). Prints paths only."""

import sys
from multiprocessing import Pool
from pathlib import Path

from catanatron.colonist.capture import load as load_capture

NEEDLE = sys.argv[1].lower()


def scan(path):
    names = set()
    try:
        for f in load_capture(path):
            d = f.data.get("data") if isinstance(f.data, dict) else None
            if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
                continue
            gs = d["payload"].get("playerUserStates")
            if isinstance(gs, list):
                for u in gs:
                    if isinstance(u, dict) and u.get("username"):
                        names.add(u["username"])
    except Exception as error:
        return path, None, str(error)
    return path, sorted(n for n in names if NEEDLE in n.lower()), None


if __name__ == "__main__":
    paths = sorted(str(p) for p in Path("captures").glob("*/*.json"))
    print(f"{len(paths)} captures", flush=True)
    bad = 0
    with Pool(4) as pool:
        for path, hit, err in pool.imap_unordered(scan, paths, chunksize=16):
            if err:
                bad += 1
            elif hit:
                print("HIT", path, hit, flush=True)
    print(f"DONE errors {bad}", flush=True)
