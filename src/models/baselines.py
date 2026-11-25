"""
baselines.py
Baseline machine-learning models (scikit-learn / XGBoost) with a small
hyper-parameter search. Every candidate is trained on the TRAIN split and
scored on the VALIDATION split (macro-F1); the best one is kept. The test
split is never touched here.
"""

import time
from typing import Dict, List, Tuple

import numpy as np
from sklearn.metrics import f1_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier


class SoftmaxSVC:
    """RBF-SVM trained on a stratified subsample; probabilities = softmax(decision_function)."""

    def __init__(self, C=10.0, gamma="scale", max_train=20000, seed=42):
        self.C, self.gamma, self.max_train, self.seed = C, gamma, max_train, seed
        self.model = SVC(C=C, gamma=gamma, kernel="rbf", decision_function_shape="ovr",
                         class_weight="balanced", random_state=seed)

    def fit(self, X, y):
        if len(X) > self.max_train:
            rng = np.random.RandomState(self.seed)
            idx = []
            per = max(1, self.max_train // len(np.unique(y)))
            for c in np.unique(y):
                ci = np.where(y == c)[0]
                idx.append(rng.choice(ci, size=min(per, len(ci)), replace=False))
            idx = np.concatenate(idx)
            X, y = X[idx], y[idx]
        self.model.fit(X, y)
        self.classes_ = self.model.classes_
        return self

    def predict(self, X):
        return self.model.predict(X)

    def predict_proba(self, X):
        d = self.model.decision_function(X)
        if d.ndim == 1:
            d = np.stack([-d, d], axis=1)
        e = np.exp(d - d.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)


def _candidates(n_classes: int, seed: int) -> Dict[str, List[Tuple[str, object]]]:
    xgb_obj = "multi:softprob" if n_classes > 2 else "binary:logistic"
    return {
        "Decision Tree": [
            (f"max_depth={d}", DecisionTreeClassifier(max_depth=d, class_weight="balanced", random_state=seed))
            for d in (10, 20, None)
        ],
        "KNN": [
            (f"k={k}", KNeighborsClassifier(n_neighbors=k, weights="distance", n_jobs=-1))
            for k in (3, 5, 9)
        ],
        "Random Forest": [
            (f"n=100,max_depth={d}", RandomForestClassifier(n_estimators=100, max_depth=d, min_samples_leaf=2,
                                                              n_jobs=-1, class_weight="balanced", random_state=seed))
            for d in (20, None)
        ],
        "XGBoost": [
            (f"max_depth={d},lr={lr}", XGBClassifier(n_estimators=600, max_depth=d, learning_rate=lr,
                                                      subsample=0.9, colsample_bytree=0.9,
                                                      objective=xgb_obj, tree_method="hist", n_jobs=-1,
                                                      early_stopping_rounds=30, eval_metric="mlogloss"
                                                      if n_classes > 2 else "logloss", random_state=seed))
            for d in (6, 10) for lr in (0.1,)
        ],
        "SVM (RBF)": [
            (f"C={C}", SoftmaxSVC(C=C, seed=seed)) for C in (1.0, 10.0)
        ],
    }


def train_baselines(X_tr, y_tr, X_val, y_val, n_classes: int, seed: int = 42,
                    knn_max_train: int = 60000, verbose: bool = True) -> Tuple[Dict[str, object], Dict]:
    best_models, log = {}, {}
    for name, cands in _candidates(n_classes, seed).items():
        results = []
        best = (-1.0, None, None)
        for tag, model in cands:
            Xf, yf = X_tr, y_tr
            if name == "KNN" and len(X_tr) > knn_max_train:
                idx = np.random.RandomState(seed).choice(len(X_tr), knn_max_train, replace=False)
                Xf, yf = X_tr[idx], y_tr[idx]
            t0 = time.time()
            if name == "XGBoost":
                model.fit(Xf, yf, eval_set=[(X_val, y_val)], verbose=False)
            else:
                model.fit(Xf, yf)
            fit_s = time.time() - t0
            f1 = f1_score(y_val, model.predict(X_val), average="macro")
            results.append({"params": tag, "val_f1_macro": round(float(f1) * 100, 3), "fit_seconds": round(fit_s, 2)})
            if verbose:
                print(f"    [{name:<13}] {tag:<22} val macro-F1 = {f1*100:6.2f}%   ({fit_s:.1f}s)")
            if f1 > best[0]:
                best = (f1, model, tag)
        best_models[name] = best[1]
        log[name] = {"search": results, "selected": best[2], "fit_seconds": next(
            r["fit_seconds"] for r in results if r["params"] == best[2])}
    return best_models, log
