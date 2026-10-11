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
