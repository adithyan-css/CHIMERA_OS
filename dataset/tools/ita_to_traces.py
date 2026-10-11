"""
Build per-minute request traces from two public web-server logs of the Internet Traffic
Archive (https://ita.ee.lbl.gov/html/traces.html; "the traces may be freely redistributed"):

  NASA_access_log_Jul95.gz      NASA Kennedy Space Center WWW server, July 1995
  clarknet_access_log_Aug28.gz  ClarkNet (an Internet provider), 28 Aug - 3 Sep 1995

Each output trace is a 100-minute window of requests per minute, in the same JSON layout
as dataset/workloads/*.json. The windows are fixed HERE, by calendar rule, before any
controller is run on them:

  NASA      14:00 local on 3, 6, 10, 13, 18 and 24 July        (6 windows)
  ClarkNet  29 Aug 10:00, 30 Aug 15:00, 31 Aug 20:00, 1 Sep 10:00  (4 windows)

These are web-server traces, not serverless-function traces: a different kind of real
workload from the Azure Functions excerpts, never used in any design round.

    python -I dataset/tools/ita_to_traces.py dataset/external_raw/ita dataset/external
"""

import gzip
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

STAMP = re.compile(rb"\[(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):\d{2} ")
MONTHS = {b"Jul": 7, b"Aug": 8, b"Sep": 9}
WINDOWS = {
    "NASA_access_log_Jul95.gz": ("nasa", [datetime(1995, 7, d, 14, 0) for d in (3, 6, 10, 13, 18, 24)]),
    "clarknet_access_log_Aug28.gz": ("clarknet", [datetime(1995, 8, 29, 10, 0), datetime(1995, 8, 30, 15, 0),
                                                  datetime(1995, 8, 31, 20, 0), datetime(1995, 9, 1, 10, 0)]),
}
MINUTES = 100


def per_minute(path: Path) -> Counter:
    counts = Counter()
    with gzip.open(path, "rb") as f:
        for line in f:
            m = STAMP.search(line)
            if m and m.group(2) in MONTHS:
                counts[(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)), int(m.group(4)), int(m.group(5)))] += 1
    return counts


def main(raw_dir: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for fname, (site, starts) in WINDOWS.items():
        counts = per_minute(Path(raw_dir) / fname)
        for start in starts:
            rpm = []
            for i in range(MINUTES):
                t = start + timedelta(minutes=i)
                rpm.append(counts.get((t.year, t.month, t.day, t.hour, t.minute), 0))
            wid = f"{site}_{start:%m%d_%H%M}"
            (out / f"{wid}.json").write_text(json.dumps({
                "function_id": wid,
                "source": f"Internet Traffic Archive, {fname}",
                "window_start_local": start.isoformat(),
                "request_trace": {"requests_per_minute": rpm},
            }), encoding="utf-8")
            index.append((wid, sum(rpm) / MINUTES, min(rpm), max(rpm)))
    for wid, mean, lo, hi in index:
        print(f"{wid}: mean {mean:.1f} rpm, min {lo}, max {hi}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
