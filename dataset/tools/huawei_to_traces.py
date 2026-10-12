"""
Build per-minute request traces from the Huawei Private Cloud FaaS release 2023
(https://github.com/sir-lab/data-release, "requests_minute.zip"; CC BY 4.0; analysed in
Joosen et al., "How Does It Function? Characterizing Long-term Trends in Production
Serverless Workloads", SoCC 2023). The file holds requests per minute for 200 production
serverless functions.

Each output trace is a 100-minute window of one function, in the same JSON layout as
dataset/workloads/*.json. The selection is fixed HERE, before any controller is run:

  window    the first released day with index >= 30, minutes 600-699 (10:00-11:39)
  functions the 10 lowest function IDs that, in that window, have no missing minute and
            at least 60 requests per minute on average (below that a per-minute count is
            mostly integer noise once rescaled)

    python -I dataset/tools/huawei_to_traces.py dataset/external_raw/huawei/requests_minute.zip dataset/external_huawei
"""

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

FIRST_DAY, START_MIN, MINUTES, N_FUNCTIONS, MIN_MEAN = 30, 600, 100, 10, 60.0


def main(zip_path: str, out_dir: str):
    z = zipfile.ZipFile(zip_path)
    days = sorted(int(n.split("day_")[1][:3]) for n in z.namelist() if n.endswith(".csv"))
    day = next(d for d in days if d >= FIRST_DAY)
    with z.open(f"requests_minute/day_{day:03d}.csv") as f:
        rows = list(csv.reader(io.TextIOWrapper(f, encoding="utf-8")))
    header, body = rows[0], rows[1:]
    window = body[START_MIN:START_MIN + MINUTES]
    assert len(window) == MINUTES and int(window[0][1]) % 86400 == START_MIN * 60, "unexpected file layout"

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chosen = 0
    for col in range(2, len(header)):
        cells = [r[col] for r in window]
        if any(c == "" for c in cells):
            continue
        rpm = [float(c) for c in cells]
        if sum(rpm) / MINUTES < MIN_MEAN:
            continue
        wid = f"huawei_f{int(header[col]):03d}"
        (out / f"{wid}.json").write_text(json.dumps({
            "function_id": wid,
            "source": "Huawei Private Cloud FaaS trace 2023 (sir-lab/data-release), requests_minute",
            "day": day, "start_minute": START_MIN,
            "request_trace": {"requests_per_minute": rpm},
        }), encoding="utf-8")
        cv = (sum((x - sum(rpm) / MINUTES) ** 2 for x in rpm) / MINUTES) ** 0.5 / (sum(rpm) / MINUTES)
        print(f"{wid}: mean {sum(rpm) / MINUTES:.0f} rpm, min {min(rpm):.0f}, max {max(rpm):.0f}, cv {cv:.2f}")
        chosen += 1
        if chosen == N_FUNCTIONS:
            break
    print(f"day {day}: {chosen} functions")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
