#!/usr/bin/env python3
"""
Example script for running workload classification pipeline.

This demonstrates how to:
1. Load Azure Functions data
2. Extract features using sliding windows
3. Apply weak supervision for labeling
4. Train a LightGBM classifier
5. Evaluate classification performance
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from pathlib import Path
import logging
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix
import joblib

from src.data.loader import AzureFunctionsDataLoader
from src.data.windowing import SlidingWindowProcessor
from src.data.features import SimpleFeatureExtractor
from src.models.labeling import WeakSupervisionLabeler
from src.models.classifier import WorkloadClassifier

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run the classification pipeline."""
    # Configuration
    data_dir = Path("data/raw")
    output_dir = Path("models")
    output_dir.mkdir(exist_ok=True)
    
    # Parameters
    window_size = 60  # 60 minutes
    stride = 10       # 10 minute stride
    min_invocations = 1000  # Filter low-activity functions
    
    logger.info("Starting workload classification pipeline")
    
    # Step 1: Load data
    logger.info("Loading Azure Functions data...")
    loader = AzureFunctionsDataLoader(data_dir)
    
    # Load first 9 days for training
    train_data = loader.load_invocation_data(days=list(range(1, 10)))
    logger.info(f"Loaded {len(train_data)} functions for training")
    
    # Filter low-activity functions
    filtered_data = {}
    for func_hash, time_series in train_data.items():
        if np.sum(time_series) >= min_invocations:
            filtered_data[func_hash] = time_series
    
    logger.info(f"Filtered to {len(filtered_data)} active functions")
    
    # Step 2: Create sliding windows
    logger.info("Creating sliding windows...")
    window_processor = SlidingWindowProcessor(
        window_size=window_size,
        stride=stride
    )
    
    all_windows = []
    window_metadata = []
    
    for func_hash, time_series in filtered_data.items():
        windows = window_processor.create_windows(time_series)
        for i, window in enumerate(windows):
            all_windows.append(window)
            window_metadata.append({
                'function_hash': func_hash,
                'window_index': i
            })
    
    logger.info(f"Created {len(all_windows)} windows")
    
    # Step 3: Extract features
    logger.info("Extracting features...")
    feature_extractor = SimpleFeatureExtractor()
    
    features_list = []
    for window in all_windows:
        features = feature_extractor.extract_features(window)
        features_list.append(features)
    
    feature_df = pd.DataFrame(features_list)
    logger.info(f"Extracted {len(feature_df.columns)} features")
    
    # Step 4: Apply weak supervision
    logger.info("Applying weak supervision labeling...")
    labeler = WeakSupervisionLabeler()
    
    labels = []
    for window in all_windows:
        label = labeler.label_window(window)
        labels.append(label)
    
    # Convert labels to numeric
    label_map = {
        'PERIODIC': 0,
        'SPIKE': 1,
        'STATIONARY_NOISY': 2,
        'RAMP': 3
    }
    numeric_labels = [label_map[label] for label in labels]
    
    # Print label distribution
    label_counts = pd.Series(labels).value_counts()
    logger.info("Label distribution:")
    for label, count in label_counts.items():
        logger.info(f"  {label}: {count} ({count/len(labels)*100:.1f}%)")
    
    # Step 5: Train classifier
    logger.info("Training LightGBM classifier...")
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        feature_df, numeric_labels, test_size=0.2, random_state=42
    )
    
    # Train model
    classifier = WorkloadClassifier(n_estimators=100, random_state=42)
    classifier.fit(X_train, y_train)
    
    # Step 6: Evaluate
    logger.info("Evaluating classifier...")
    y_pred = classifier.predict(X_test)
    
    # Print classification report
    target_names = ['PERIODIC', 'SPIKE', 'STATIONARY_NOISY', 'RAMP']
    report = classification_report(y_test, y_pred, target_names=target_names)
    logger.info(f"\\nClassification Report:\\n{report}")
    
    # Print confusion matrix
    cm = confusion_matrix(y_test, y_pred)
    logger.info(f"\\nConfusion Matrix:\\n{cm}")
    
    # Calculate accuracy
    accuracy = classifier.score(X_test, y_test)
    logger.info(f"\\nOverall accuracy: {accuracy:.3f}")
    
    # Step 7: Save model and artifacts
    logger.info("Saving model and artifacts...")
    
    # Save classifier
    model_path = output_dir / "lightgbm_classifier.pkl"
    joblib.dump(classifier, model_path)
    logger.info(f"Saved classifier to {model_path}")
    
    # Save feature names
    feature_names_path = output_dir / "feature_names.txt"
    with open(feature_names_path, 'w') as f:
        for feature in feature_df.columns:
            f.write(f"{feature}\\n")
    logger.info(f"Saved feature names to {feature_names_path}")
    
    # Save label mapping
    label_map_path = output_dir / "label_map.json"
    import json
    with open(label_map_path, 'w') as f:
        json.dump(label_map, f, indent=2)
    logger.info(f"Saved label mapping to {label_map_path}")
    
    logger.info("Classification pipeline completed successfully!")


if __name__ == "__main__":
    main()