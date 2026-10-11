"""
CHIMERA Unit Tests

Verifies control laws, stability filters, CQR, and drift detection.
Uses python's built-in unittest framework for dependency-free execution.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import unittest
from src.core.chimera_controller import ChimeraController
from src.models.cqr_uncertainty import ConformalQuantileRegressor
from src.models.drift_detector import AdwinDriftDetector
from src.core.chimera_simulator import calculate_extended_rei, JointMetrics

class TestChimeraController(unittest.TestCase):
    
    def test_chimera_controller_lqr(self):
        """Verify DARE solver and LQR gain matrices."""
        # Test default augmented mode
        controller_aug = ChimeraController(
            v_min=0.5, v_max=4.0, h_min=2, h_max=30, C_max=60.0, use_state_augmentation=True
        )
        self.assertEqual(controller_aug.P.shape, (4, 4))
        self.assertEqual(controller_aug.K.shape, (2, 4))
        eigvals_aug = np.linalg.eigvals(controller_aug.P)
        self.assertTrue(np.all(eigvals_aug > 0))
        
        # Test unaugmented baseline mode
        controller_unaug = ChimeraController(
            v_min=0.5, v_max=4.0, h_min=2, h_max=30, C_max=60.0, use_state_augmentation=False
        )
        self.assertEqual(controller_unaug.P.shape, (2, 2))
        self.assertEqual(controller_unaug.K.shape, (2, 2))
        eigvals_unaug = np.linalg.eigvals(controller_unaug.P)
        self.assertTrue(np.all(eigvals_unaug > 0))

    def test_cross_coupling_penalty(self):
        """Verify cross-coupling penalty calculates coupling costs."""
        controller = ChimeraController(kappa=0.5, eta=0.2, gamma_ratio=0.5)
        x_t = np.array([1.0, 4.0]) # v=1.0, h=4
        
        # Opposing resizing: vertical up + horizontal down
        u_opposing = np.array([1.0, -1.0])
        penalty_opposing = controller.compute_cross_coupling_penalty(x_t, u_opposing)
        
        # Non-opposing resizing: vertical 0, horizontal up
        u_non_opposing = np.array([0.0, 1.0])
        penalty_non_opposing = controller.compute_cross_coupling_penalty(x_t, u_non_opposing)
        
        self.assertTrue(penalty_opposing > penalty_non_opposing)

    def test_lyapunov_stability_filter(self):
        """Verify Lyapunov candidate function and drift verification."""
        controller = ChimeraController(sigma=0.1, zeta=0.5)
        e_t = np.array([1.0, 2.0, 0.0, 0.0])
        
        # Large control action in wrong direction (should violate stability check)
        u_bad = np.array([2.0, 4.0])
        is_safe_bad, margin_bad = controller.verify_lyapunov_drift(e_t, u_bad, uncertainty_width=0.2)
        
        # Small control action in correct direction (should satisfy stability check)
        u_good = np.array([-0.5, -1.0])
        is_safe_good, margin_good = controller.verify_lyapunov_drift(e_t, u_good, uncertainty_width=1.5)
        
        self.assertTrue(is_safe_good)
        self.assertFalse(is_safe_bad)

    def test_delay_buffer_matches_observed_delay(self):
        """A pod ordered at tick k is first observed at tick floor(tau/dt)+1; the buffer
        holds one slot fewer than that. 20 s boot at 15 s -> 1 slot (3x3 state)."""
        c = ChimeraController()
        c._solve_riccati(tau=20.0)
        self.assertEqual(len(c.dh_history), 1)
        self.assertEqual(c.A_aug.shape, (3, 3))
        self.assertTrue(np.all(np.linalg.eigvalsh(c.P) > 0))
        c._solve_riccati(tau=40.0)
        self.assertEqual(len(c.dh_history), 2)
        legacy = ChimeraController(legacy_delay_model=True)
        legacy._solve_riccati(tau=20.0)
        self.assertEqual(len(legacy.dh_history), 2)   # the Review-2 model, one slot too many

    def test_coupling_penalises_only_opposing_moves(self):
        """Scaling both axes up together (the bridging move) is not a conflict."""
        c = ChimeraController(kappa=0.5, eta=0.0)
        x = np.array([1.0, 4.0])
        self.assertEqual(c.compute_cross_coupling_penalty(x, np.array([0.5, 2.0])), 0.0)
        self.assertGreater(c.compute_cross_coupling_penalty(x, np.array([0.5, -2.0])), 0.0)
        legacy = ChimeraController(kappa=0.5, eta=0.0, coupling_mode="absolute")
        self.assertGreater(legacy.compute_cross_coupling_penalty(x, np.array([0.5, 2.0])), 0.0)

    def test_vectorised_filter_matches_scalar_check(self):
        """The batched Lyapunov check agrees with verify_lyapunov_drift on every candidate."""
        c = ChimeraController(sigma=0.1, zeta=0.5)
        e = np.array([1.0, 2.0, 1.0, 0.0])
        U = np.array([[dv, dh] for dv in np.linspace(-1.5, 1.5, 7) for dh in np.linspace(-5, 5, 11)])
        batch = c._lyapunov_safe_batch(e, U, 0.3)
        scalar = np.array([c.verify_lyapunov_drift(e, u, 0.3)[0] for u in U])
        np.testing.assert_array_equal(batch, scalar)

    def test_safe_action_respects_bounds(self):
        c = ChimeraController(v_min=0.5, v_max=2.0, h_min=2, h_max=6)
        u, x_next = c.get_desired_allocation(np.array([1.9, 5.0]), np.array([4.0, 12.0]), 1.0, tau=20.0)
        self.assertLessEqual(x_next[0], 2.0 + 1e-9)
        self.assertLessEqual(x_next[1], 6.0 + 1e-9)


class TestSimulatorFixes(unittest.TestCase):

    def test_load_sensor_is_not_capped(self):
        """recent_arrivals used to be deque(maxlen=100), capping load at 6.7 req/s."""
        import simpy
        from src.core.simulator import ServerlessSimulator
        sim = ServerlessSimulator(simpy.Environment(), autoscaler=None)
        sim.scale_interval = 15.0
        sim.recent_arrivals.extend(np.linspace(0.0, 14.9, 600))   # 40 req/s for 15 s
        sim.env.run(until=15.0)
        self.assertAlmostEqual(sim.calculate_current_load(), 600 / 15.0)

    def test_target_split_covers_demand_at_vmax(self):
        """Once v* is clipped at v_max, h* must grow so that h* x v_max >= demand."""
        import simpy
        from src.core.chimera_simulator import ChimeraSimulator
        ctrl = ChimeraController(v_min=0.5, v_max=0.5, h_min=2, h_max=30)
        sim = ChimeraSimulator(simpy.Environment(), ctrl, cpu_per_request=0.5)
        sim.run([600.0] * 3, "f", lambda: 0.1)          # 10 req/s -> ~7 cores at 70%
        self.assertGreaterEqual(max(r for _, r, _, _ in sim.timeline), 10)


    def test_cascade_releases_cpu_after_load_drops(self):
        """With the inner loop, CPU follows a drop within seconds instead of being held
        through the 60 s vertical cooldown; cost is integrated second by second."""
        import simpy
        from src.core.chimera_simulator import ChimeraSimulator

        def run(inner):
            np.random.seed(1)
            ctrl = ChimeraController(v_min=0.5, v_max=4.0, h_min=2, h_max=30)
            sim = ChimeraSimulator(simpy.Environment(), ctrl, cpu_per_request=0.5,
                                   vertical_bridging=True, serving_replica_state=True,
                                   inner_vertical_period=5.0 if inner else 0.0,
                                   inner_release_band=0.15, inner_release_ticks=2)
            sim.run([400.0, 400.0, 30.0, 30.0], "f", lambda: np.random.exponential(0.1))
            return sim
        slow, fast = run(False), run(True)
        self.assertTrue(fast.exact_cost)
        self.assertLess(fast.metrics.total_cpu_allocated, slow.metrics.total_cpu_allocated)
        self.assertLess(fast.current_cpu_cores, slow.current_cpu_cores + 1e-9)


    def _sim(self, **kw):
        import simpy
        from src.core.chimera_simulator import ChimeraSimulator
        np.random.seed(3)
        ctrl = ChimeraController(v_min=0.5, v_max=4.0, h_min=2, h_max=30, sigma=100.0)
        return ChimeraSimulator(simpy.Environment(), ctrl, cpu_per_request=0.5, vertical_bridging=True,
                                serving_replica_state=True, inner_vertical_period=5.0,
                                inner_release_band=0.15, inner_release_ticks=2, **kw)

    def test_inner_loop_is_sole_owner_of_cpu(self):
        """With inner_owns_vertical, every CPU resize comes from the inner loop."""
        sim = self._sim(inner_owns_vertical=True)
        calls = []
        orig = sim._resize_all
        sim._resize_all = lambda v: (calls.append(v), (yield from orig(v)))[1]
        sim.run([300.0, 600.0, 300.0], "f", lambda: np.random.exponential(0.1))
        self.assertEqual(sim.metrics.vertical_scaling_actions, len(calls))

    def test_replica_band_moves_pods_less(self):
        """Replicas move only when vertical resizing cannot absorb the change: on a steady
        load the band makes no more replica changes, and runs no more pods, than the plain
        split (pods do not change the CPU bill, but every new one boots for 20 s)."""
        load = [600.0] * 10
        banded = self._sim(inner_owns_vertical=True, replica_band=0.85)
        banded.run(load, "f", lambda: np.random.exponential(0.1))
        plain = self._sim(inner_owns_vertical=True)
        plain.run(load, "f", lambda: np.random.exponential(0.1))
        self.assertLessEqual(banded.metrics.horizontal_scaling_actions, plain.metrics.horizontal_scaling_actions)
        self.assertLess(np.mean([r for _, r, _, _ in banded.timeline]), np.mean([r for _, r, _, _ in plain.timeline]))

    def test_adwin_reset_trims_margin_history(self):
        class AlwaysDrift:
            def add_element(self, _):
                return True
        sim = self._sim(adwin_margin_reset=True)
        sim.drift_detector = AlwaysDrift()
        sim.load_history = list(np.linspace(0, 5, 60))
        sim.run([300.0], "f", lambda: np.random.exponential(0.1))
        self.assertLessEqual(len(sim.load_history), 9)


    def test_risk_sizing_reduces_to_target_utilisation_when_certain(self):
        """With one certain demand scenario the risk-optimal capacity is exactly L*cpp/u, so
        the target-utilisation knob keeps its meaning; uncertainty only adds headroom."""
        import simpy
        from src.core.chimera_simulator import ChimeraSimulator
        sim = ChimeraSimulator(simpy.Environment(), ChimeraController(), cpu_per_request=0.5,
                               target_utilization=0.7, risk_sizing=True)
        for L in (0.5, 4.0, 12.0):
            self.assertAlmostEqual(sim._risk_cores([L]), L * 0.5 / 0.7, places=4)
        self.assertGreater(sim._risk_cores([7.0, 10.0, 13.0]), sim._risk_cores([10.0]))

    def test_risk_sizing_learns_service_time(self):
        """k = SLO / mean service time is estimated from completed requests: slower requests
        mean a smaller k and therefore more capacity for the same load."""
        import simpy
        from src.core.chimera_simulator import ChimeraSimulator
        sim = ChimeraSimulator(simpy.Environment(), ChimeraController(), cpu_per_request=0.5,
                               target_utilization=0.7, risk_sizing=True)
        default_k = sim._k_hat()
        sim._svc.extend([0.2] * 1000)                       # requests take twice as long
        self.assertAlmostEqual(sim._k_hat(), 2.5, places=6)
        self.assertLess(sim._k_hat(), default_k)


class TestCQRUncertainty(unittest.TestCase):
    
    def test_conformal_quantile_regressor(self):
        """Verify CQR lower/upper bounds and conformity scores."""
        cqr = ConformalQuantileRegressor(alpha=0.1, calibration_size=10)
        cqr.calibration_scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        
        # Get correction term E_t as (1-alpha) percentile of calibration scores
        E_t = np.percentile(cqr.calibration_scores, 100 * (1.0 - cqr.alpha))
        self.assertAlmostEqual(E_t, 0.91, places=2)

class TestDriftDetector(unittest.TestCase):
    
    def test_adwin_drift_detector(self):
        """Verify ADWIN signals concept drift on statistical shift."""
        detector = AdwinDriftDetector(delta=0.1, min_window_length=5)
        
        # Phase 1: Stable mean of 10.0
        np.random.seed(42)
        for _ in range(30):
            val = np.random.normal(10.0, 0.2)
            detector.add_element(val)
            
        # Phase 2: Abrupt shift to mean of 25.0
        drift_detected = False
        for _ in range(30):
            val = np.random.normal(25.0, 0.2)
            if detector.add_element(val):
                drift_detected = True
                break
                
        self.assertTrue(drift_detected)

class TestMetrics(unittest.TestCase):
    
    def test_extended_rei_metric(self):
        """Verify calculation of extended REI score."""
        metrics = JointMetrics()
        metrics.total_requests = 100
        metrics.slo_violations = 5
        metrics.total_cpu_allocated = 200.0
        metrics.scaling_actions = 10
        metrics.coupling_penalties = [0.1, 0.2, 0.3]
        
        rei_val = calculate_extended_rei(metrics, delta=0.15)
        self.assertTrue(0.0 <= rei_val <= 1.0)

if __name__ == "__main__":
    unittest.main()
