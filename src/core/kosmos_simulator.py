"""
KOSMOS baseline simulator (Baresi et al., ICSOC 2021).

Faithful reimplementation of KOSMOS's control structure inside the CHIMERA
simulator, for a like-for-like comparison. Structure per the paper:

  * KVPA (vertical, primary, 5s period): a Proportional-Integral controller
    operating on the INVERSE of response time:
        error <- 1/(alpha * RT_SLA) - 1/RT
        prop  <- DC * error
        intg  <- xc + BC * error
        cores <- intg + prop
    alpha in (0,1] is a conservatism factor tightening the setpoint.

  * KHPA (horizontal, secondary, 300s period): activates only when KVPA
    cannot stabilise the system (vertical actuator saturated at v_max) or
    when nodes are poorly utilised (UTIL heuristic -> scale in).

  * Contention Manager: when the sum of desired allocations exceeds node
    capacity, all allocations are scaled down PROPORTIONALLY (not rejected).

Deliberate fidelity notes:
  - KOSMOS's own control periods are used (5s vertical / 300s horizontal),
    not CHIMERA's 15s loop. The fast-vertical/slow-horizontal split is the
    core of KOSMOS's design and flattening it would strawman the baseline.
  - Vertical actuation uses the in-place resize path (fast), matching
    KOSMOS's assumption that vertical scaling is the cheap primary actuator.
  - PI gains are tuned by sweep rather than guessed, so the baseline is
    compared at its best, not at its default.
"""

import numpy as np
from src.core.chimera_simulator import ChimeraSimulator
from src.core.simulator import Pod


class KOSMOSSimulator(ChimeraSimulator):
    def __init__(self, env, controller, cpu_per_request,
                 DC=0.35, BC=0.06, alpha=0.85,
                 vertical_period=5.0, horizontal_period=300.0,
                 util_low=0.35, target_utilization=0.70,
                 use_inplace_resize=True):
        super().__init__(env, controller, cpu_per_request=cpu_per_request,
                         use_inplace_resize=use_inplace_resize,
                         target_utilization=target_utilization)
        self.DC = DC
        self.BC = BC
        self.alpha = alpha
        self.vertical_period = vertical_period
        self.horizontal_period = horizontal_period
        self.util_low = util_low

        self.xc = 0.0
        self.current_cpu_cores = 1.0
        self._rt_cursor = 0
        self._sat_high_streak = 0

    def _recent_rt(self):
        rts = self.metrics.response_times
        new = rts[self._rt_cursor:]
        self._rt_cursor = len(rts)
        if not new:
            return None
        return float(np.mean(new))

    def _contention_manager(self, desired_v, replicas):
        C_max = getattr(self.controller, 'C_max', 60.0)
        total = desired_v * replicas
        if total > C_max and total > 0:
            desired_v = desired_v * (C_max / total)
        return desired_v

    def autoscaling_controller(self, features_list=None):
        last_horizontal = self.env.now
        RT_SLA = self.slo_threshold

        while True:
            replicas = len(self.pods)

            rt = self._recent_rt()
            if rt is not None and rt > 1e-6:
                error = 1.0 / (self.alpha * RT_SLA) - 1.0 / rt

                prop = self.DC * error
                intg = self.xc + self.BC * error
                cores = intg + prop

                v_min, v_max = self.controller.v_min, self.controller.v_max
                cores_clipped = float(np.clip(cores, v_min, v_max))
                cores_clipped = self._contention_manager(cores_clipped, replicas)
                cores_clipped = float(np.clip(cores_clipped, v_min, v_max))

                if abs(cores_clipped - cores) < 1e-9:
                    self.xc = intg
                else:
                    self.xc = float(np.clip(self.xc, v_min - prop, v_max - prop))

                if cores_clipped >= v_max - 1e-9 and error > 0:
                    self._sat_high_streak += 1
                else:
                    self._sat_high_streak = 0

                if abs(cores_clipped - self.current_cpu_cores) >= 0.01:
                    if self.use_inplace_resize:
                        yield self.env.timeout(self.inplace_delay)
                        for pod in self.pods:
                            pod.cpu_cores = cores_clipped
                    else:
                        # No KEP-1287: vertical resize requires a rolling restart.
                        for pod in self.pods:
                            yield self.env.timeout(self.rolling_delay / max(1, len(self.pods)))
                            pod.cpu_cores = cores_clipped
                    self.current_cpu_cores = cores_clipped
                    self.metrics.scaling_actions += 1
                    self.metrics.vertical_scaling_actions += 1

            if self.env.now - last_horizontal >= self.horizontal_period:
                last_horizontal = self.env.now
                replicas = len(self.pods)
                h_min, h_max = self.controller.h_min, self.controller.h_max

                total_cpu = self.current_cpu_cores * max(1, replicas)
                capacity_rps = max(0.01, total_cpu / self.cpu_per_request)
                util = self.calculate_current_load() / capacity_rps

                dh = 0
                if self._sat_high_streak > 0:
                    dh = max(1, int(np.ceil(replicas * 0.5)))
                elif util < self.util_low and replicas > h_min:
                    dh = -1

                if dh > 0:
                    dh = min(dh, h_max - replicas)
                    for _ in range(dh):
                        self.pods.append(Pod(
                            f"pod-{len(self.pods)}",
                            cpu_cores=self.current_cpu_cores,
                            startup_time=self.horizontal_delay,
                            warmup_time=self.env.now))
                    if dh > 0:
                        self.metrics.scaling_actions += 1
                        self.metrics.horizontal_scaling_actions += 1
                        self._sat_high_streak = 0
                elif dh < 0:
                    if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                        self.pods.sort(key=lambda p: p.last_used)
                        to_remove = min(abs(dh), len(self.pods) - h_min)
                        if to_remove > 0:
                            self.pods = self.pods[to_remove:]
                            self.metrics.scaling_actions += 1
                            self.metrics.horizontal_scaling_actions += 1
                            self.last_scale_down_time = self.env.now

            self.metrics.cpu_limit_history.append(self.current_cpu_cores)
            total_active_cpu = sum(p.cpu_cores for p in self.pods)
            self.metrics.total_cpu_allocated += total_active_cpu * (self.vertical_period / 60.0)
            self.metrics.pod_minutes += len(self.pods) * (self.vertical_period / 60.0)

            yield self.env.timeout(self.vertical_period)
