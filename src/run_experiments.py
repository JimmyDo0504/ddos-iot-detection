"""
run_experiments.py
Step 2 of the pipeline: for each dataset
   EDA -> preprocessing -> class balancing -> 5 ML baselines -> 4 DL models
   -> test-set evaluation (accuracy, precision, recall, F1, ROC-AUC, confusion
   matrix, latency, throughput, size) -> comparison -> ONNX export + INT8.

All hyper-parameters are chosen on the VALIDATION split; the TEST split is
used once, for the final numbers.

Usage:
    python src/run_experiments.py --dataset all
    python src/run_experiments.py --dataset n_baiot --max-epochs 30
"""

import argparse
import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iot_datasets import DATASETS  # noqa: E402
from iot_datasets.cic_iot2023 import CATEGORY_ORDER  # noqa: E402
from iot_datasets.n_baiot import CLASS_ORDER  # noqa: E402
from prepare_data import prepare, sample_path  # noqa: E402
from preprocess import Preprocessor, stratified_split, balance_train  # noqa: E402
from eda import run_eda  # noqa: E402
from models.baselines import train_baselines  # noqa: E402
from models.deep import train_deep_models  # noqa: E402
from evaluate import evaluate_model, plot_confusion, plot_history, plot_comparison  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLASS_ORDERS = {"cic_iot2023": CATEGORY_ORDER, "n_baiot": CLASS_ORDER}


def onnx_export_and_quantize(model, X_test, y_test, out_dir, stem):
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType
    fp32 = os.path.join(out_dir, f"{stem}.onnx")
    int8 = os.path.join(out_dir, f"{stem}_int8.onnx")
    model.export_onnx(fp32)
    res = {}
    try:
        quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)
    except Exception as e:  # some conv graphs cannot be quantised dynamically
        print(f"    [warn] INT8 quantization failed: {e}")
        int8 = None
    for tag, path in (("fp32", fp32), ("int8", int8)):
        if not path:
            continue
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        name = sess.get_inputs()[0].name
        probs = sess.run(None, {name: X_test.astype(np.float32)})[0]
        acc = float((probs.argmax(1) == y_test).mean() * 100)
        x1 = X_test[:1].astype(np.float32)
        sess.run(None, {name: x1})
        ts = []
        for i in range(300):
            t0 = time.perf_counter()
            sess.run(None, {name: X_test[i:i + 1].astype(np.float32)})
            ts.append((time.perf_counter() - t0) * 1000)
        res[tag] = {"file": os.path.basename(path), "size_KB": round(os.path.getsize(path) / 1024, 2),
                    "accuracy": round(acc, 3), "latency_ms_single": round(float(np.median(ts)), 4)}
    return res


def run_dataset(name, args):
    spec = DATASETS[name]
    title = spec["title"]
    out = os.path.join(args.out, name)
    fig_dir = os.path.join(out, "figures")
    model_dir = os.path.join(out, "models")
    os.makedirs(fig_dir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)
    t_start = time.time()
    print("\n" + "=" * 78 + f"\n  DATASET: {title}\n" + "=" * 78)

    # ---------------------------------------------------------- load sample
    sp = sample_path(name, args.processed)
    if os.path.exists(sp) and not args.reload:
        df = pd.read_parquet(sp)
        with open(os.path.join(args.processed, f"{name}_info.json"), encoding="utf-8") as f:
            info = json.load(f)
        print(f"[+] Using cached sample {sp} ({len(df):,} rows)")
    else:
        df, info = prepare(name, args.data_root, args.processed, args.cap_per_class)
    vc = df["label"].astype(str).value_counts()
    rare = vc[vc < 10].index.tolist()
    if rare:
        print(f"[!] Dropping classes with < 10 rows (cannot be split/evaluated): {rare}")
        df = df[~df["label"].astype(str).isin(rare)].reset_index(drop=True)
    class_order = CLASS_ORDERS.get(name)
    leakage = {}
    if name == "n_baiot" and not args.keep_jit:
        # N-BaIoT/Kitsune artifact: the first inter-arrival of a new HH_jit stream is the ABSOLUTE
        # Unix timestamp (~1.5e9 s), so HH_jit_* encode WHEN a file was captured. Gafgyt_tcp and
        # Gafgyt_udp were recorded ~1000 s apart and are separable only through this timestamp
        # (temporal leakage). The live extractor does not reproduce it either -> drop the group.
        jit = [c for c in df.columns if c.startswith("HH_jit")]
        df = df.drop(columns=jit)
        leakage["dropped_features"] = jit
        print(f"[!] Leakage fix: dropped {len(jit)} timestamp-contaminated HH_jit_* features")
    if name == "n_baiot" and not args.no_merge_gafgyt_tcpudp:
        # Without the timestamp, Gafgyt_tcp and Gafgyt_udp have identical feature distributions
        # (every model, ML or DL, splits them ~0/66 F1). Merge them into one flood class.
        df["label"] = df["label"].astype(str).replace({"Gafgyt_tcp": "Gafgyt_tcpudp",
                                                       "Gafgyt_udp": "Gafgyt_tcpudp"})
        class_order = [c for c in class_order if c not in ("Gafgyt_tcp", "Gafgyt_udp")]
        class_order.insert(class_order.index("Gafgyt_scan") + 1, "Gafgyt_tcpudp")
        leakage["merged_classes"] = {"Gafgyt_tcpudp": ["Gafgyt_tcp", "Gafgyt_udp"]}
        print("[!] Merged Gafgyt_tcp + Gafgyt_udp -> Gafgyt_tcpudp (indistinguishable without the timestamp)")
    if name == "cic_iot2023" and not args.keep_cic_artifacts:
        # CIC-IoT-2023 artifact: "IAT" holds a capture-clock value (~8.3e7 / ~1.67e8 per recording
        # session, 0.0x for a few), not an inter-arrival time; alone it identifies the label far
        # better than any behavioural feature. "Number" and "Weight" take three discrete values
        # that follow the same recording sessions. Drop all three (temporal/session leakage).
        art = [c for c in ("IAT", "Number", "Weight") if c in df.columns]
        df = df.drop(columns=art)
        leakage["dropped_features"] = art
        print(f"[!] Leakage fix: dropped CIC-IoT-2023 session/timestamp artifacts {art}")
    # ============================================================ split
    meta_cols = [c for c in df.columns if c.startswith("_")]
    feat_cols = [c for c in df.columns if c != "label" and c not in meta_cols]
    df["label"] = df["label"].astype(str)
    audit = {"rows_after_feature_drop": int(len(df))}
    rowhash = pd.util.hash_pandas_object(df[feat_cols], index=False).to_numpy()
    audit["duplicate_feature_vectors"] = int(pd.Series(rowhash).duplicated().sum())
    temporal = "_src" in df.columns and args.split == "temporal"
    T = args.seq_len if temporal else 1
    tr_end, va_end, g = 1 - args.val - args.test, 1 - args.test, args.gap
    if temporal:
        # Chronological split INSIDE every capture file: first 70 % of the file -> train, next 15 % ->
        # validation, last 15 % -> test, with a small gap at both boundaries. Neighbouring packets
        # (almost identical feature vectors) can therefore never be on both sides of the split.
        df = df.sort_values(["_src", "_pos"]).reset_index(drop=True)
        rowhash = pd.util.hash_pandas_object(df[feat_cols], index=False).to_numpy()
        pos, blk = df["_pos"].to_numpy(), df["_blk"].to_numpy()
        region = np.full(len(df), -1)
        region[pos < tr_end - g] = 0
        region[(pos >= tr_end) & (pos < va_end - g)] = 1
        region[pos >= va_end] = 2
        key = blk * 4 + region
        newrun = np.r_[True, key[1:] != key[:-1]]
        run_start = np.flatnonzero(newrun)[np.cumsum(newrun) - 1]
        valid = (region >= 0) & (np.arange(len(df)) - run_start >= T - 1)   # full window, same block+split
        rows = np.flatnonzero(valid)
        split_of = region[rows]
        split_desc = (f"temporal per capture file: train pos<{tr_end-g:.2f}, val {tr_end:.2f}-{va_end-g:.2f}, "
                      f"test >={va_end:.2f}; windows of {T} consecutive packets")
    else:
        # rows are not time-ordered (CIC-IoT-2023): drop exact duplicate vectors (and vectors seen with
        # several labels) BEFORE a stratified random split, so no test vector also occurs in train.
        n0 = len(df)
        lab_per_vec = df.groupby(rowhash)["label"].nunique()
        conflict = set(lab_per_vec[lab_per_vec > 1].index)
        keep = ~pd.Series(rowhash).duplicated().to_numpy() & ~np.isin(rowhash, list(conflict))
        df, rowhash = df[keep].reset_index(drop=True), rowhash[keep]
        audit["dedup_removed"] = int(n0 - len(df))
        from sklearn.model_selection import train_test_split
        idx = np.arange(len(df))
        i_tmp, i_te = train_test_split(idx, test_size=args.test, stratify=df["label"], random_state=args.seed)
        i_tr, i_va = train_test_split(i_tmp, test_size=args.val / (1 - args.test),
                                      stratify=df["label"].iloc[i_tmp], random_state=args.seed)
        rows = np.concatenate([i_tr, i_va, i_te])
        split_of = np.concatenate([np.zeros(len(i_tr), int), np.ones(len(i_va), int), np.full(len(i_te), 2)])
        split_desc = "stratified random 70/15/15 after global de-duplication (no time order available)"
    # leakage guard: drop validation/test samples whose current-packet vector also occurs in train
    train_hashes = set(rowhash[rows[split_of == 0]])
    seen = np.array([h in train_hashes for h in rowhash[rows]]) & (split_of > 0)
    audit["val_test_rows_also_in_train_removed"] = int(seen.sum())
    rows, split_of = rows[~seen], split_of[~seen]
    audit["split"] = split_desc
    print(f"[+] Split: {split_desc}")
    print(f"[+] Leakage audit: {audit}")

    y_all = df["label"].to_numpy()
    X_all = df[feat_cols]
    tr_rows = rows[split_of == 0]
    prep = Preprocessor(corr_threshold=args.corr, top_k=args.top_k, seed=args.seed)
    prep.fit(X_all.iloc[tr_rows].reset_index(drop=True), y_all[tr_rows], class_order=class_order)
    classes = prep.classes_
    F = prep.transform(X_all)                                    # (N, n_features) every row
    D = F.shape[1]
    win = rows[:, None] + np.arange(-T + 1, 1)[None, :]           # (M, T) row indices of each window
    Xw = F[win].reshape(len(rows), T * D)
    yw = prep.encode(y_all[rows])
    X_tr, X_va, X_te = (Xw[split_of == k] for k in (0, 1, 2))
    y_tr, y_va, y_te = (yw[split_of == k] for k in (0, 1, 2))
    del Xw
    prep.save(os.path.join(model_dir, "preprocessor.json"))
    print(f"[+] Samples: train {len(y_tr):,} | val {len(y_va):,} | test {len(y_te):,}  "
          f"(window {T} x {D} features)")
    print(f"[+] Features: {len(prep.input_features)} input -> {len(prep.dropped_constant)} constant dropped, "
          f"{len(prep.dropped_correlated)} correlated dropped -> {len(prep.features)} selected")
    X_tr_df = X_all.iloc[tr_rows]

    # ---------------------------------------------------------------- EDA
    print("[+] EDA ...")
    eda = run_eda(X_all, y_all, info, prep, out, title)

    # ----------------------------------------------------------- balance
    X_trb, y_trb, bal = balance_train(X_tr, y_tr, cap=args.train_cap, floor=args.smote_floor, seed=args.seed)
    print(f"[+] Train balancing: {bal['before']} -> {bal['after']}")

    # ---------------------------------------------------------- baselines
    from models.deep import InputSlice
    width = X_trb.shape[1]
    print("[+] Training ML baselines on the current packet (selection on validation macro-F1) ...")
    base_raw, base_log = train_baselines(X_trb[:, width - D:], y_trb, X_va[:, width - D:], y_va,
                                         len(classes), seed=args.seed)
    base_models = {n: InputSlice(m, width - D, "row") for n, m in base_raw.items()}
    if T > 1:
        # fairness check: XGBoost may also look at the same packet window as the sequence models
        from xgboost import XGBClassifier
        t0 = time.time()
        xw = XGBClassifier(n_estimators=600, max_depth=6, learning_rate=0.1, subsample=0.9,
                           colsample_bytree=0.9, objective="multi:softprob", tree_method="hist", n_jobs=-1,
                           early_stopping_rounds=30, eval_metric="mlogloss", random_state=args.seed)
        xw.fit(X_trb, y_trb, eval_set=[(X_va, y_va)], verbose=False)
        from sklearn.metrics import f1_score as _f1
        base_models["XGBoost-Window"] = InputSlice(xw, 0, "window")
        base_log["XGBoost-Window"] = {"search": [], "selected": f"max_depth=6,lr=0.1,window={T}",
                                      "fit_seconds": round(time.time() - t0, 2)}
        print(f"    [XGBoost-Window] val macro-F1 = {_f1(y_va, xw.predict(X_va), average='macro')*100:6.2f}%")

    # --------------------------------------------------------------- deep
    print("[+] Training deep-learning models (early stopping on validation macro-F1) ...")
    seq_len, d_in = (T, D) if T > 1 else (D, 1)
    deep_models, deep_log = train_deep_models(X_trb, y_trb, X_va, y_va, len(classes), seq_len=seq_len,
                                              d=d_in, row_dim=D, seed=args.seed, max_epochs=args.max_epochs,
                                              verbose=args.verbose)

    # ----------------------------------------------------------- evaluate
    print("[+] Evaluating on the held-out TEST split ...")
    results = []
    import torch
    for n, m in base_models.items():
        r = evaluate_model(n, "ML Baseline", m, X_te, y_te, classes)
        r["selected_params"] = base_log[n]["selected"]
        r["Train_seconds"] = base_log[n]["fit_seconds"]
        r["Input"] = m.input_kind
        results.append(r)
        with open(os.path.join(model_dir, f"{n.replace(' ', '_').replace('(', '').replace(')', '')}.pkl"), "wb") as f:
            pickle.dump(m, f)
    for n, m in deep_models.items():
        r = evaluate_model(n, "Deep Learning", m, X_te, y_te, classes)
        r["selected_params"] = deep_log[n]["selected"]
        r["Train_seconds"] = deep_log[n]["fit_seconds"]
        r["best_epoch"] = m.best_epoch
        r["Input"] = m.input_kind
        results.append(r)
        torch.save({"arch": m.arch, "params": m.params, "n_in": m.n_in, "n_out": m.n_out,
                    "start": m.start, "input_kind": m.input_kind,
                    "state_dict": m.net.state_dict()}, os.path.join(model_dir, f"{n}.pt"))
    for r in results:
        print(f"    {r['Model']:<16} {r['Category']:<13} Acc {r['Accuracy']:7.3f}  F1 {r['F1_macro']:7.3f}  "
              f"AUC {r['ROC_AUC_macro']:7.3f}  lat {r['Latency_ms_single']:8.4f} ms  size {r['Size_KB']:10.1f} KB")

    # --------------------------------------------- requirement #5 (honest)
    base = [r for r in results if r["Category"] == "ML Baseline"]
    deep = [r for r in results if r["Category"] == "Deep Learning"]
    bb = max(base, key=lambda r: r["F1_macro"])
    bd = max(deep, key=lambda r: r["F1_macro"])
    gap = round(bd["F1_macro"] - bb["F1_macro"], 3)
    req = {"best_baseline": bb["Model"], "best_baseline_f1": bb["F1_macro"],
           "best_deep": bd["Model"], "best_deep_f1": bd["F1_macro"], "gap_f1": gap, "passed": gap > 0,
           "best_baseline_acc": bb["Accuracy"], "best_deep_acc": bd["Accuracy"]}
    print(f"[+] Requirement #5: best DL {bd['Model']} F1 {bd['F1_macro']:.3f} vs best baseline "
          f"{bb['Model']} F1 {bb['F1_macro']:.3f} -> gap {gap:+.3f} -> {'PASS' if gap > 0 else 'NOT MET'}")

    # ------------------------------------------------------------ export
    onnx_res = {}
    all_models = {**base_models, **deep_models}
    if name == "n_baiot":
        # N-BaIoT features are reproducible live (AfterImage) -> every model is exported for Kali
        import shutil
        from export_models import export_all
        dep = os.path.join(ROOT, "deploy_kali", "model")
        os.makedirs(dep, exist_ok=True)
        print(f"[+] Exporting all {len(all_models)} models to ONNX -> {dep}")
        default = "BiGRU-Attention" if "BiGRU-Attention" in all_models else bd["Model"]
        onnx_res = export_all(all_models, results, X_te, y_te, classes, dep, seq_len=T, n_features=D,
                              dataset=title, default_model=default,
                              extra={"split": split_desc, "leakage_audit": audit})
        shutil.copy2(os.path.join(model_dir, "preprocessor.json"), os.path.join(dep, "preprocessor.json"))
        print(f"[+] Deploy bundle (registry.json + {len(all_models)} ONNX models) written to {dep}")

    # -------------------------------------------------------------- plots
    plot_comparison(results, f"{title}: test-set comparison", os.path.join(fig_dir, "model_comparison.png"))
    plot_confusion(bb["confusion_matrix"], classes, f"{title} - {bb['Model']} (best baseline)",
                   os.path.join(fig_dir, "cm_best_baseline.png"))
    plot_confusion(bd["confusion_matrix"], classes, f"{title} - {bd['Model']} (best DL)",
                   os.path.join(fig_dir, "cm_best_dl.png"))
    plot_history({n: deep_log[n]["history"] for n in deep_log}, f"{title}: DL training curves",
                 os.path.join(fig_dir, "dl_training_curves.png"))

    summary = {
        "dataset": title, "classes": classes, "n_classes": len(classes),
        "splits": {"train": int(len(y_tr)), "val": int(len(y_va)), "test": int(len(y_te)),
                   "train_after_balancing": int(len(y_trb))},
        "balancing": bal, "features_selected": prep.features, "results": results,
        "baseline_search": base_log,
        "deep_search": {k: {kk: vv for kk, vv in v.items() if kk != "history"} for k, v in deep_log.items()},
        "deep_history": {k: v["history"] for k, v in deep_log.items()},
        "requirement_5": req, "onnx": onnx_res, "eda": eda, "data_info": info, "leakage_fix": leakage,
        "leakage_audit": audit, "split": split_desc, "window": T,
        "runtime_minutes": round((time.time() - t_start) / 60, 1),
        "config": vars(args),
    }
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    # markdown table
    lines = [f"## {title}", "", "| Model | Category | Accuracy | Precision | Recall | F1-macro | ROC-AUC | Latency (ms) | Size (KB) |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['Model']} | {r['Category']} | {r['Accuracy']:.2f} | {r['Precision_macro']:.2f} | "
                     f"{r['Recall_macro']:.2f} | {r['F1_macro']:.2f} | {r['ROC_AUC_macro']:.2f} | "
                     f"{r['Latency_ms_single']:.4f} | {r['Size_KB']:.1f} |")
    with open(os.path.join(out, "results_table.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[+] Done {title} in {summary['runtime_minutes']} min -> {out}")
    return summary


def build_parser():
    ap = argparse.ArgumentParser(description="Run the full ML vs DL experiment on real IoT datasets")
    ap.add_argument("--dataset", default="all", choices=["all"] + list(DATASETS.keys()))
    ap.add_argument("--data-root", default=os.path.join(ROOT, "datasets"))
    ap.add_argument("--processed", default=os.path.join(ROOT, "data", "processed"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results"))
    ap.add_argument("--cap-per-class", type=int, default=40000, help="rows kept per class when sampling raw files")
    ap.add_argument("--train-cap", type=int, default=20000, help="max train rows per class after split")
    ap.add_argument("--smote-floor", type=int, default=3000, help="SMOTE rare train classes up to this many rows")
    ap.add_argument("--top-k", type=int, default=30, help="number of features kept after selection")
    ap.add_argument("--corr", type=float, default=0.98, help="correlation threshold for dropping features")
    ap.add_argument("--max-epochs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reload", action="store_true", help="re-read raw files even if a cached sample exists")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--split", default="temporal", choices=["temporal", "random"],
                    help="temporal: chronological split inside every capture file (N-BaIoT)")
    ap.add_argument("--seq-len", type=int, default=10, help="packets per window for sequence models")
    ap.add_argument("--val", type=float, default=0.15)
    ap.add_argument("--test", type=float, default=0.15)
    ap.add_argument("--gap", type=float, default=0.01, help="gap (fraction of file) around split boundaries")
    ap.add_argument("--keep-jit", action="store_true",
                    help="N-BaIoT: keep the HH_jit_* features (they leak the capture timestamp)")
    ap.add_argument("--keep-cic-artifacts", action="store_true",
                    help="CIC-IoT-2023: keep IAT/Number/Weight (capture-session leakage)")
    ap.add_argument("--no-merge-gafgyt-tcpudp", action="store_true",
                    help="N-BaIoT: keep Gafgyt_tcp and Gafgyt_udp as separate classes")
    return ap


def default_args(**overrides):
    """Default configuration as a Namespace (used by the notebook)."""
    a = build_parser().parse_args([])
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


if __name__ == "__main__":
    a = build_parser().parse_args()
    names = list(DATASETS.keys()) if a.dataset == "all" else [a.dataset]
    all_req = {}
    for n in names:
        all_req[n] = run_dataset(n, a)["requirement_5"]
    print("\n==== SUMMARY (requirement #5: DL > baseline on the same dataset) ====")
    for n, r in all_req.items():
        print(f"  {n:<12} best DL {r['best_deep']} {r['best_deep_f1']:.3f} | best baseline "
              f"{r['best_baseline']} {r['best_baseline_f1']:.3f} | gap {r['gap_f1']:+.3f} | "
              f"{'PASS' if r['passed'] else 'NOT MET'}")
