"""
eda.py
Exploratory Data Analysis for CIC-IoT-2023 / N-BaIoT.
Writes figures to <out_dir>/figures and a machine-readable eda_summary.json.
"""

import json
import os
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PALETTE = "#2a6fdb"


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def run_eda(X: pd.DataFrame, y: np.ndarray, info: Dict, prep, out_dir: str, title: str) -> Dict:
    fig_dir = os.path.join(out_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    classes = prep.classes_
    counts = pd.Series(y).value_counts().reindex(classes).fillna(0).astype(int)

    # 1. class distribution: raw (whole files read) vs sampled
    raw = info.get("raw_class_counts", {})
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
    if raw:
        rs = pd.Series(raw).sort_values(ascending=False)
        axes[0].bar(rs.index.astype(str), rs.values, color="#8aa9d6")
        axes[0].set_yscale("log")
        axes[0].set_title(f"{title}: raw rows read per label (log)")
        axes[0].tick_params(axis="x", rotation=60, labelsize=7)
    axes[1].bar(counts.index.astype(str), counts.values, color=PALETTE)
    axes[1].set_title(f"{title}: sampled dataset used ({len(y):,} rows)")
    axes[1].tick_params(axis="x", rotation=60, labelsize=8)
    _save(fig, os.path.join(fig_dir, "class_distribution.png"))

    # 2. correlation heatmap (non-constant input features, max 40)
    cols = [c for c in X.columns if c not in prep.dropped_constant]
    if len(cols) > 40:
        cols = list(prep.importances.keys())[:20] + [c for c, _, _ in prep.dropped_correlated][:20]
        cols = list(dict.fromkeys(cols))[:40]
    corr = X[cols].sample(n=min(len(X), 30000), random_state=0).corr().fillna(0)
    fig, ax = plt.subplots(figsize=(10, 8.5))
    im = ax.imshow(corr.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=90, fontsize=6)
    ax.set_yticks(range(len(cols)))
    ax.set_yticklabels(cols, fontsize=6)
    ax.set_title(f"{title}: Pearson correlation")
    fig.colorbar(im, ax=ax, fraction=0.04)
    _save(fig, os.path.join(fig_dir, "correlation_heatmap.png"))

    # 3. feature importance (ExtraTrees, train split)
    imp = pd.Series(prep.importances).head(20)[::-1]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(imp.index, imp.values, color=PALETTE)
    ax.set_title(f"{title}: top-20 feature importance (ExtraTrees)")
    _save(fig, os.path.join(fig_dir, "feature_importance.png"))

    # 4. distributions of the 6 most important features per class
    top = list(prep.importances.keys())[:6]
    fig, axes = plt.subplots(2, 3, figsize=(15, 7.5))
    for ax, f in zip(axes.ravel(), top):
        data = [np.log10(np.clip(X.loc[y == c, f].to_numpy(dtype=float), 0, None) + 1e-3) for c in classes]
        ax.boxplot(data, showfliers=False)
        ax.set_xticks(range(1, len(classes) + 1))
        ax.set_xticklabels(classes, rotation=60, fontsize=7)
        ax.set_title(f"log10({f})", fontsize=9)
    fig.suptitle(f"{title}: per-class distribution of key features")
    _save(fig, os.path.join(fig_dir, "feature_distributions.png"))

    desc = X[top].describe().T.round(4)
    summary = {
        "dataset": title,
        "n_rows": int(len(X)),
        "n_input_features": int(X.shape[1]),
        "class_counts": {str(k): int(v) for k, v in counts.items()},
        "imbalance_ratio_max_min": float(counts.max() / max(1, counts[counts > 0].min())),
        "cleaning": info.get("cleaning", {}),
        "dropped_constant": prep.dropped_constant,
        "n_dropped_correlated": len(prep.dropped_correlated),
        "dropped_correlated_examples": [list(t) for t in prep.dropped_correlated[:15]],
        "selected_features": prep.features,
        "log_transformed": [f for f, m in zip(prep.features, prep.log_mask) if m],
        "top_feature_stats": desc.to_dict(orient="index"),
        "figures": sorted(os.listdir(fig_dir)),
    }
    with open(os.path.join(out_dir, "eda_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    return summary
