"""
CHIMERA Concept Drift Detection Module (ADWIN)

This module implements the ADWIN (Adaptive Windowing) algorithm to monitor
forecasting error distribution changes and trigger recalibration.
"""

import numpy as np
import logging
from typing import List

logger = logging.getLogger(__name__)

class AdwinDriftDetector:
    """
    Adaptive Windowing (ADWIN) drift detector.
    Monitors prediction errors and signals a concept drift when statistcal change is detected.
    """
    
    def __init__(self, delta: float = 0.05, min_window_length: int = 10, max_window_length: int = 500):
        """
        Initialize ADWIN detector.
        
        Args:
            delta: Confidence level for statistical test (default: 0.05)
            min_window_length: Minimum size of split sub-windows
            max_window_length: Maximum tracked window size
        """
        self.delta = delta
        self.min_window_length = min_window_length
        self.max_window_length = max_window_length
        self.window = []
        
    def add_element(self, value: float) -> bool:
        """
        Add a new observed value (prediction error) and check for concept drift.
        Returns True if a concept drift was detected at this step.
        """
        self.window.append(value)
        
        # Enforce max window capacity
        if len(self.window) > self.max_window_length:
            self.window.pop(0)
            
        drift_detected = False
        
        # Run split window test
        while len(self.window) >= 2 * self.min_window_length:
            n = len(self.window)
            drift_in_current_window = False
            
            # Test all possible split points
            for i in range(self.min_window_length, n - self.min_window_length + 1):
                W0 = self.window[:i]
                W1 = self.window[i:]
                
                n0 = len(W0)
                n1 = len(W1)
                
                mean0 = np.mean(W0)
                mean1 = np.mean(W1)
                
                # Harmonic mean of window sizes
                m = 1.0 / (1.0 / n0 + 1.0 / n1)
                
                # Dynamic threshold epsilon_cut (standard Hoeffding bound for ADWIN)
                epsilon_cut = np.sqrt((1.0 / (2.0 * m)) * np.log((4.0 * n) / self.delta))
                
                # Check statistical difference
                if np.abs(mean0 - mean1) > epsilon_cut:
                    drift_detected = True
                    drift_in_current_window = True
                    break
            
            if drift_in_current_window:
                # Discard older elements (W0) to adapt to the new concept
                self.window = self.window[i:]
            else:
                break
                
        return drift_detected

    def reset(self):
        """Clear window history."""
        self.window = []
