# Methodology

One joint control step every 15 s, plus a vertical-only inner step every 5 s. Code:
`src/core/chimera_controller.py`, `src/core/chimera_simulator.py`; the operator mirrors it
in `k8s/operator_core.py`.

## 1. Forecast

A LightGBM conformalized quantile regression (CQR) model gives a 90 % interval for
next-minute demand. A reactive fallback (last value plus a split-conformal quantile of
recent one-step changes) carries its own margin, so a model with nothing to learn from
cannot leave the controller with zero headroom. The operator, which has no offline
training data for a new target, uses the same conformal construction around an EWMA.

## 2. Risk-optimal capacity

A request arriving at utilisation `u` misses the SLO with probability

    p(u) = exp(-k (1 - u))        for u < 0.95 (saturating above),
    k    = SLO / mean CPU service time, learned online.

Total cores `c` minimise expected violations plus a price per core over demand scenarios
`L_i` (current load plus recent signed one-step changes, plus the CQR upper bound):

    c* = argmin_c  mean_i [ L_i p(L_i / c) ] + lambda c,
    lambda = p'(u0) u0^2.

With this price a certain demand `L` gives exactly `c* = L / u0`, so the knob `u0` keeps
its usual meaning as a target utilisation, and under uncertainty the convexity of `p`
decides where headroom pays. The objective is not convex (p saturates above 0.95), so it
is minimised globally on a grid and refined with bounded Brent, over capacities that keep
the expected load at or below 95 % utilisation.

## 3. Target split and replica band

Capacity is split into `x* = [v*, h*]` with `h* = ceil(sqrt(c / gamma))` and always
`h* x v_max >= c`. Between `ceil(c / (0.85 v_max))` and `floor(c / v_min)` the pod count
does not change the CPU bill, but every new pod costs 20 s of booting CPU, so `h*` stays
at the current count inside that band.

## 4. Residual vertical bridging

`v*` is sized for the replicas that are serving now. While ordered pods boot, the fast
actuator covers the gap; as they arrive it hands the capacity back.

## 5. Delay-augmented LQR

    e_t = [ v - v*,  h_serving - h*,  dh(t-1) ]
    u_t = -K e_t,   K from the discrete algebraic Riccati equation,
    Q = diag(30, 10),  R = diag(0.05, 1).

The third component is the in-flight slot: replicas ordered at the previous tick that have
not booted. `floor(tau / dt)` slots are needed; a 20 s boot on a 15 s loop needs one. Only
scale-ups enter the buffer, because a removed pod stops serving immediately. The gain is
solved once per delay and cached.

## 6. Safety

Actions are projected onto the physical bounds and the joint capacity budget. Stability
follows from the Riccati solution: `V = e'Pe` satisfies
`V(e+) - V(e) = -e'(Q + K'RK)e <= 0` under `u = -Ke`. A separate runtime Lyapunov filter
therefore cannot bind for the nominal action and is off by default (measured: 0.00
activations per run; re-enabling it gives identical runs).

## 7. Cascade inner loop (5 s)

Only the inner loop moves CPU. It scales up when a Poisson test flags a significant surge
in a short arrival window or when the need exceeds the allocation by a 10 % dead-band, and
releases once the need has stayed 15 % below the allocation for two ticks. Re-acquiring
CPU takes 0.1 s, so holding it through a cooldown is waste.

## 8. Drift

ADWIN monitors the forecast error. On a detected regime change the reactive margin drops
its pre-change history and re-learns from the new regime.

## 9. Actuation

CPU in place through the `pods/resize` subresource; replicas through the Deployment scale
subresource.
