"""The performance log (`perf.py`) summed up, one block per app version, to compare versions.

Per route: how many requests, the median and slowest time; per step and tally: the time it
takes in a request, on average; and the store's lock: who waited, for how long, and on whom.
The log holds no personal data, and neither does this.

    uv run python -m taxbinder.dev.perf_report [LOG] [--version 0.10.0]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from taxbinder import perf


def load(path: Path) -> list[dict]:
    files = sorted(path.parent.glob(path.name + ".*"), reverse=True) + [path]  # oldest first
    out = []
    for f in files:
        if f.exists():
            out += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return out


def report(lines: list[dict]) -> str:
    out = []
    by_version: dict[str, list[dict]] = defaultdict(list)
    for x in lines:
        by_version[x.get("v", "?")].append(x)
    for v, xs in by_version.items():
        reqs = [x for x in xs if x["kind"] == "request"]
        out.append(f"== TaxBinder {v}: {len(reqs)} requests")
        routes: dict[str, list[float]] = defaultdict(list)
        for x in reqs:
            routes[f"{x['method']} {x['route']}"].append(x["ms"])
        out.append(f"{'route':48} {'n':>5} {'median ms':>10} {'max ms':>9}")
        for r, ms in sorted(routes.items(), key=lambda kv: -sum(kv[1])):
            out.append(f"{r[:48]:48} {len(ms):5} {statistics.median(ms):10.0f} {max(ms):9.0f}")
        steps: dict[str, list[float]] = defaultdict(list)
        for x in xs:
            if x["kind"] == "step":
                steps[x["name"]].append(x["ms"])
        for x in reqs:
            for name, (_, ms) in x.get("tallies", {}).items():
                steps[f"{name} (all calls)"].append(ms)
        out.append(f"\n{'step':48} {'n':>5} {'mean ms':>10} {'max ms':>9}")
        for s, ms in sorted(steps.items(), key=lambda kv: -sum(kv[1]))[:25]:
            out.append(f"{s[:48]:48} {len(ms):5} {statistics.mean(ms):10.0f} {max(ms):9.0f}")
        waits: dict[tuple[str, str], list[float]] = defaultdict(list)
        for x in xs:
            if x["kind"] == "lock-wait":
                waits[(x["who"], x["held_by"])].append(x["ms"])
        if waits:
            out.append(f"\n{'waited for the lock':32} {'held by':24} {'n':>5} {'total s':>8}")
            for (w, h), ms in sorted(waits.items(), key=lambda kv: -sum(kv[1]))[:15]:
                out.append(f"{w[:32]:32} {h[:24]:24} {len(ms):5} {sum(ms) / 1000:8.1f}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="perf_report")
    ap.add_argument("log", nargs="?", type=Path, default=perf.default_path())
    ap.add_argument("--version", help="only this app version")
    args = ap.parse_args(argv)
    lines = load(args.log)
    if args.version:
        lines = [x for x in lines if x.get("v") == args.version]
    print(report(lines) if lines else f"nothing in {args.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
