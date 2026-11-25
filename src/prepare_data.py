"""
prepare_data.py
Step 1 of the pipeline: read the raw dataset files (possibly several GB),
apply per-class capped sampling + cleaning, and store a compact sample in
data/processed/<dataset>_sample.parquet together with a JSON info file.

Usage:
    python src/prepare_data.py --dataset all --data-root datasets --cap-per-class 40000
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iot_datasets import DATASETS  # noqa: E402
from iot_datasets.common import TimeBudgetExceeded  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def sample_path(name: str, out_dir: str) -> str:
    return os.path.join(out_dir, f"{name}_sample.parquet")


def prepare(name: str, data_root: str, out_dir: str, cap: int, seed: int = 42, time_budget=None):
    spec = DATASETS[name]
    folder = os.path.join(data_root, spec["folder"])
    print(f"\n[+] Loading {spec['title']} from {folder} (cap {cap:,} rows / class)")
    X, y, feats, info = spec["loader"](folder, cap_per_class=cap, label_mode=spec["label_mode"], seed=seed,
                                       cache_dir=os.path.join(out_dir, "_cache", name), time_budget=time_budget)
    os.makedirs(out_dir, exist_ok=True)
    df = X.copy()
    df["label"] = y
    df.to_parquet(sample_path(name, out_dir), index=False)
    with open(os.path.join(out_dir, f"{name}_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"[+] Saved {len(df):,} rows x {len(feats)} features -> {sample_path(name, out_dir)}")
    print("    class counts:", df["label"].value_counts().to_dict())
    return df, info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="all", choices=["all"] + list(DATASETS.keys()))
    ap.add_argument("--data-root", default=os.path.join(ROOT, "datasets"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "processed"))
    ap.add_argument("--cap-per-class", type=int, default=40000)
    ap.add_argument("--time-budget", type=float, default=None,
                    help="stop after this many seconds and resume on the next run (progress is cached)")
    a = ap.parse_args()
    names = list(DATASETS.keys()) if a.dataset == "all" else [a.dataset]
    for n in names:
        try:
            prepare(n, a.data_root, a.out, a.cap_per_class, time_budget=a.time_budget)
        except TimeBudgetExceeded as e:
            print(f"[PARTIAL] {n}: {e}")
            sys.exit(3)
