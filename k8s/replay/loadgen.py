"""
Open-loop trace replay load generator (runs in the cluster; standard library only).

Sends requests at the rate the trace dictates whether or not earlier ones have returned
(Poisson arrivals within each step), so an overloaded service sees the load it would see
from real users instead of being let off by a client that waits. Measures latency at the
client and serves Prometheus metrics on :8000, which is where the operator's request-rate
signal comes from in the replay experiments.

Environment:
  TARGET_URL  service to call
  TRACE       JSON list of request rates (requests/second), one per step
  STEP        seconds per trace step (default 15)
  SLO         latency objective in seconds (default 1.0)
  SEED        RNG seed (default 1)
  TIMEOUT     per-request timeout in seconds (default 20); a timeout counts as a violation
  SCORE_FROM  seconds of lead-in at the start of the trace that are replayed but not scored

Prints one line "RESULT {json}" when the trace ends, then stays up so the log can be read.
"""

import json
import os
import random
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

TARGET = os.environ["TARGET_URL"]
TRACE = json.loads(os.environ["TRACE"])
STEP = float(os.environ.get("STEP", "15"))
SLO = float(os.environ.get("SLO", "1.0"))
TIMEOUT = float(os.environ.get("TIMEOUT", "20"))
SCORE_FROM = float(os.environ.get("SCORE_FROM", "0"))
random.seed(int(os.environ.get("SEED", "1")))

lock = threading.Lock()
sent = done = violations = errors = 0
latencies = []          # (send time since start, latency)
t_start = None


def one_request(t_rel):
    global done, violations, errors
    t0 = time.monotonic()
    ok = True
    try:
        with urllib.request.urlopen(TARGET, timeout=TIMEOUT) as r:
            r.read()
    except Exception:
        ok = False
    lat = time.monotonic() - t0 if ok else TIMEOUT
    with lock:
        done += 1
        errors += 0 if ok else 1
        violations += 1 if lat > SLO else 0
        latencies.append((t_rel, lat))


class Metrics(BaseHTTPRequestHandler):
    def do_GET(self):
        with lock:
            body = (f"loadgen_requests_sent_total {sent}\n"
                    f"loadgen_requests_done_total {done}\n"
                    f"loadgen_slo_violations_total {violations}\n"
                    f"loadgen_errors_total {errors}\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main():
    global sent, t_start
    threading.Thread(target=HTTPServer(("", 8000), Metrics).serve_forever, daemon=True).start()
    time.sleep(float(os.environ.get("START_DELAY", "20")))     # let Prometheus find the target
    t_start = time.monotonic()
    threads = []
    for i, rate in enumerate(TRACE):
        end = t_start + (i + 1) * STEP
        while True:
            gap = random.expovariate(rate) if rate > 0 else STEP
            nxt = time.monotonic() + gap
            if nxt >= end:
                break
            time.sleep(max(0.0, nxt - time.monotonic()))
            with lock:
                sent += 1
            th = threading.Thread(target=one_request, args=(time.monotonic() - t_start,), daemon=True)
            th.start()
            threads.append(th)
        time.sleep(max(0.0, end - time.monotonic()))
    for th in threads:
        th.join(timeout=TIMEOUT + 5)

    with lock:
        scored = [(t - SCORE_FROM, l) for t, l in latencies if t >= SCORE_FROM]
        lats = [l for _, l in scored]
        minutes = int((len(TRACE) * STEP - SCORE_FROM) // 60) + 1
        per_min = [[0, 0] for _ in range(minutes)]
        for t, l in scored:
            m = min(minutes - 1, int(t // 60))
            per_min[m][0] += 1
            per_min[m][1] += 1 if l > SLO else 0
        result = dict(requests=len(lats), sent=sent, errors=errors, slo_seconds=SLO,
                      slo_violation_rate=(sum(1 for l in lats if l > SLO) / len(lats)) if lats else None,
                      mean=sum(lats) / len(lats) if lats else None,
                      p50=pct(lats, 0.50), p95=pct(lats, 0.95), p99=pct(lats, 0.99),
                      violations_at={str(th): (sum(1 for l in lats if l > th) / len(lats)) if lats else None
                                     for th in (0.3, 0.5, 1.0, 2.0)},
                      per_minute=per_min)
    print("RESULT " + json.dumps(result), flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
