"""
evaluate.py
Test-set evaluation, cost measurement and plots shared by every model.
"""

import io
import json
import os
import pickle
import time
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, precision_score, recall_score,
                             roc_auc_score, classification_report)


def model_size_bytes(model) -> int:
    if hasattr(model, "size_bytes"):
        return int(model.size_bytes())
    buf = io.BytesIO()
    pickle.dump(model, buf, protocol=pickle.HIGHEST_PROTOCOL)
    return buf.tell()


def measure_latency(model, X: np.ndarray, n_single: int = 200, batch: int = 1024) -> Dict[str, float]:
    """Median single-sample latency (ms) and batch throughput (samples/s) on CPU."""
    fn = model.predict_proba
    fn(X[:8])  # warm-up
    times = []
    for i in range(n_single):
        x = X[i % len(X): i % len(X) + 1]
        t0 = time.perf_counter()
        fn(x)
        times.append((time.perf_counter() - t0) * 1000)
    xb = X[:batch] if len(X) >= batch else np.resize(X, (batch, X.shape[1]))
    reps, t0 = 5, time.perf_counter()
    for _ in range(reps):
        fn(xb)
    thr = reps * len(xb) / (time.perf_counter() - t0)
    return {"latency_ms_single": float(np.median(times)), "throughput_per_s": float(thr)}


def evaluate_model(name: str, category: str, model, X_test, y_test, classes: List[str]) -> Dict:
    proba = np.asarray(model.predict_proba(X_test), dtype=np.float64)
    pred = proba.argmax(1)
    if hasattr(model, "classes_") and not isinstance(model.classes_, list):
        cl = np.asarray(model.classes_)
        if len(cl) == proba.shape[1]:
            pred = cl[pred]
            full = np.zeros((len(X_test), len(classes)))
            full[:, cl.astype(int)] = proba
            proba = full
    n = len(classes)
    try:
        if n == 2:
            auc = roc_auc_score(y_test, proba[:, 1])
        else:
            p = proba / np.clip(proba.sum(1, keepdims=True), 1e-12, None)
            auc = roc_auc_score(y_test, p, multi_class="ovr", average="macro", labels=list(range(n)))
    except ValueError:
        auc = float("nan")
    # measure deployment-style latency single-threaded (avoids joblib thread start-up per call)
    if hasattr(model, "n_jobs"):
        old = model.n_jobs
        model.n_jobs = 1
        cost = measure_latency(model, X_test)
        model.n_jobs = old
    else:
        cost = measure_latency(model, X_test)
    rep = classification_report(y_test, pred, labels=list(range(n)), target_names=classes,
                                output_dict=True, zero_division=0)
    return {
        "Model": name,
        "Category": category,
        "Accuracy": round(accuracy_score(y_test, pred) * 100, 3),
        "Precision_macro": round(precision_score(y_test, pred, average="macro", zero_division=0) * 100, 3),
        "Recall_macro": round(recall_score(y_test, pred, average="macro", zero_division=0) * 100, 3),
        "F1_macro": round(f1_score(y_test, pred, average="macro") * 100, 3),
        "F1_weighted": round(f1_score(y_test, pred, average="weighted") * 100, 3),
        "ROC_AUC_macro": round(float(auc) * 100, 3),
        "Latency_ms_single": round(cost["latency_ms_single"], 4),
        "Throughput_per_s": round(cost["throughput_per_s"], 1),
        "Size_KB": round(model_size_bytes(model) / 1024, 2),
        "Params": int(model.n_params()) if hasattr(model, "n_params") else None,
        "per_class_f1": {c: round(rep[c]["f1-score"] * 100, 2) for c in classes},
        "confusion_matrix": confusion_matrix(y_test, pred, labels=list(range(n))).tolist(),
    }


# ------------------------------------------------------------------ plots
def plot_confusion(cm, classes, title, path):
    cm = np.asarray(cm, dtype=float)
    norm = cm / np.clip(cm.sum(1, keepdims=True), 1, None)
    fig, ax = plt.subplots(figsize=(1.0 + 0.6 * len(classes), 0.8 + 0.55 * len(classes)))
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    for i in range(len(classes)):
        for j in range(len(classes)):
            ax.text(j, i, f"{int(cm[i, j])}", ha="center", va="center", fontsize=7,
                    color="white" if norm[i, j] > 0.5 else "black")
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels(classes, rotation=60, fontsize=8)
    ax.set_yticks(range(len(classes)))
    ax.set_yticklabels(classes, fontsize=8)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_history(histories: Dict[str, List[Dict]], title, path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for name, h in histories.items():
        ep = [r["epoch"] for r in h]
        axes[0].plot(ep, [r["train_loss"] for r in h], label=f"{name} train")
        axes[0].plot(ep, [r["val_loss"] for r in h], "--", label=f"{name} val")
        axes[1].plot(ep, [r["val_f1_macro"] * 100 for r in h], label=name)
    axes[0].set_title("Loss per epoch")
    axes[0].set_xlabel("epoch")
    axes[0].legend(fontsize=7)
    axes[1].set_title("Validation macro-F1 (%) per epoch")
    axes[1].set_xlabel("epoch")
    axes[1].legend(fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_comparison(results: List[Dict], title, path):
    names = [r["Model"] for r in results]
    colors = ["#8aa9d6" if r["Category"] == "ML Baseline" else "#2a6fdb" for r in results]
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.2))
    for ax, key, lab, log in ((axes[0], "F1_macro", "Macro-F1 (%)", False),
                              (axes[1], "Latency_ms_single", "Latency per sample (ms, log)", True),
                              (axes[2], "Size_KB", "Model size (KB, log)", True)):
        vals = [r[key] for r in results]
        ax.bar(names, vals, color=colors)
        if log:
            ax.set_yscale("log")
        else:
            lo = min(vals)
            ax.set_ylim(max(0, lo - (100 - lo) * 0.6 - 1), 100)
        ax.set_title(lab)
        ax.tick_params(axis="x", rotation=60, labelsize=8)
    fig.suptitle(title + "   (light = ML baseline, dark = deep learning)")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
