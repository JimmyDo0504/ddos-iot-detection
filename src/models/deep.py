"""
deep.py
Deep-learning models (PyTorch) for tabular IoT flow / packet features.

  * MLP          : dense network with BatchNorm + Dropout
  * 1D-CNN       : treats the feature vector as a 1-D signal
  * GRU          : treats the feature vector as a sequence (one feature per step)
  * Edge-CNN     : lightweight depthwise-separable 1D-CNN (MobileNet-style) for edge devices

Training: AdamW, class-weighted cross-entropy, ReduceLROnPlateau and
early stopping on VALIDATION macro-F1 (best weights restored). The per-epoch
history (train loss, val loss, val accuracy, val macro-F1) is recorded for plots.
"""

import copy
import io
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import f1_score

torch.set_num_threads(max(1, torch.get_num_threads()))


# ----------------------------------------------------------------- networks
class MLP(nn.Module):
    def __init__(self, n_in: int, n_out: int, hidden=(256, 128, 64), dropout=0.2):
        super().__init__()
        layers, d = [], n_in
        for h in hidden:
            layers += [nn.Linear(d, h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(dropout)]
            d = h
        layers.append(nn.Linear(d, n_out))
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)


class CNN1D(nn.Module):
    def __init__(self, n_in: int, n_out: int, ch=(32, 64, 64), dropout=0.2):
        super().__init__()
        c1, c2, c3 = ch
        self.features = nn.Sequential(
            nn.Conv1d(1, c1, 3, padding=1), nn.BatchNorm1d(c1), nn.ReLU(),
            nn.Conv1d(c1, c2, 3, padding=1), nn.BatchNorm1d(c2), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(c2, c3, 3, padding=1), nn.BatchNorm1d(c3), nn.ReLU(),
            nn.AdaptiveAvgPool1d(4),
        )
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(dropout), nn.Linear(c3 * 4, 128), nn.ReLU(),
                                  nn.Linear(128, n_out))

    def forward(self, x):
        return self.head(self.features(x.unsqueeze(1)))


class SeqNet(nn.Module):
    """
    Recurrent classifier over a SEQUENCE input. The flat input (B, seq_len*d) is reshaped to
    (B, seq_len, d):
      * N-BaIoT : seq_len = 10 consecutive packets, d = 30 features per packet (real temporal order)
      * CIC-IoT-2023 (rows are not time-ordered): seq_len = 30, d = 1 (feature-as-sequence)
    cell      : "gru" | "lstm"
    bidir     : bidirectional recurrent layer
    attention : additive (Bahdanau-style) attention pooling over all time steps instead of using only
                the last hidden state -> "BiGRU-Attention" when cell="gru", bidir=True, attention=True.
    """

    def __init__(self, n_in: int, n_out: int, seq_len=10, d=30, cell="gru", hidden=64, bidir=False,
                 attention=False, emb=32, dropout=0.2):
        super().__init__()
        assert seq_len * d == n_in, f"seq_len*d ({seq_len}*{d}) must equal n_in ({n_in})"
        self.seq_len, self.d, self.attention = seq_len, d, attention
        self.embed = nn.Sequential(nn.Linear(d, emb), nn.ReLU())
        rnn = nn.GRU if cell == "gru" else nn.LSTM
        self.rnn = rnn(emb, hidden, batch_first=True, bidirectional=bidir)
        out = hidden * (2 if bidir else 1)
        if attention:
            self.att_w = nn.Linear(out, out)
            self.att_v = nn.Linear(out, 1, bias=False)
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(out, 64), nn.ReLU(), nn.Linear(64, n_out))

    def forward(self, x):
        h = self.embed(x.reshape(x.shape[0], self.seq_len, self.d))   # (B, T, emb)
        o, _ = self.rnn(h)                                              # (B, T, out)
        if self.attention:
            a = torch.softmax(self.att_v(torch.tanh(self.att_w(o))), dim=1)   # (B, T, 1)
            z = (a * o).sum(dim=1)
        else:
            z = o[:, -1, :]
        return self.head(z)


class GRUNet(SeqNet):
    def __init__(self, n_in, n_out, **kw):
        super().__init__(n_in, n_out, cell="gru", **kw)


class LSTMNet(SeqNet):
    def __init__(self, n_in, n_out, **kw):
        super().__init__(n_in, n_out, cell="lstm", **kw)


class BiGRUNet(SeqNet):
    def __init__(self, n_in, n_out, **kw):
        super().__init__(n_in, n_out, cell="gru", bidir=True, **kw)


class BiGRUAttNet(SeqNet):
    def __init__(self, n_in, n_out, **kw):
        super().__init__(n_in, n_out, cell="gru", bidir=True, attention=True, **kw)


class DSConv(nn.Module):
    """Depthwise-separable 1-D convolution block."""

    def __init__(self, c_in, c_out):
        super().__init__()
        self.dw = nn.Conv1d(c_in, c_in, 3, padding=1, groups=c_in)
        self.pw = nn.Conv1d(c_in, c_out, 1)
        self.bn = nn.BatchNorm1d(c_out)

    def forward(self, x):
        return torch.relu(self.bn(self.pw(self.dw(x))))


class EdgeCNN(nn.Module):
    def __init__(self, n_in: int, n_out: int, width=16):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv1d(1, width, 3, padding=1), nn.BatchNorm1d(width), nn.ReLU())
        self.blocks = nn.Sequential(DSConv(width, 2 * width), DSConv(2 * width, 2 * width))
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(2 * width, n_out)

    def forward(self, x):
        z = self.blocks(self.stem(x.unsqueeze(1)))
        return self.fc(self.pool(z).squeeze(-1))


ARCHS = {"MLP": MLP, "1D-CNN": CNN1D, "Edge-CNN": EdgeCNN,
         "LSTM": LSTMNet, "GRU": GRUNet, "BiGRU": BiGRUNet, "BiGRU-Attention": BiGRUAttNet}
SEQ_ARCHS = {"LSTM", "GRU", "BiGRU", "BiGRU-Attention"}   # take the whole packet window as input
ROW_ARCHS = {"MLP", "1D-CNN", "Edge-CNN"}                 # take only the current packet's features

# small, comparable search space per architecture (seq_len/d are filled in by the caller)
SEARCH = {
    "MLP": [{"hidden": (256, 128, 64), "dropout": 0.2}, {"hidden": (512, 256, 128), "dropout": 0.3}],
    "1D-CNN": [{"ch": (32, 64, 64), "dropout": 0.2}, {"ch": (64, 128, 128), "dropout": 0.3}],
    "Edge-CNN": [{"width": 16}, {"width": 24}],
    "LSTM": [{"hidden": 64, "dropout": 0.2}],
    "GRU": [{"hidden": 64, "dropout": 0.2}],
    "BiGRU": [{"hidden": 64, "dropout": 0.2}],
    "BiGRU-Attention": [{"hidden": 64, "dropout": 0.2}, {"hidden": 96, "dropout": 0.3}],
}


# ------------------------------------------------------------- wrapper
class TorchClassifier:
    def __init__(self, arch: str, n_in: int, n_out: int, params: Dict, lr=1e-3, batch=512,
                 max_epochs=40, patience=6, seed=42):
        self.arch, self.n_in, self.n_out, self.params = arch, n_in, n_out, params
        self.lr, self.batch, self.max_epochs, self.patience, self.seed = lr, batch, max_epochs, patience, seed
        torch.manual_seed(seed)
        np.random.seed(seed)
        self.net = ARCHS[arch](n_in, n_out, **params)
        self.history: List[Dict] = []
        self.best_epoch = 0

    def n_params(self) -> int:
        return int(sum(p.numel() for p in self.net.parameters()))

    def size_bytes(self) -> int:
        buf = io.BytesIO()
        torch.save(self.net.state_dict(), buf)
        return buf.tell()

    def fit(self, X, y, X_val, y_val, verbose=True):
        Xt, yt = torch.from_numpy(X), torch.from_numpy(y)
        Xv, yv = torch.from_numpy(X_val), torch.from_numpy(y_val)
        counts = np.bincount(y, minlength=self.n_out).astype(np.float64)
        w = np.where(counts > 0, (counts.sum() / (self.n_out * np.maximum(counts, 1))) ** 0.5, 0.0)
        crit = nn.CrossEntropyLoss(weight=torch.tensor(w, dtype=torch.float32))
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=1e-4)
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
                opt.zero_grad()
                loss = crit(self.net(Xt[b]), yt[b])
                loss.backward()
                opt.step()
                tot += loss.item()
                nb += 1
            self.net.eval()
            with torch.no_grad():
                logits = self._logits(Xv)
                vloss = float(crit(logits, yv))
                vpred = logits.argmax(1).numpy()
            vacc = float((vpred == y_val).mean())
            vf1 = float(f1_score(y_val, vpred, average="macro"))
            sched.step(vf1)
            self.history.append({"epoch": ep, "train_loss": tot / max(1, nb), "val_loss": vloss,
                                 "val_acc": vacc, "val_f1_macro": vf1, "lr": opt.param_groups[0]["lr"]})
            if verbose:
                print(f"      ep {ep:2d} loss {tot/max(1,nb):.4f} | val loss {vloss:.4f} acc {vacc*100:.2f}% F1 {vf1*100:.2f}%")
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

    def _logits(self, X: torch.Tensor) -> torch.Tensor:
        out = [self.net(X[i:i + 8192]) for i in range(0, len(X), 8192)]
        return torch.cat(out)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        self.net.eval()
        with torch.no_grad():
            return torch.softmax(self._logits(torch.from_numpy(np.asarray(X, dtype=np.float32))), 1).numpy()

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict_proba(X).argmax(1)

    def export_onnx(self, path: str):
        self.net.eval()
        dummy = torch.zeros(1, self.n_in, dtype=torch.float32)
        wrapped = nn.Sequential(self.net, nn.Softmax(dim=1))
        try:
            torch.onnx.export(wrapped, dummy, path, input_names=["input"], output_names=["probs"],
                              dynamic_axes={"input": {0: "batch"}, "probs": {0: "batch"}}, opset_version=17,
                              dynamo=False)
        except Exception as e:
            # The legacy TorchScript exporter cannot export AdaptiveAvgPool1d when the output size is
            # not a factor of the input size (1D-CNN: 15 -> 4). The torch.export-based exporter can.
            print(f"    [info] legacy ONNX export failed ({type(e).__name__}); using torch.export (dynamo)")
            batch = torch.export.Dim("batch", min=1, max=1 << 20)
            torch.onnx.export(wrapped, (torch.zeros(2, self.n_in, dtype=torch.float32),), path,
                              input_names=["input"], output_names=["probs"],
                              dynamic_shapes=({0: batch},), opset_version=18, dynamo=True, verbose=False,
                              external_data=False)  # single self-contained .onnx file for Kali


class InputSlice:
    """
    Feeds a model only the columns it is meant to see. All models receive the same flat window
    matrix (B, seq_len*d); per-packet models use only the LAST packet (the trailing `row_dim`
    columns), sequence models use the whole window. Attribute access is delegated to the model.
    """

    def __init__(self, model, start: int, input_kind: str):
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "input_kind", input_kind)   # "row" or "window"

    def _x(self, X):
        return X[:, self.start:] if self.start else X

    def predict_proba(self, X):
        return self.model.predict_proba(self._x(X))

    def predict(self, X):
        return self.model.predict(self._x(X))

    def fit(self, X, y, *a, **kw):
        if "eval_set" in kw:
            kw["eval_set"] = [(self._x(Xe), ye) for Xe, ye in kw["eval_set"]]
        self.model.fit(self._x(X), y, *a, **kw)
        return self

    def __getattr__(self, k):
        return getattr(object.__getattribute__(self, "model"), k)

    def __setattr__(self, k, v):
        setattr(self.model, k, v)

    def __getstate__(self):
        return {"model": self.model, "start": self.start, "input_kind": self.input_kind}

    def __setstate__(self, st):
        for k, v in st.items():
            object.__setattr__(self, k, v)


def train_deep_models(X_tr, y_tr, X_val, y_val, n_classes: int, seq_len: int, d: int, row_dim: int,
                      seed=42, max_epochs=40, archs: Optional[List[str]] = None,
                      verbose=True) -> Tuple[Dict[str, "InputSlice"], Dict]:
    """
    X_* are flat window matrices (N, seq_len*d). Per-packet archs (MLP/CNN) get the last `row_dim`
    columns; sequence archs (LSTM/GRU/BiGRU/BiGRU-Attention) get the full window reshaped to (seq_len, d).
    """
    best, log = {}, {}
    width = X_tr.shape[1]
    for arch in (archs or list(ARCHS.keys())):
        start = 0 if arch in SEQ_ARCHS else width - row_dim
        kind = "window" if arch in SEQ_ARCHS else "row"
        Xa, Xv = X_tr[:, start:], X_val[:, start:]
        results, top = [], None
        for params in SEARCH[arch]:
            params = dict(params)
            if arch in SEQ_ARCHS:
                params.update({"seq_len": seq_len, "d": d})
            if verbose:
                print(f"    [{arch}] params={params}", flush=True)
            m = TorchClassifier(arch, Xa.shape[1], n_classes, params, max_epochs=max_epochs, seed=seed)
            m.fit(Xa, y_tr, Xv, y_val, verbose=verbose)
            results.append({"params": str(params), "val_f1_macro": round(m.best_val_f1 * 100, 3),
                            "best_epoch": m.best_epoch, "epochs_run": len(m.history),
                            "fit_seconds": round(m.fit_seconds, 1), "n_params": m.n_params()})
            if top is None or m.best_val_f1 > top.best_val_f1:
                top = m
        best[arch] = InputSlice(top, start, kind)
        log[arch] = {"search": results, "selected": str(top.params), "history": top.history,
                     "fit_seconds": round(top.fit_seconds, 1), "input_kind": kind}
        print(f"    [{arch}] selected {top.params} val F1 {top.best_val_f1*100:.2f}% "
              f"(best epoch {top.best_epoch}/{len(top.history)})", flush=True)
    return best, log
