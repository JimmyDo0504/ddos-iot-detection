"""
preprocess.py
Dataset-agnostic preprocessing for the IoT intrusion datasets.

Everything that is *learned* (dropped columns, selected features, log mask,
scaler statistics, label encoding) is fitted on the TRAIN split only, so the
validation/test splits never leak into preprocessing. The fitted state is
serialised to JSON so the real-time Kali gateway applies exactly the same
transformation before inference.

Steps
  1. Stratified split 70 / 15 / 15
  2. Drop constant features
  3. Drop highly correlated features (|Pearson r| > threshold, greedy)
  4. Feature selection: keep the top-k features by ExtraTrees importance
  5. log1p on non-negative heavy-tailed features (|skew| > 2)
  6. StandardScaler
  7. Class balancing on TRAIN only: undersample huge classes + SMOTE rare ones
"""

import json
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.model_selection import train_test_split


def stratified_split(X: pd.DataFrame, y: np.ndarray, val: float = 0.15, test: float = 0.15, seed: int = 42):
    X_tmp, X_test, y_tmp, y_test = train_test_split(X, y, test_size=test, stratify=y, random_state=seed)
    rel_val = val / (1.0 - test)
    X_train, X_val, y_train, y_val = train_test_split(X_tmp, y_tmp, test_size=rel_val, stratify=y_tmp,
                                                      random_state=seed)
    return (X_train.reset_index(drop=True), y_train, X_val.reset_index(drop=True), y_val,
            X_test.reset_index(drop=True), y_test)


class Preprocessor:
    def __init__(self, corr_threshold: float = 0.98, top_k: Optional[int] = 30,
                 skew_threshold: float = 2.0, seed: int = 42):
        self.corr_threshold = corr_threshold
        self.top_k = top_k
        self.skew_threshold = skew_threshold
        self.seed = seed
        self.input_features: List[str] = []
        self.dropped_constant: List[str] = []
        self.dropped_correlated: List[Tuple[str, str, float]] = []
        self.importances: Dict[str, float] = {}
        self.features: List[str] = []
        self.log_mask: List[bool] = []
        self.mean_: Optional[np.ndarray] = None
        self.scale_: Optional[np.ndarray] = None
        self.classes_: List[str] = []

    # ------------------------------------------------------------------ fit
    def fit(self, X: pd.DataFrame, y: np.ndarray, class_order: Optional[List[str]] = None):
        self.input_features = list(X.columns)
        present = set(map(str, y))
        if class_order:
            self.classes_ = [c for c in class_order if c in present] + sorted(present - set(class_order))
        else:
            self.classes_ = sorted(present)

        std = X.std(axis=0)
        self.dropped_constant = [c for c in X.columns if not np.isfinite(std[c]) or std[c] == 0]
        cols = [c for c in X.columns if c not in self.dropped_constant]

        # correlation filter (greedy: keep the first of each highly-correlated pair)
        sample = X[cols].sample(n=min(len(X), 50000), random_state=self.seed)
        corr = sample.corr().abs().fillna(0.0).to_numpy()
        keep, dropped = [], []
        for i, c in enumerate(cols):
            hit = None
            for j in keep:
                if corr[i, j] > self.corr_threshold:
                    hit = j
                    break
            if hit is None:
                keep.append(i)
            else:
                dropped.append((c, cols[hit], float(corr[i, hit])))
        self.dropped_correlated = dropped
        cols = [cols[i] for i in keep]

        # importance-based selection (ExtraTrees on a train subsample)
        n = min(len(X), 60000)
        idx = np.random.RandomState(self.seed).choice(len(X), size=n, replace=False)
        et = ExtraTreesClassifier(n_estimators=100, n_jobs=-1, random_state=self.seed,
                                  class_weight="balanced")
        et.fit(X[cols].iloc[idx].to_numpy(dtype=np.float32), np.asarray(y)[idx])
        imp = dict(zip(cols, et.feature_importances_.astype(float)))
        self.importances = dict(sorted(imp.items(), key=lambda kv: -kv[1]))
        if self.top_k and self.top_k < len(cols):
            self.features = list(self.importances.keys())[: self.top_k]
        else:
            self.features = list(self.importances.keys())

        F = X[self.features].to_numpy(dtype=np.float64)
        skew = pd.DataFrame(F).skew().to_numpy()
        self.log_mask = [bool(F[:, k].min() >= 0 and abs(skew[k]) > self.skew_threshold)
                         for k in range(F.shape[1])]
        T = self._log(F)
        self.mean_ = T.mean(axis=0)
        self.scale_ = T.std(axis=0)
        self.scale_[self.scale_ == 0] = 1.0
        return self

    # ------------------------------------------------------------ transform
    def _log(self, F: np.ndarray) -> np.ndarray:
        F = F.copy()
        m = np.asarray(self.log_mask)
        if m.any():
            F[:, m] = np.log1p(np.clip(F[:, m], 0, None))
        return F

    def transform(self, X) -> np.ndarray:
        if isinstance(X, pd.DataFrame):
            F = X[self.features].to_numpy(dtype=np.float64)
        else:  # already-ordered raw array of input_features
            idx = [self.input_features.index(f) for f in self.features]
            F = np.asarray(X, dtype=np.float64)[:, idx]
        return ((self._log(F) - self.mean_) / self.scale_).astype(np.float32)

    def encode(self, y: np.ndarray) -> np.ndarray:
        lut = {c: i for i, c in enumerate(self.classes_)}
        return np.array([lut[str(v)] for v in y], dtype=np.int64)

    # ---------------------------------------------------------------- io
    def to_dict(self) -> Dict:
        return {
            "input_features": self.input_features,
            "features": self.features,
            "log_mask": self.log_mask,
            "mean": self.mean_.tolist(),
            "scale": self.scale_.tolist(),
            "classes": self.classes_,
            "corr_threshold": self.corr_threshold,
            "top_k": self.top_k,
            "dropped_constant": self.dropped_constant,
            "dropped_correlated": [list(t) for t in self.dropped_correlated],
            "importances": self.importances,
        }

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Preprocessor":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        p = cls(corr_threshold=d["corr_threshold"], top_k=d["top_k"])
        p.input_features, p.features, p.log_mask = d["input_features"], d["features"], d["log_mask"]
        p.mean_, p.scale_ = np.array(d["mean"]), np.array(d["scale"])
        p.classes_ = d["classes"]
        return p


def balance_train(X: np.ndarray, y: np.ndarray, cap: int, floor: int, seed: int = 42) -> Tuple[np.ndarray, np.ndarray, Dict]:
    """
    Class balancing applied to the TRAIN split only:
      * random undersampling of classes larger than `cap`
      * SMOTE oversampling of classes smaller than `floor` (up to `floor`)
    Validation and test keep their natural distribution.
    """
    rng = np.random.RandomState(seed)
    before = {int(c): int(n) for c, n in zip(*np.unique(y, return_counts=True))}
    keep = []
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        if len(idx) > cap:
            idx = rng.choice(idx, size=cap, replace=False)
        keep.append(idx)
    keep = np.concatenate(keep)
    Xb, yb = X[keep], y[keep]
    counts = {int(c): int(n) for c, n in zip(*np.unique(yb, return_counts=True))}
    targets = {c: floor for c, n in counts.items() if 1 < n < floor}
    smote_used = False
    if targets:
        from imblearn.over_sampling import SMOTE
        k = max(1, min(5, min(counts[c] for c in targets) - 1))
        Xb, yb = SMOTE(sampling_strategy=targets, k_neighbors=k, random_state=seed).fit_resample(Xb, yb)
        smote_used = True
    perm = rng.permutation(len(yb))
    after = {int(c): int(n) for c, n in zip(*np.unique(yb, return_counts=True))}
    return Xb[perm].astype(np.float32), yb[perm], {"before": before, "after": after, "cap": cap,
                                                    "smote_floor": floor, "smote_used": smote_used}
