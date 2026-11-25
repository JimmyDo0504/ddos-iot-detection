"""
common.py
Shared helpers for loading real IoT intrusion datasets (CIC-IoT-2023, N-BaIoT).

All loaders return a pandas DataFrame of numeric features plus a 1-D array of
string labels, so that EDA / preprocessing / training are dataset-agnostic.
"""

import os
import glob
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def find_files(root: str, patterns: List[str]) -> List[str]:
    """Recursively find files under `root` matching any glob pattern (case-insensitive on Windows)."""
    found = []
    for pat in patterns:
        found.extend(glob.glob(os.path.join(root, "**", pat), recursive=True))
    # de-duplicate, stable order
    seen, out = set(), []
    for f in sorted(found):
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


class PerClassSampler:
    """
    Streaming per-class sampler with a cap.

    Rows are added chunk by chunk (one CSV file at a time). Each class keeps at
    most `cap` rows; when a class overflows we randomly subsample it back to
    `cap`. Rare classes therefore keep ALL their rows, while huge classes are
    capped -> memory stays bounded even for multi-GB datasets.
    """

    def __init__(self, cap: int, seed: int = 42):
        self.cap = cap
        self.rng = np.random.RandomState(seed)
        self.parts: Dict[str, List[pd.DataFrame]] = {}
        self.sizes: Dict[str, int] = {}
        self.seen: Dict[str, int] = {}  # raw rows seen per class (before capping)

    def add(self, df: pd.DataFrame, label_col: str, raw_counts: Optional[Dict[str, int]] = None):
        if raw_counts:
            for lbl, n in raw_counts.items():
                self.seen[lbl] = self.seen.get(lbl, 0) + int(n)
        for lbl, g in df.groupby(label_col, sort=False):
            lbl = str(lbl)
            if not raw_counts:
                self.seen[lbl] = self.seen.get(lbl, 0) + len(g)
            self.parts.setdefault(lbl, []).append(g)
            self.sizes[lbl] = self.sizes.get(lbl, 0) + len(g)
            if self.sizes[lbl] > 2 * self.cap:
                self._shrink(lbl)

    def _shrink(self, lbl: str):
        merged = pd.concat(self.parts[lbl], ignore_index=True)
        if len(merged) > self.cap:
            idx = self.rng.choice(len(merged), size=self.cap, replace=False)
            merged = merged.iloc[np.sort(idx)].reset_index(drop=True)
        self.parts[lbl] = [merged]
        self.sizes[lbl] = len(merged)

    def result(self) -> pd.DataFrame:
        frames = []
        for lbl in list(self.parts.keys()):
            self._shrink(lbl)
            frames.append(self.parts[lbl][0])
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        return out.sample(frac=1.0, random_state=42).reset_index(drop=True)


def clean_numeric(df: pd.DataFrame, feature_cols: List[str]) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Basic data cleaning shared by every dataset:
      * coerce features to numeric
      * +/-inf -> NaN, then drop rows containing NaN
      * drop exact duplicate rows (features + label)
    Returns the cleaned frame and a small report dict for the EDA section.
    """
    report = {"rows_in": int(len(df))}
    df = df.copy()
    for c in feature_cols:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    feats = df[feature_cols]
    n_inf = int(np.isinf(feats.to_numpy(dtype=np.float64)).sum())
    df[feature_cols] = feats.replace([np.inf, -np.inf], np.nan)
    n_nan_rows = int(df[feature_cols].isna().any(axis=1).sum())
    df = df.dropna(subset=feature_cols)
    n_before = len(df)
    df = df.drop_duplicates()
    report.update({
        "inf_values": n_inf,
        "rows_with_nan_or_inf_dropped": n_nan_rows,
        "duplicate_rows_dropped": int(n_before - len(df)),
        "rows_out": int(len(df)),
    })
    for c in feature_cols:
        df[c] = df[c].astype(np.float32)
    return df.reset_index(drop=True), report


# ---------------------------------------------------------------------------
# Per-file cache: makes reading multi-GB raw folders resumable. Each raw file is
# read once, its rows are capped per class and stored as a small parquet file.
# A later run (or a run interrupted by a time budget) reuses those parts.
# ---------------------------------------------------------------------------
import hashlib
import json as _json
import time as _time


class TimeBudgetExceeded(RuntimeError):
    """Raised when the per-run time budget is used up; progress is kept in the cache."""


class FileCache:
    def __init__(self, cache_dir: Optional[str], cap: int, time_budget: Optional[float] = None, seed: int = 42):
        self.dir = cache_dir
        self.cap = cap
        self.budget = time_budget
        self.t0 = _time.time()
        self.seed = seed
        self.new_files = 0
        if self.dir:
            os.makedirs(self.dir, exist_ok=True)

    def _key(self, path: str, extra: str = "") -> str:
        st = os.stat(path)
        h = hashlib.md5(f"{os.path.abspath(path)}|{extra}|{st.st_size}|{int(st.st_mtime)}|{self.cap}".encode()).hexdigest()
        return os.path.join(self.dir, h[:16])

    def get(self, path: str, extra: str = ""):
        if not self.dir:
            return None, None
        k = self._key(path, extra)
        if os.path.exists(k + ".parquet") and os.path.exists(k + ".json"):
            with open(k + ".json", encoding="utf-8") as f:
                meta = _json.load(f)
            return pd.read_parquet(k + ".parquet"), meta
        return None, None

    def presample(self, df: pd.DataFrame, label_col: str = "label") -> pd.DataFrame:
        parts = []
        for _, g in df.groupby(label_col, sort=False):
            parts.append(g.sample(n=self.cap, random_state=self.seed) if len(g) > self.cap else g)
        return pd.concat(parts, ignore_index=True) if parts else df

    def put(self, path: str, df: pd.DataFrame, meta: Dict, extra: str = ""):
        if not self.dir:
            return
        k = self._key(path, extra)
        df.to_parquet(k + ".parquet", index=False)
        with open(k + ".json", "w", encoding="utf-8") as f:
            _json.dump(meta, f)
        self.new_files += 1

    def check_budget(self, done: int, total: int):
        if self.budget and _time.time() - self.t0 > self.budget and done < total:
            raise TimeBudgetExceeded(f"{done}/{total} files processed; run the same command again to continue")
