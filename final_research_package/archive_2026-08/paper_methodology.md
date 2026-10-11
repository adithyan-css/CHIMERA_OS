# Paper Evidence: Methodology

We formulate the system state as a 2D tracking error vector:
\[e_t = x_t - x^*_t = [v_t - v^*_t, h_t - h^*_t]^T\]
where $x_t$ represents the current state of allocated CPU cores per pod ($v_t$) and replica count ($h_t$), and $x^*_t$ represents the target resource trajectory calculated to satisfy incoming demand.

## 1. Optimal Control Law (LQR)
We compute optimal feedback gains using a discrete LQR formulation:
\[u_\text{nom} = -K e_t\]
where $K$ is solved from the discrete algebraic Riccati equation (DARE) online:
\[P = Q + A^T P A - A^T P B (R + B^T P B)^{-1} B^T P A\]
with state cost $Q = \text{diag}([1.0, 1.0])$ and control cost $R = \text{diag}([0.5, 0.5])$.

## 2. Cross-Coupling Penalty
To coordinate H/V actions, we introduce the penalty $\Psi(v_t, h_t)$:
\[\Psi(v_t, h_t) = \kappa |dv \cdot dh| + \eta \left(\frac{v_\text{next}}{h_\text{next}} - \gamma_\text{ratio}\right)^2\]
where $dv, dh$ are resizing commands, $\kappa = 0.5$ is the coupling penalty weight, and $\eta = 0.2$ is the ratio imbalance weight.

## 3. Lyapunov Stability Boundary
We define the Control Lyapunov Function (CLF) as $V(e_t) = e_t^T P e_t$. The safety filter rejects any candidate control action $u_t$ that violates the stability drift inequality:
\[V(e_{t+1}) - V(e_t) \leq -\sigma \|e_t\|^2 + \zeta (U_t - L_t)\]
where $\sigma = 0.1$ is the drift convergence rate, $\zeta = 0.5$ is the uncertainty weight, and $U_t - L_t$ is the conformal prediction interval width. If violated, we project the nominal action onto the safe boundary using grid-search.
