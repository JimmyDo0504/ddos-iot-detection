"""
cic_iot2023.py
Loader for the CIC-IoT-2023 dataset (Canadian Institute for Cybersecurity, UNB).

Source : https://www.unb.ca/cic/datasets/iotdataset-2023.html
Paper  : Neto et al., "CICIoT2023: A Real-Time Dataset and Benchmark for
         Large-Scale Attacks in IoT Environment", Sensors 23(13), 2023.

Testbed: 105 real IoT devices, 33 attacks in 7 categories + benign traffic.
Files  : many CSV parts (e.g. "part-00000-...-c000.csv" or "Merged01.csv"),
         ~46 flow features + a "label" column with 34 fine-grained labels.

The loader is schema-tolerant: it accepts both the original release
(flow_duration, Header_Length, Protocol Type, Duration, Rate, Srate, ...,
Magnitue, Radius, Covariance, Variance, Weight, label) and later re-exports
(e.g. with Time_To_Live / IGMP / attack_class columns). Every numeric column
except the label columns is used as a feature.
"""

import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from iot_datasets.common import find_files, PerClassSampler, clean_numeric, FileCache

LABEL_CANDIDATES = ["label", "Label", "LABEL"]
EXTRA_LABEL_COLS = ["attack_class", "Attack_class", "class", "Class", "category", "Category"]

# Fine-grained label (34) -> attack category (8), following the CICIoT2023 paper.
WEB_ATTACKS = {"BACKDOOR_MALWARE", "BROWSERHIJACKING", "COMMANDINJECTION",
               "SQLINJECTION", "UPLOADING_ATTACK", "XSS"}
SPOOFING = {"DNS_SPOOFING", "MITM-ARPSPOOFING"}
CATEGORY_ORDER = ["Benign", "DDoS", "DoS", "Mirai", "Recon", "Spoofing", "Web", "BruteForce"]


def to_category(label: str) -> str:
    """Map a fine-grained CICIoT2023 label to one of the 8 categories."""
    s = str(label).strip()
    u = s.upper()
    if u.startswith("BENIGN"):
        return "Benign"
    if u.startswith("DDOS"):
        return "DDoS"
    if u.startswith("DOS"):
        return "DoS"
    if u.startswith("MIRAI"):
        return "Mirai"
    if u.startswith("RECON") or u == "VULNERABILITYSCAN":
        return "Recon"
    if u in SPOOFING or "SPOOF" in u:
        return "Spoofing"
    if u in WEB_ATTACKS:
        return "Web"
    if "BRUTEFORCE" in u:
        return "BruteForce"
    return "Other"


def _detect_label_col(columns: List[str]) -> str:
    for c in LABEL_CANDIDATES:
        if c in columns:
            return c
    raise ValueError(f"No label column found among {columns[-5:]}")


def load_cic_iot2023(
    root: str,
    cap_per_class: int = 40000,
    label_mode: str = "8class",
    max_files: Optional[int] = None,
    seed: int = 42,
    verbose: bool = True,
    cache_dir: Optional[str] = None,
    time_budget: Optional[float] = None,
) -> Tuple[pd.DataFrame, np.ndarray, List[str], Dict]:
    """
    Load and sample CIC-IoT-2023.

    label_mode:
      "8class"  : Benign + 7 attack categories (default, as in the paper)
      "34class" : the 34 original labels
      "binary"  : Benign vs Attack
    Returns (X_df, y_labels, feature_names, info_dict).
    """
    files = find_files(root, ["*.csv", "*.CSV"])
    if not files:
        raise FileNotFoundError(
            f"No CSV files found under '{root}'. Download CIC-IoT-2023 CSV parts "
            f"(part-*.csv or Merged*.csv) into this folder.")
    if max_files:
        files = files[:max_files]

    sampler = PerClassSampler(cap=cap_per_class, seed=seed)
    cache = FileCache(cache_dir, cap_per_class, time_budget, seed)
    feature_cols: Optional[List[str]] = None
    label_col = None
    total_rows = 0
    for i, f in enumerate(files):
        header = pd.read_csv(f, nrows=0)
        header.columns = [c.strip() for c in header.columns]
        cols = list(header.columns)
        lc = _detect_label_col(cols)
        drop = set(LABEL_CANDIDATES) | set(EXTRA_LABEL_COLS)
        fcols = [c for c in cols if c not in drop]
        if feature_cols is None:
            feature_cols, label_col = fcols, lc
        elif fcols != feature_cols:
            # keep only the common columns if a part has a slightly different schema
            feature_cols = [c for c in feature_cols if c in fcols]
        cached, meta = cache.get(f, label_mode)
        if cached is not None:
            df, n_raw, raw_counts = cached, meta["rows"], meta["raw_counts"]
            df = df[[c for c in feature_cols if c in df.columns] + ["label"]]
        else:
            df = pd.read_csv(f, low_memory=False)
            df.columns = [c.strip() for c in df.columns]
            df = df[[c for c in feature_cols if c in df.columns] + [lc]].rename(columns={lc: "label"})
            if label_mode == "8class":
                df["label"] = df["label"].map(to_category)
                df = df[df["label"] != "Other"]          # unrecognised labels are excluded
            elif label_mode == "binary":
                df["label"] = np.where(df["label"].map(to_category) == "Benign", "Benign", "Attack")
            else:
                df["label"] = df["label"].astype(str).str.strip()
            n_raw = len(df)
            raw_counts = {str(k): int(v) for k, v in df["label"].value_counts().items()}
            df = cache.presample(df)
            cache.put(f, df, {"rows": n_raw, "raw_counts": raw_counts}, label_mode)
        total_rows += n_raw
        sampler.add(df, "label", raw_counts)
        if verbose:
            print(f"    [CIC-IoT-2023] {i+1}/{len(files)} {os.path.basename(f)}: {n_raw:,} rows"
                  f"{' (cached)' if cached is not None else ''}")
        cache.check_budget(i + 1, len(files))

    data = sampler.result()
    data = data[[c for c in feature_cols if c in data.columns] + ["label"]]
    data, clean_report = clean_numeric(data, [c for c in feature_cols if c in data.columns])
    feature_cols = [c for c in feature_cols if c in data.columns]
    y = data["label"].to_numpy()
    X = data[feature_cols]

    info = {
        "dataset": "CIC-IoT-2023",
        "source": "https://www.unb.ca/cic/datasets/iotdataset-2023.html",
        "files_used": [os.path.basename(f) for f in files],
        "raw_rows_read": int(total_rows),
        "raw_class_counts": {k: int(v) for k, v in sorted(sampler.seen.items())},
        "cap_per_class": cap_per_class,
        "label_mode": label_mode,
        "cleaning": clean_report,
    }
    return X, y, feature_cols, info
