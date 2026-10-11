"""
CHIMERA Joint LQR-Lyapunov Controller

This module implements the core control theory components of CHIMERA:
1. LQR optimal control law solved via the Discrete-Time Algebraic Riccati Equation.
2. Control Lyapunov Function (CLF) safety filter incorporating conformal prediction uncertainty bounds.
3. Cross-coupling penalty function between horizontal and vertical scaling axes.
"""

import numpy as np
import logging
from typing import Dict, Tuple, Optional, List

logger = logging.getLogger(__name__)

class ChimeraController:
    """
    Joint HPA+VPA optimal controller with Lyapunov stability guarantees.
    Implements CHIMERA Phase 2 Problem Formulation and Algorithm 1,
    augmented with Delay-Aware Predictive Coupled Autoscaling (DAPCA).
    """
    
    def __init__(self, 
                 v_min: float = 0.5,     # Min CPU cores per pod
                 v_max: float = 4.0,     # Max CPU cores per pod
                 h_min: int = 2,         # Min pod replicas
                 h_max: int = 30,        # Max pod replicas
                 C_max: float = 60.0,    # Max total CPU budget (v * h)
                 kappa: float = 0.5,     # Cross-coupling penalty coefficient
                 eta: float = 0.2,       # Ratio penalty coefficient
                 gamma_ratio: float = 0.5, # Desired CPU/replica ratio
                 sigma: float = 0.1,     # Lyapunov convergence rate
                 zeta: float = 0.5,      # Lyapunov uncertainty coupling factor
                 Q: Optional[np.ndarray] = None, # LQR state cost matrix (2x2 base)
                 R: Optional[np.ndarray] = None, # LQR control cost matrix (2x2 base)
                 lambda_delay: float = 0.05, # Actuation delay rate
                 use_state_augmentation: bool = True, # DAPCA toggle
                 use_scale_down_guardrail: bool = True, # Anti-chattering toggle
                 decoupled_safety: bool = False, # NEW: true MIMO decoupling (KOSMOS-style independent-loop baseline)
                 coupling_mode: str = "opposing", # "opposing": penalise only dv*dh < 0 (the spec);
                                                  # "absolute": legacy kappa*|dv*dh|, kept for ablation
                 legacy_delay_model: bool = False, # True: Review-2 buffer (one step too long)
                 control_interval: float = 15.0,
                 lean: bool = False      # True: plain LQR + bounds, without the coupling penalty,
                                         # the Lyapunov filter and the action grid (all inert in v5)
                 ):
        """Initialize controller parameters."""
        self.v_min = v_min
        self.v_max = v_max
        self.h_min = h_min
        self.h_max = h_max
        self.C_max = C_max
        self.kappa = kappa
        self.eta = eta
        self.gamma_ratio = gamma_ratio
        self.sigma = sigma
        self.zeta = zeta
        self.lambda_delay = lambda_delay
        self.use_state_augmentation = use_state_augmentation
        self.use_scale_down_guardrail = use_scale_down_guardrail
        self.decoupled_safety = decoupled_safety
        self.coupling_mode = coupling_mode
        self.legacy_delay_model = legacy_delay_model
        self.lean = lean
        self.control_interval = control_interval
        # Set by project_to_safe_set: True when the Lyapunov constraint changed the chosen action
        self.last_filter_active = False
        self._solved_key = None
        if self.decoupled_safety:
            # A truly decoupled controller has no cross-coupling objective by construction.
            self.kappa = 0.0
            self.eta = 0.0
        
        # Base weights for LQR (2x2)
        self.Q = Q if Q is not None else np.diag([30.0, 10.0])
        self.R = R if R is not None else np.diag([0.05, 1.0])
        
        # History buffer for in-flight horizontal scaling commands
        self.dh_history = []
        
        # Augmented system dynamics (initialized during _solve_riccati)
        self.A_aug = np.eye(2)
        self.B_aug = np.eye(2)
        self.P = np.eye(2)
        self.K = np.zeros((2, 2))
        
        # Initial solve
        self._solve_riccati()

    def _solve_riccati(self, tau: float = 30.0):
        """
        Solve Discrete Algebraic Riccati Equation (DARE) offline for the augmented state-space.
        Implements delay-horizon optimal LQR gains.

        The gain depends only on (tau, Q, R), so it is solved once per delay value and
        cached; re-solving every control step was pure overhead.
        """
        key = (float(tau), self.use_state_augmentation, self.legacy_delay_model)
        if key == self._solved_key:
            return
        self._solved_key = key

        if not self.use_state_augmentation:
            # Original unaugmented 2D state LQR solver
            B_tau = np.diag([1.0, np.exp(-self.lambda_delay * tau)])
            self.A_aug = np.eye(2)
            self.B_aug = B_tau
            try:
                from scipy.linalg import solve_discrete_are
                self.P = solve_discrete_are(np.eye(2), B_tau, self.Q, self.R)
                self.K = np.linalg.inv(self.R + B_tau.T @ self.P @ B_tau) @ B_tau.T @ self.P @ np.eye(2)
            except Exception as e:
                logger.warning(f"Failed to solve unaugmented DARE: {e}. Fallback to analytical.")
                self.P = self.Q + np.eye(2)
                self.K = np.zeros((2, 2))
                self.K[0, 0] = self.Q[0, 0] / (self.R[0, 0] + self.Q[0, 0])
                self.K[1, 1] = self.Q[1, 1] / (self.R[1, 1] + self.Q[1, 1])
            self.dh_history = []
            return

        # Number of in-flight slots.
        # A replica ordered at tick k boots after tau seconds and is first OBSERVED at
        # tick k + D, D = floor(tau / dt) + 1 (tau = 20 s, dt = 15 s -> D = 2). The model
        # h(k+1) = h(k) + (oldest slot) therefore needs D - 1 slots. The Review-2 model used
        # ceil(tau / dt) slots, i.e. one step too many: pods that had already booted were
        # still "in flight", counted twice, and the controller scaled in right after
        # scaling out.
        if self.legacy_delay_model:
            d = int(np.ceil(tau / 15.0))
        else:
            d = int(np.floor(tau / self.control_interval)) + 1 - 1
        
        if d == 0:
            self.A_aug = np.eye(2)
            self.B_aug = np.eye(2)
            Q_aug = self.Q.copy()
            R_aug = self.R.copy()
            n = 2
        else:
            n = 2 + d
            self.A_aug = np.zeros((n, n))
            self.A_aug[0, 0] = 1.0  # v_{t+1} = v_t
            self.A_aug[1, 1] = 1.0  # h_{t+1} = h_t + dh_{t-d}
            self.A_aug[1, n - 1] = 1.0  # dh_{t-d} takes effect now
            
            # Shift in-flight horizontal commands down the delay pipeline
            for i in range(3, n):
                self.A_aug[i, i - 1] = 1.0
                
            self.B_aug = np.zeros((n, 2))
            self.B_aug[0, 0] = 1.0  # dv command immediately adjusts v_t
            self.B_aug[2, 1] = 1.0  # dh command enters the pipeline at dh_{t-1}
            
            # Augmented state cost matrix: penalize tracking errors of physical state only
            Q_aug = np.zeros((n, n))
            Q_aug[0, 0] = self.Q[0, 0]
            Q_aug[1, 1] = self.Q[1, 1]
            
            R_aug = self.R.copy()
            
        # Ensure the history buffer matches the augmented state dimension
        if len(self.dh_history) != d:
            self.dh_history = [0.0] * d
            
        try:
            from scipy.linalg import solve_discrete_are
            self.P = solve_discrete_are(self.A_aug, self.B_aug, Q_aug, R_aug)
            self.K = np.linalg.inv(R_aug + self.B_aug.T @ self.P @ self.B_aug) @ self.B_aug.T @ self.P @ self.A_aug
        except Exception as e:
            logger.warning(f"Failed to solve augmented DARE: {e}. Using unaugmented fallback.")
            self.P = np.eye(n)
            self.K = np.zeros((2, n))
            self.K[0, 0] = 0.5
            self.K[1, 1] = 0.5

    def compute_cross_coupling_penalty(self, x_t: np.ndarray, u_t: np.ndarray) -> float:
        """
        Compute cross-coupling penalty Psi(v_t, h_t).
        Penalizes simultaneous opposing horizontal-vertical moves and ratio imbalance.

        Accepts a single action of shape (2,) or a batch of shape (N, 2).

        The legacy form kappa*|dv*dh| also penalised SAME-direction moves, which blocks
        exactly the behaviour the joint design relies on: resizing CPU up while new pods
        are still booting. "opposing" mode penalises only dv*dh < 0 (the two loops
        fighting), matching the documented intent.
        """
        u = np.asarray(u_t, dtype=float)
        dv, dh = u[..., 0], u[..., 1]
        v_next = x_t[0] + dv
        h_next = x_t[1] + dh

        if self.coupling_mode == "absolute":
            coupling = self.kappa * np.abs(dv * dh)
        else:
            coupling = self.kappa * np.maximum(0.0, -dv * dh)

        with np.errstate(divide="ignore", invalid="ignore"):
            ratio_term = np.where(h_next > 0,
                                  self.eta * ((v_next / np.maximum(h_next, 1e-9)) - self.gamma_ratio) ** 2,
                                  0.0)

        out = coupling + ratio_term
        return float(out) if np.ndim(out) == 0 else out

    def compute_lyapunov_value(self, e_t: np.ndarray) -> float:
        """Compute Lyapunov candidate function value V(e_t) = e_t^T P e_t."""
        return float(e_t.T @ self.P @ e_t)

    def verify_lyapunov_drift(self, e_t: np.ndarray, u_opt: np.ndarray, 
                               uncertainty_width: float, tau: float = 30.0) -> Tuple[bool, float]:
        """
        Verify Lyapunov drift constraint on the augmented state tracking error.
        V(e_{t+1}) - V(e_t) <= -sigma * ||e_t||^2 + zeta * uncertainty_width

        When self.decoupled_safety is True, this evaluates two INDEPENDENT scalar/sub-block
        certificates (one for the vertical axis, one for the horizontal+delay-buffer axes)
        instead of one joint certificate over the full augmented error vector. This is the
        control-theoretic definition of "decoupled": each loop only ever reasons about its
        own error dynamics, with no shared Lyapunov function and no coupling term.
        """
        if self.sigma >= 10.0:
            return True, 1e6  # Bypass safety filter

        # Predict next augmented error state e_{t+1} = A_aug * e_t + B_aug * u_opt
        e_next = self.A_aug @ e_t + self.B_aug @ u_opt

        if self.decoupled_safety:
            Pv = float(self.P[0, 0])
            Ph = self.P[1:, 1:]
            Vv_t, Vv_next = Pv * e_t[0] ** 2, Pv * e_next[0] ** 2
            Vh_t = float(e_t[1:].T @ Ph @ e_t[1:])
            Vh_next = float(e_next[1:].T @ Ph @ e_next[1:])
            bound_v = -self.sigma * (e_t[0] ** 2) + self.zeta * uncertainty_width
            bound_h = -self.sigma * float(np.sum(e_t[1:] ** 2)) + self.zeta * uncertainty_width
            drift_v = Vv_next - Vv_t
            drift_h = Vh_next - Vh_t
            is_safe = (drift_v <= bound_v) and (drift_h <= bound_h)
            margin = min(bound_v - drift_v, bound_h - drift_h)
            return is_safe, margin

        V_t = self.compute_lyapunov_value(e_t)
        V_next = self.compute_lyapunov_value(e_next)

        drift = V_next - V_t
        drift_bound = -self.sigma * np.sum(e_t ** 2) + self.zeta * uncertainty_width

        is_safe = drift <= drift_bound
        return is_safe, drift_bound - drift

    def project_to_safe_set(self, x_t: np.ndarray, u_nom: np.ndarray, e_t: np.ndarray,
                             uncertainty_width: float, tau: float = 30.0) -> np.ndarray:
        """
        Project nominal action onto the Lyapunov and physical safety bounds.
        Using grid search over action space for speed & robustness (<0.5ms target latency).
        """
        if self.lean:
            # The LQR action clipped to the physical bounds and the capacity budget. No
            # coupling penalty, no Lyapunov filter, no action grid: in v5 none of them
            # changes a decision (see the ablation), so the deployable controller omits them.
            v_next = float(np.clip(x_t[0] + u_nom[0], self.v_min, self.v_max))
            h_next = float(np.clip(x_t[1] + u_nom[1], self.h_min, self.h_max))
            if v_next * h_next > self.C_max:
                v_next = max(self.v_min, self.C_max / h_next)
            self.last_filter_active = False
            return np.array([v_next - x_t[0], h_next - x_t[1]], dtype=float)

        # Search grid (same grid as the original loop implementation, evaluated in one shot)
        v_actions = np.linspace(-1.5, 1.5, 31)
        h_actions = np.linspace(-5, 5, 21)
        v_actions = np.unique(np.concatenate([v_actions, [u_nom[0], 0.0]]))
        h_actions = np.unique(np.concatenate([h_actions, [u_nom[1], 0.0]]))
        DV, DH = np.meshgrid(v_actions, h_actions, indexing="ij")
        U = np.stack([DV.ravel(), DH.ravel()], axis=1)            # (N, 2)

        # 1. Physical limits
        v_next = x_t[0] + U[:, 0]
        h_next = x_t[1] + U[:, 1]
        feasible = ((v_next >= self.v_min) & (v_next <= self.v_max) &
                    (h_next >= self.h_min) & (h_next <= self.h_max))
        # 2. Capacity budget (a truly decoupled controller has no shared visibility
        #    into the other axis's footprint, so it cannot enforce a joint budget)
        if not self.decoupled_safety:
            feasible &= (v_next * h_next <= self.C_max)

        # 3. Lyapunov stability constraint
        safe = feasible & self._lyapunov_safe_batch(e_t, U, uncertainty_width)

        # 4. Objective: distance to the nominal LQR action plus the coupling penalty
        cost = np.sum((U - u_nom) ** 2, axis=1) + self.compute_cross_coupling_penalty(x_t, U)

        def argmin_of(mask):
            if not mask.any():
                return None
            c = np.where(mask, cost, np.inf)
            return U[int(np.argmin(c))]

        best_physical = argmin_of(feasible)
        best_safe = argmin_of(safe)
        best_u = best_safe if best_safe is not None else np.zeros(2)
        # Filter "activation" = the stability constraint changed the action that would
        # otherwise have been chosen. Measured here in one pass instead of re-running the
        # whole controller with the filter disabled.
        self.last_filter_active = (best_physical is not None and
                                   not np.allclose(best_u, best_physical, atol=1e-3))
        return np.array(best_u, dtype=float)

    def _lyapunov_safe_batch(self, e_t: np.ndarray, U: np.ndarray, uncertainty_width: float) -> np.ndarray:
        """Vectorised verify_lyapunov_drift over a batch of candidate actions U (N, 2)."""
        if self.sigma >= 10.0:
            return np.ones(len(U), dtype=bool)
        E_next = (self.A_aug @ e_t)[None, :] + U @ self.B_aug.T   # (N, n)
        if self.decoupled_safety:
            Pv = float(self.P[0, 0])
            Ph = self.P[1:, 1:]
            drift_v = Pv * E_next[:, 0] ** 2 - Pv * e_t[0] ** 2
            Vh_t = float(e_t[1:] @ Ph @ e_t[1:])
            drift_h = np.einsum("ni,ij,nj->n", E_next[:, 1:], Ph, E_next[:, 1:]) - Vh_t
            bound_v = -self.sigma * e_t[0] ** 2 + self.zeta * uncertainty_width
            bound_h = -self.sigma * float(np.sum(e_t[1:] ** 2)) + self.zeta * uncertainty_width
            return (drift_v <= bound_v) & (drift_h <= bound_h)
        V_t = float(e_t @ self.P @ e_t)
        V_next = np.einsum("ni,ij,nj->n", E_next, self.P, E_next)
        bound = -self.sigma * float(np.sum(e_t ** 2)) + self.zeta * uncertainty_width
        return (V_next - V_t) <= bound

    def get_desired_allocation(self, x_t: np.ndarray, x_star: np.ndarray, 
                               uncertainty_width: float, tau: float = 30.0) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute optimal resource allocation command under dynamic state augmentation.
        
        Args:
            x_t: Current resource vector [v_t, h_t]^T
            x_star: Target resource vector [v*_t, h*_t]^T
            uncertainty_width: Conformal prediction interval width
            tau: Actuation delay parameter
            
        Returns:
            Tuple of (optimal_action, next_state)
        """
        # Riccati gains for the delay tau (cached after the first solve)
        self._solve_riccati(tau)
        
        if not self.use_state_augmentation:
            # Original unaugmented 2D state error and control action calculation
            e_t = x_t - x_star
            u_nom = -self.K @ e_t
            u_opt = self.project_to_safe_set(x_t, u_nom, e_t, uncertainty_width, tau)
            return u_opt, x_t + u_opt
            
        # 1. Build augmented error vector E_t
        # e_t = [v_t - v*_t, h_t - h*_t, dh_{t-1}, dh_{t-2}, ..., dh_{t-d}]^T
        d = len(self.dh_history)
        e_t = np.zeros(2 + d)
        e_t[0] = x_t[0] - x_star[0]
        e_t[1] = x_t[1] - x_star[1]
        for i in range(d):
            e_t[2 + i] = self.dh_history[i]
            
        # 2. Compute nominal feedback action
        u_nom = -self.K @ e_t
        
        # 3. Project onto safe set via Lyapunov CLF safety filter
        u_opt = self.project_to_safe_set(x_t, u_nom, e_t, uncertainty_width, tau)
        
        return u_opt, x_t + u_opt
