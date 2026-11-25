"""
export_models.py
Exports EVERY trained model (ML baselines + deep-learning models) to ONNX so that the Kali gateway can
switch between them at run time with onnxruntime only (no scikit-learn / xgboost / torch on Kali).

Output (deploy directory):
    registry.json            list of models: file, input kind (row/window), seq_len, test metrics
    preprocessor.json        shared feature pipeline (selection + log1p + scaling)
    <Model>.onnx             one file per model (+ <Model>_int8.onnx for DL models when quantisable)

Input kinds
    row    : features of the current packet (n_features)
    window : the last seq_len packets of the same host, flattened (seq_len * n_features)
"""

import json
import os
import time
from typing import Dict, List

import numpy as np


def safe_name(n: str) -> str:
    return n.replace(" ", "_").replace("(", "").replace(")", "")


def _softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def onnx_scores(sess, X: np.ndarray) -> np.ndarray:
    """Run an exported model and return (N, C) class probabilities whatever its output layout."""
    name = sess.get_inputs()[0].name
    outs = sess.run(None, {name: X.astype(np.float32)})
    mats = [o for o in outs if isinstance(o, np.ndarray) and o.ndim == 2 and o.dtype.kind == "f"]
    P = mats[-1].astype(np.float64)
    if P.min() < -1e-6 or np.abs(P.sum(1) - 1).max() > 1e-3:   # decision scores (SVM) -> softmax
        P = _softmax(P)
    return P


def _export_sklearn(est, n_in: int, path: str):
    from xgboost import XGBClassifier
    if isinstance(est, XGBClassifier):
        import onnxmltools
        from onnxmltools.convert.common.data_types import FloatTensorType as FT
        onx = onnxmltools.convert_xgboost(est, initial_types=[("input", FT([None, n_in]))], target_opset=15)
    else:
        from skl2onnx import to_onnx
        from skl2onnx.common.data_types import FloatTensorType
        from sklearn.svm import SVC
        opts = None if isinstance(est, SVC) else {id(est): {"zipmap": False}}
        onx = to_onnx(est, initial_types=[("input", FloatTensorType([None, n_in]))], options=opts,
                      target_opset=17)
    with open(path, "wb") as f:
        f.write(onx.SerializeToString())


def _latency_ms(sess, X1: np.ndarray, n=200) -> float:
    name = sess.get_inputs()[0].name
    sess.run(None, {name: X1})
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, {name: X1})
        ts.append((time.perf_counter() - t0) * 1000)
    return float(np.median(ts))


def export_all(models: Dict[str, object], results: List[Dict], X_test: np.ndarray, y_test: np.ndarray,
               classes: List[str], out_dir: str, seq_len: int, n_features: int, dataset: str,
               default_model: str, extra: Dict = None) -> Dict:
    """
    models  : name -> InputSlice wrapper (has .model, .start, .input_kind)
    X_test  : flat window matrix used for evaluation (last n_features columns = current packet)
    """
    import onnxruntime as ort
    os.makedirs(out_dir, exist_ok=True)
    by_name = {r["Model"]: r for r in results}
    reg = {"dataset": dataset, "classes": classes, "seq_len": seq_len, "n_features": n_features,
           "default_model": default_model, "models": []}
    if extra:
        reg.update(extra)
    sample = slice(0, min(len(X_test), 20000))
    for name, w in models.items():
        kind = w.input_kind
        Xs = X_test[sample, w.start:] if w.start else X_test[sample]
        n_in = Xs.shape[1]
        fn = safe_name(name) + ".onnx"
        path = os.path.join(out_dir, fn)
        entry = {"name": name, "file": fn, "input": kind, "n_in": int(n_in),
                 "category": by_name.get(name, {}).get("Category", "")}
        try:
            inner = w.model
            if hasattr(inner, "export_onnx"):          # TorchClassifier
                inner.export_onnx(path)
            else:
                est = getattr(inner, "model", inner)   # SoftmaxSVC wraps an sklearn SVC
                _export_sklearn(est, n_in, path)
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            P = onnx_scores(sess, Xs)
            ref = np.asarray(w.predict(X_test[sample]))
            entry["onnx_agreement_pct"] = round(float((P.argmax(1) == ref).mean() * 100), 3)
            entry["onnx_accuracy_pct"] = round(float((P.argmax(1) == y_test[sample]).mean() * 100), 3)
            entry["size_KB"] = round(os.path.getsize(path) / 1024, 1)
            entry["onnx_latency_ms"] = round(_latency_ms(sess, Xs[:1]), 4)
            r = by_name.get(name, {})
            for k in ("Accuracy", "Precision_macro", "Recall_macro", "F1_macro", "ROC_AUC_macro"):
                if k in r:
                    entry["test_" + k] = r[k]
            if hasattr(inner, "export_onnx"):           # dynamic INT8 for DL models (optional)
                try:
                    from onnxruntime.quantization import quantize_dynamic, QuantType
                    p8 = os.path.join(out_dir, safe_name(name) + "_int8.onnx")
                    quantize_dynamic(path, p8, weight_type=QuantType.QInt8)
                    s8 = ort.InferenceSession(p8, providers=["CPUExecutionProvider"])
                    P8 = onnx_scores(s8, Xs)
                    entry["int8"] = {"file": os.path.basename(p8), "size_KB": round(os.path.getsize(p8) / 1024, 1),
                                     "accuracy_pct": round(float((P8.argmax(1) == y_test[sample]).mean() * 100), 3)}
                except Exception as e:
                    entry["int8"] = {"error": str(e)[:120]}
            print(f"    [export] {name:<18} {kind:<6} {entry['size_KB']:>9.1f} KB  "
                  f"agree {entry['onnx_agreement_pct']:.2f}%  acc {entry['onnx_accuracy_pct']:.2f}%", flush=True)
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"    [export] {name}: FAILED {entry['error']}", flush=True)
        reg["models"].append(entry)
    with open(os.path.join(out_dir, "registry.json"), "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)
    return reg
