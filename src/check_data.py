"""
check_data.py
Verifies the downloaded REAL datasets before anything is trained.

    python src/check_data.py                 # checks datasets/CIC-IoT-2023 and datasets/N-BaIoT

For each dataset it reports, with a clear OK / WARN / ERROR verdict:
  * files found (count, size), archives that still need extracting
  * label column / label values recognised, full per-class row counts
  * N-BaIoT: device x class coverage, and whether the 115 CSV columns match
    the live AfterImage extractor used on the Kali gateway (names + order),
    plus a statistical consistency check of the H / HH / MI feature groups.
Exit code 1 if any ERROR is found (run_pipeline.py stops in that case).
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iot_datasets.common import find_files  # noqa: E402
from iot_datasets.cic_iot2023 import to_category, LABEL_CANDIDATES, CATEGORY_ORDER  # noqa: E402
from iot_datasets.n_baiot import _label_from_path, _device_from_path, CLASS_ORDER, DEVICES  # noqa: E402
from realtime.afterimage import nbaiot_feature_names, LAMBDA_TAGS  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Report:
    def __init__(self, name):
        self.name, self.items, self.data = name, [], {}

    def ok(self, msg):
        self.items.append(("OK", msg))

    def warn(self, msg):
        self.items.append(("WARN", msg))

    def err(self, msg):
        self.items.append(("ERROR", msg))

    @property
    def n_errors(self):
        return sum(1 for s, _ in self.items if s == "ERROR")

    def show(self):
        print(f"\n=== {self.name} ===")
        for s, m in self.items:
            print(f"  [{s:<5}] {m}")


def _size_gb(files):
    return sum(os.path.getsize(f) for f in files) / 1024 ** 3


def check_cic(folder: str) -> Report:
    r = Report("CIC-IoT-2023")
    if not os.path.isdir(folder):
        r.err(f"Folder not found: {folder}")
        return r
    archives = find_files(folder, ["*.zip", "*.rar", "*.7z", "*.gz"])
    csvs = find_files(folder, ["*.csv", "*.CSV"])
    if archives and not csvs:
        r.err(f"Only archives found ({len(archives)}), please extract them: {[os.path.basename(a) for a in archives[:3]]}")
        return r
    if not csvs:
        r.err("No CSV files. Download several CSV parts (part-*.csv or Merged*.csv) of CIC-IoT-2023 into this folder.")
        return r
    r.ok(f"{len(csvs)} CSV files, {_size_gb(csvs):.2f} GB")
    if len(csvs) < 3:
        r.warn("Fewer than 3 CSV parts: rare classes (Web, BruteForce) may have very few rows. 5-8 parts recommended.")
    fine, cats, bad_files, n_feats = Counter(), Counter(), [], set()
    for f in csvs:
        try:
            head = pd.read_csv(f, nrows=0)
            cols = [c.strip() for c in head.columns]
            lc = next((c for c in LABEL_CANDIDATES if c in cols), None)
            if lc is None:
                bad_files.append((os.path.basename(f), "no label column"))
                continue
            raw_lc = head.columns[cols.index(lc)]
            labels = pd.read_csv(f, usecols=[raw_lc])[raw_lc].astype(str).str.strip()
            fine.update(labels.value_counts().to_dict())
            n_feats.add(len(cols) - 1)
        except Exception as e:  # unreadable / truncated download
            bad_files.append((os.path.basename(f), str(e)[:80]))
    for b in bad_files:
        r.err(f"Cannot use {b[0]}: {b[1]}")
    for lbl, n in fine.items():
        cats[to_category(lbl)] += n
    unknown = sorted(l for l in fine if to_category(l) == "Other")
    if unknown:
        r.warn(f"Unrecognised labels (ignored as 'Other'): {unknown[:10]}")
    r.ok(f"{sum(fine.values()):,} rows, {len(fine)} fine-grained labels, feature columns per file: {sorted(n_feats)}")
    missing = [c for c in CATEGORY_ORDER if cats.get(c, 0) == 0]
    if missing:
        r.err(f"Categories with no rows: {missing} -> download more CSV parts")
    for c in CATEGORY_ORDER:
        n = cats.get(c, 0)
        if 0 < n < 500:
            r.warn(f"Category {c} has only {n} rows (results for this class will be noisy)")
    r.data = {"files": len(csvs), "rows": int(sum(fine.values())),
              "category_counts": {c: int(cats.get(c, 0)) for c in CATEGORY_ORDER},
              "fine_label_counts": {k: int(v) for k, v in sorted(fine.items())}}
    return r


def check_nbaiot(folder: str) -> Report:
    r = Report("N-BaIoT")
    if not os.path.isdir(folder):
        r.err(f"Folder not found: {folder}")
        return r
    csvs = [c for c in find_files(folder, ["*.csv", "*.CSV"]) if _label_from_path(c)]
    rars = find_files(folder, ["*.rar", "*.RAR"])
    zips = find_files(folder, ["*.zip"])
    extracted_dirs = {os.path.normcase(os.path.dirname(os.path.abspath(c))) for c in csvs}
    pending_rar = [x for x in rars if os.path.normcase(os.path.abspath(x[:-4])) not in extracted_dirs]
    if zips and not csvs:
        r.err(f"Zip not extracted: {[os.path.basename(z) for z in zips]} -> extract it into this folder")
        return r
    if pending_rar:
        try:
            import rarfile  # noqa: F401
            rarfile.tool_setup()
            r.warn(f"{len(pending_rar)} .rar archives not extracted; they will be read through rarfile")
        except Exception:
            r.err(f"{len(pending_rar)} .rar archives are not extracted and no unrar tool is available. "
                  f"Extract each gafgyt_attacks.rar / mirai_attacks.rar (WinRAR / 7-Zip, 'Extract to <name>\\'). "
                  f"Example: {os.path.relpath(pending_rar[0], folder)}")
    if not csvs:
        r.err("No N-BaIoT CSV files recognised (expected <Device>/benign_traffic.csv, "
              "<Device>/mirai_attacks/*.csv, <Device>/gafgyt_attacks/*.csv, or Kaggle n.*.csv)")
        return r
    r.ok(f"{len(csvs)} CSV files, {_size_gb(csvs):.2f} GB")

    cover = defaultdict(dict)
    expected = nbaiot_feature_names()
    header_problems, rows_per_class = [], Counter()
    for c in csvs:
        lbl, dev = _label_from_path(c), _device_from_path(c)
        cols = [x.strip() for x in pd.read_csv(c, nrows=0).columns]
        if cols != expected:
            header_problems.append((os.path.relpath(c, folder), cols))
        n = sum(1 for _ in open(c, "rb")) - 1
        cover[dev][lbl] = n
        rows_per_class[lbl] += n
    missing_classes = [c for c in CLASS_ORDER if rows_per_class.get(c, 0) == 0]
    if missing_classes:
        r.err(f"Classes with no file: {missing_classes}")
    else:
        r.ok(f"All 11 classes present, {sum(rows_per_class.values()):,} rows, {len(cover)} devices")
    mirai_less = [d for d in cover if not any(k.startswith("Mirai") for k in cover[d])]
    if mirai_less:
        r.warn(f"Devices without Mirai files: {mirai_less} (normal for Ennio_Doorbell and Samsung_SNH_1011_N_Webcam)")

    # header vs live extractor
    if not header_problems:
        r.ok("All CSV headers equal the 115 AfterImage feature names used on Kali (same names, same order)")
    else:
        f, cols = header_problems[0]
        miss = [x for x in expected if x not in cols]
        extra = [x for x in cols if x not in expected]
        if not miss and not extra:
            r.warn(f"{len(header_problems)} files have the same 115 names in a different order "
                   f"(OK: features are matched by name)")
        else:
            r.err(f"Header mismatch with the live extractor in {len(header_problems)} files, e.g. {f}: "
                  f"missing {miss[:4]}, unexpected {extra[:4]}")

    # statistical consistency of H / HH / MI groups (tells how the original extractor shared streams)
    benign = [c for c in csvs if _label_from_path(c) == "Benign"]
    if benign:
        df = pd.read_csv(benign[0], nrows=20000)
        df.columns = [x.strip() for x in df.columns]
        cons = {}
        for tag in LAMBDA_TAGS:
            try:
                h, hh, mi = df[f"H_{tag}_weight"], df[f"HH_{tag}_weight"], df[f"MI_dir_{tag}_weight"]
                cons[tag] = {
                    "HH_weight_equals_H_weight": float(np.mean(np.isclose(hh, h, rtol=1e-4))),
                    "HH_mean_equals_H_mean": float(np.mean(np.isclose(df[f"HH_{tag}_mean"], df[f"H_{tag}_mean"], rtol=1e-4))),
                    "MI_weight_equals_H_weight": float(np.mean(np.isclose(mi, h, rtol=1e-4))),
                    "median_HH_over_H_weight": float(np.median(hh / h.replace(0, np.nan))),
                }
            except KeyError:
                pass
        r.data["afterimage_consistency"] = cons
        if cons:
            l5 = cons.get("L0.01", next(iter(cons.values())))
            r.ok(f"AfterImage consistency (benign sample): HH==H weight in {l5['HH_weight_equals_H_weight']*100:.0f}% rows, "
                 f"MI==H weight in {l5['MI_weight_equals_H_weight']*100:.0f}% rows, median HH/H weight = "
                 f"{l5['median_HH_over_H_weight']:.3f}")
    r.data.update({"rows_per_class": {k: int(v) for k, v in rows_per_class.items()},
                   "device_class_rows": cover})
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.path.join(ROOT, "datasets"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "processed", "check_report.json"))
    ap.add_argument("--dataset", default="all", choices=["all", "cic_iot2023", "n_baiot"])
    a = ap.parse_args()
    reports = []
    if a.dataset in ("all", "cic_iot2023"):
        reports.append(check_cic(os.path.join(a.data_root, "CIC-IoT-2023")))
    if a.dataset in ("all", "n_baiot"):
        reports.append(check_nbaiot(os.path.join(a.data_root, "N-BaIoT")))
    for r in reports:
        r.show()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({r.name: {"checks": r.items, "data": r.data} for r in reports}, f, indent=2, default=str)
    n_err = sum(r.n_errors for r in reports)
    print(f"\n{'ALL CHECKS PASSED' if n_err == 0 else f'{n_err} ERROR(S) - fix them before training'}"
          f"  (report: {a.out})")
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
