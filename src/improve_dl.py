"""
improve_dl.py
Thử nghiệm cải thiện các mô hình DL tuần tự trên N-BaIoT, sau khi BỎ lớp quá ít mẫu test (Gafgyt_tcp/udp -> Gafgyt_tcpudp
chỉ còn 5 mẫu test). Mọi biến thể dùng cùng dữ liệu, cùng cách chia theo thời gian, chống rò rỉ như run_experiments.py.

Usage:  python src/improve_dl.py --variant base|win20|reg|win20reg [--archs LSTM BiGRU-Attention]

Biến thể:
  base     cửa sổ 10 gói, siêu tham số mặc định (đối chứng cùng ngân sách huấn luyện)
  win20    cửa sổ 20 gói
  reg      cửa sổ 10 gói + chống quá khớp (dropout cao, weight decay lớn, nhiễu Gaussian, label smoothing, lr nhỏ)
  win20reg cửa sổ 20 gói + chống quá khớp
"""
import argparse
import copy
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iot_datasets.n_baiot import CLASS_ORDER  # noqa: E402
from models.deep import ARCHS, TorchClassifier  # noqa: E402
from preprocess import Preprocessor, balance_train  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DROP_CLASSES = ["Gafgyt_tcp", "Gafgyt_udp"]  # sau khi bỏ HH_jit hai lớp này không tách được và chỉ còn 5 mẫu test

VARIANTS = {
    "base":     dict(T=10, drop_lstm=0.2, drop_att=0.3, wd=1e-4, noise=0.0, ls=0.0, lr=1e-3),
    "win20":    dict(T=20, drop_lstm=0.2, drop_att=0.3, wd=1e-4, noise=0.0, ls=0.0, lr=1e-3),
    "reg":      dict(T=10, drop_lstm=0.4, drop_att=0.4, wd=1e-2, noise=0.15, ls=0.05, lr=5e-4),
    "win20reg": dict(T=20, drop_lstm=0.4, drop_att=0.4, wd=1e-2, noise=0.15, ls=0.05, lr=5e-4),
    # vòng 2: lr thấp + cắt gradient để huấn luyện ổn định hơn với cửa sổ dài
    "w20lr":    dict(T=20, drop_lstm=0.2, drop_att=0.3, wd=1e-4, noise=0.0, ls=0.0, lr=2e-4, clip=1.0),
    "w30lr":    dict(T=30, drop_lstm=0.2, drop_att=0.3, wd=1e-4, noise=0.0, ls=0.0, lr=2e-4, clip=1.0),
    "w10lr":    dict(T=10, drop_lstm=0.2, drop_att=0.3, wd=1e-4, noise=0.0, ls=0.0, lr=2e-4, clip=1.0),
}


class RegClassifier(TorchClassifier):
    """TorchClassifier + weight decay, Gaussian input noise and label smoothing (training only)."""

    def __init__(self, *a, wd=1e-4, noise=0.0, ls=0.0, clip=0.0, **kw):
        super().__init__(*a, **kw)
        self.wd, self.noise, self.ls, self.clip = wd, noise, ls, clip

    def fit(self, X, y, X_val, y_val, verbose=True):
        Xt, yt = torch.from_numpy(X), torch.from_numpy(y)
        Xv, yv = torch.from_numpy(X_val), torch.from_numpy(y_val)
        counts = np.bincount(y, minlength=self.n_out).astype(np.float64)
        w = np.where(counts > 0, (counts.sum() / (self.n_out * np.maximum(counts, 1))) ** 0.5, 0.0)
        wt = torch.tensor(w, dtype=torch.float32)
        crit = nn.CrossEntropyLoss(weight=wt, label_smoothing=self.ls)
        vcrit = nn.CrossEntropyLoss(weight=wt)
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=self.wd)
        sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="max", factor=0.5, patience=2)
        best_f1, best_state, wait = -1.0, None, 0
        g = torch.Generator().manual_seed(self.seed)
        t0 = time.time()
        for ep in range(1, self.max_epochs + 1):
            self.net.train()
            perm = torch.randperm(len(Xt), generator=g)
            tot, nb = 0.0, 0
            for i in range(0, len(Xt), self.batch):
                b = perm[i:i + self.batch]
                if len(b) < 2:
                    continue
                xb = Xt[b]
                if self.noise > 0:
                    xb = xb + self.noise * torch.randn_like(xb)
                opt.zero_grad()
                loss = crit(self.net(xb), yt[b])
                loss.backward()
                if self.clip > 0:
                    nn.utils.clip_grad_norm_(self.net.parameters(), self.clip)
                opt.step()
                tot += loss.item()
                nb += 1
            self.net.eval()
            with torch.no_grad():
                logits = self._logits(Xv)
                vloss = float(vcrit(logits, yv))
                vpred = logits.argmax(1).numpy()
            vf1 = float(f1_score(y_val, vpred, average="macro"))
            sched.step(vf1)
            self.history.append({"epoch": ep, "train_loss": tot / max(1, nb), "val_loss": vloss,
                                 "val_acc": float((vpred == y_val).mean()), "val_f1_macro": vf1})
            if verbose:
                print(f"      ep {ep:2d} loss {tot/max(1,nb):.4f} | val loss {vloss:.4f} "
                      f"acc {self.history[-1]['val_acc']*100:.2f}% F1 {vf1*100:.2f}%", flush=True)
            if vf1 > best_f1 + 1e-5:
                best_f1, best_state, wait, self.best_epoch = vf1, copy.deepcopy(self.net.state_dict()), 0, ep
            else:
                wait += 1
                if wait >= self.patience:
                    break
        self.net.load_state_dict(best_state)
        self.fit_seconds = time.time() - t0
        self.best_val_f1 = best_f1
        return self


def build_data(T, seed=42, val=0.15, test=0.15, gap=0.01, top_k=30, corr=0.98):
    df = pd.read_parquet(os.path.join(ROOT, "data", "processed", "n_baiot_sample.parquet"))
    df["label"] = df["label"].astype(str)
    df = df.drop(columns=[c for c in df.columns if c.startswith("HH_jit")])
    df = df[~df["label"].isin(DROP_CLASSES)].reset_index(drop=True)
    class_order = [c for c in CLASS_ORDER if c not in DROP_CLASSES]
    meta = [c for c in df.columns if c.startswith("_")]
    feat_cols = [c for c in df.columns if c != "label" and c not in meta]
    tr_end, va_end, g = 1 - val - test, 1 - test, gap
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
    valid = (region >= 0) & (np.arange(len(df)) - run_start >= T - 1)
    rows = np.flatnonzero(valid)
    split_of = region[rows]
    train_hashes = set(rowhash[rows[split_of == 0]])
    seen = np.array([h in train_hashes for h in rowhash[rows]]) & (split_of > 0)
    rows, split_of = rows[~seen], split_of[~seen]
    y_all = df["label"].to_numpy()
    X_all = df[feat_cols]
    tr_rows = rows[split_of == 0]
    prep = Preprocessor(corr_threshold=corr, top_k=top_k, seed=seed)
    prep.fit(X_all.iloc[tr_rows].reset_index(drop=True), y_all[tr_rows], class_order=class_order)
    F = prep.transform(X_all)
    D = F.shape[1]
    win = rows[:, None] + np.arange(-T + 1, 1)[None, :]
    Xw = F[win].reshape(len(rows), T * D)
    yw = prep.encode(y_all[rows])
    sp = [(Xw[split_of == k], yw[split_of == k]) for k in (0, 1, 2)]
    return sp, prep.classes_, D


def metrics(y, P, classes):
    pred = P.argmax(1)
    p, r, f, sup = precision_recall_fscore_support(y, pred, labels=list(range(len(classes))), zero_division=0)
    return {"Accuracy": round(accuracy_score(y, pred) * 100, 3), "Precision_macro": round(p.mean() * 100, 3),
            "Recall_macro": round(r.mean() * 100, 3), "F1_macro": round(f.mean() * 100, 3),
            "per_class_f1": {c: round(v * 100, 2) for c, v in zip(classes, f)},
            "support": {c: int(s) for c, s in zip(classes, sup)},
            "confusion_matrix": confusion_matrix(y, pred, labels=list(range(len(classes)))).tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=list(VARIANTS))
    ap.add_argument("--archs", nargs="+", default=["LSTM", "BiGRU-Attention"])
    ap.add_argument("--train-cap", type=int, default=12000)
    ap.add_argument("--max-epochs", type=int, default=30)
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--tag", default=None, help="tên file kết quả (mặc định = variant)")
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    v = VARIANTS[a.variant]
    T = v["T"]
    out_dir = os.path.join(ROOT, "results", "n_baiot_improve")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    (sp, classes, D) = build_data(T, seed=a.seed)
    (Xtr, ytr), (Xva, yva), (Xte, yte) = sp
    print(f"[{a.variant}] classes={classes}\n  train {len(ytr):,} val {len(yva):,} test {len(yte):,}  window {T}x{D}", flush=True)
    Xb, yb, bal = balance_train(Xtr, ytr, cap=a.train_cap, floor=3000, seed=a.seed)
    print(f"  balanced train {len(yb):,}  test support {np.bincount(yte).tolist()}", flush=True)
    res = {"variant": a.variant, "config": v, "classes": classes, "models": {}, "train_cap": a.train_cap,
           "test_support": np.bincount(yte).tolist()}
    probs = {"y_test": yte}
    for arch in a.archs:
        hidden, drop = (96, v["drop_att"]) if arch == "BiGRU-Attention" else (64, v["drop_lstm"])
        params = {"hidden": hidden, "dropout": drop, "seq_len": T, "d": D}
        print(f"  [{arch}] {params}", flush=True)
        m = RegClassifier(arch, Xb.shape[1], len(classes), params, lr=v["lr"], max_epochs=a.max_epochs,
                          patience=a.patience, seed=a.seed, wd=v["wd"], noise=v["noise"], ls=v["ls"], clip=v.get("clip", 0.0))
        m.fit(Xb, yb, Xva, yva)
        P = m.predict_proba(Xte)
        Pv = m.predict_proba(Xva)
        r = metrics(yte, P, classes)
        r.update({"val_F1_macro": round(f1_score(yva, Pv.argmax(1), average="macro") * 100, 3),
                  "best_epoch": m.best_epoch, "epochs_run": len(m.history), "fit_seconds": round(m.fit_seconds, 1),
                  "params": params, "history": m.history})
        res["models"][arch] = r
        probs[arch] = P
        probs[arch + "_val"] = Pv
        print(f"  [{arch}] TEST acc {r['Accuracy']:.2f} F1 {r['F1_macro']:.2f} (val F1 {r['val_F1_macro']:.2f}) "
              f"best ep {m.best_epoch}/{len(m.history)} {m.fit_seconds/60:.1f} min", flush=True)
        probs["y_val"] = yva
        with open(os.path.join(out_dir, f"{a.tag or a.variant}.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
        np.savez_compressed(os.path.join(out_dir, f"{a.tag or a.variant}_probs.npz"), **probs)
    print(f"[{a.variant}] done in {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
