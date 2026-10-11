"""
CHIMERA Conformalized Quantile Regression (CQR) Uncertainty Module

This module implements Conformalized Quantile Regression to provide
non-parametric, distribution-free confidence intervals for workload demand.
It replaces AAPA's Beta calibration.
"""

import numpy as np
import logging
from typing import Dict, Tuple, Optional, List
import lightgbm as lgb
import os
import pickle

logger = logging.getLogger(__name__)

class ConformalQuantileRegressor:
    """
    Conformalized Quantile Regression (CQR) model for workload demand forecasting.
    Provides mathematical bounds [L_t, U_t] with distribution-free coverage guarantees.
    """
    
    def __init__(self, alpha: float = 0.1, calibration_size: int = 100):
        """
        Initialize CQR module.
        
        Args:
            alpha: Significance level (coverage probability = 1 - alpha)
            calibration_size: Size of rolling window for conformal calibration
        """
        self.alpha = alpha
        self.calibration_size = calibration_size
        self.calibration_scores = []
        
        # We need two quantile models: lower and upper bounds
        self.model_low = None
        self.model_high = None
        self.scaler = None
        
    def fit(self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray):
        """
        Train the lower and upper quantile regression models.
        """
        logger.info(f"Training CQR quantile models (alpha={self.alpha})...")
        
        # Calculate target quantiles
        q_low = self.alpha / 2.0
        q_high = 1.0 - (self.alpha / 2.0)
        
        # Train lower quantile model (q_low)
        params_low = {
            'objective': 'quantile',
            'alpha': q_low,
            'metric': 'quantile',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'verbose': -1,
            'num_threads': -1
        }
        train_data_low = lgb.Dataset(X_train, label=y_train)
        val_data_low = lgb.Dataset(X_val, label=y_val, reference=train_data_low)
        self.model_low = lgb.train(
            params_low,
            train_data_low,
            valid_sets=[val_data_low],
            num_boost_round=200,
            callbacks=[lgb.early_stopping(30, verbose=False)]
        )
        
        # Train upper quantile model (q_high)
        params_high = {
            'objective': 'quantile',
            'alpha': q_high,
            'metric': 'quantile',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'verbose': -1,
            'num_threads': -1
        }
        train_data_high = lgb.Dataset(X_train, label=y_train)
        val_data_high = lgb.Dataset(X_val, label=y_val, reference=train_data_high)
        self.model_high = lgb.train(
            params_high,
            train_data_high,
            valid_sets=[val_data_high],
            num_boost_round=200,
            callbacks=[lgb.early_stopping(30, verbose=False)]
        )
        
        # Populate calibration score buffer using validation set
        logger.info("Initializing conformal calibration buffer...")
        self.calibrate(X_val, y_val)
        
    def calibrate(self, X_cal: np.ndarray, y_cal: np.ndarray):
        """
        Populate the calibration scores.
        Implements conformity score calculation (Eq. 2.6).
        """
        # Predict quantiles on calibration data
        pred_low = self.model_low.predict(X_cal)
        pred_high = self.model_high.predict(X_cal)
        
        # Conformity score: S_i = max(q_low(x) - y_i, y_i - q_high(x))
        scores = np.maximum(pred_low - y_cal, y_cal - pred_high)
        self.calibration_scores = list(scores[-self.calibration_size:])
        logger.info(f"Conformal buffer initialized with {len(self.calibration_scores)} scores.")
        
    def update_calibration_buffer(self, actual_val: float, pred_low: float, pred_high: float):
        """
        Add a new observed prediction error to the conformal rolling calibration window.
        """
        score = max(pred_low - actual_val, actual_val - pred_high)
        self.calibration_scores.append(score)
        if len(self.calibration_scores) > self.calibration_size:
            self.calibration_scores.pop(0)

    def predict_conformal_bounds(self, X_test: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Compute conformal interval [L_t, U_t] for given features.
        Implements conformity correction percentile selection.
        """
        # Ensure 2D input
        if len(X_test.shape) == 1:
            X_test = X_test.reshape(1, -1)
            
        pred_low = self.model_low.predict(X_test)
        pred_high = self.model_high.predict(X_test)
        
        # Get correction term E_t as (1-alpha) percentile of calibration scores
        if len(self.calibration_scores) > 0:
            E_t = np.percentile(self.calibration_scores, 100 * (1.0 - self.alpha))
        else:
            E_t = 0.0
            
        # Conformal bounds (Eq. 2.6)
        L_t = pred_low - E_t
        U_t = pred_high + E_t
        
        # Enforce physical workload non-negativity
        L_t = np.maximum(0.0, L_t)
        U_t = np.maximum(0.0, U_t)
        
        uncertainty_width = float(np.mean(U_t - L_t))
        return L_t, U_t, uncertainty_width

    def save(self, filepath_prefix: str):
        """Save low and high quantile models to file."""
        os.makedirs(os.path.dirname(filepath_prefix), exist_ok=True)
        self.model_low.save_model(filepath_prefix + "_low.txt")
        self.model_high.save_model(filepath_prefix + "_high.txt")
        with open(filepath_prefix + "_meta.pkl", "wb") as f:
            pickle.dump({
                'alpha': self.alpha,
                'calibration_size': self.calibration_size,
                'calibration_scores': self.calibration_scores
            }, f)
            
    def load(self, filepath_prefix: str):
        """Load models from file."""
        self.model_low = lgb.Booster(model_file=filepath_prefix + "_low.txt")
        self.model_high = lgb.Booster(model_file=filepath_prefix + "_high.txt")
        with open(filepath_prefix + "_meta.pkl", "rb") as f:
            meta = pickle.load(f)
            self.alpha = meta['alpha']
            self.calibration_size = meta['calibration_size']
            self.calibration_scores = meta['calibration_scores']
