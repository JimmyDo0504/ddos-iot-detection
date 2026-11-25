"""
n_baiot.py
Loader for the N-BaIoT dataset (Meidan et al., IEEE Pervasive Computing 2018).

Source : https://archive.ics.uci.edu/dataset/442/detection+of+iot+botnet+attacks+n+baiot
Testbed: 9 commercial IoT devices (doorbells, thermostat, baby monitor,
         security cameras, webcam) infected with real Mirai and BASHLITE
         (Gafgyt) botnets.
Features: 115 per-packet statistics computed by Kitsune's "AfterImage"
         incremental extractor over 5 damped time windows
         (lambda = 5, 3, 1, 0.1, 0.01  ->  L5, L3, L1, L0.1, L0.01):
           MI_dir_*  : packet size stats per source MAC-IP      (weight, mean, variance)
           H_*       : packet size stats per source IP          (weight, mean, variance)
           HH_*      : channel src IP -> dst IP (2-D stats)     (weight, mean, std, magnitude, radius, covariance, pcc)
           HH_jit_*  : channel jitter (inter-arrival time)      (weight, mean, variance)
           HpHp_*    : socket srcIP:port -> dstIP:port (2-D)    (weight, mean, std, magnitude, radius, covariance, pcc)
         Because the extractor is fully specified, the SAME features can be
         reproduced live on the Kali gateway (see realtime/afterimage.py).

Supported layouts (auto-detected):
  1) UCI archive : <root>/<Device>/benign_traffic.csv
                   <root>/<Device>/gafgyt_attacks/{combo,junk,scan,tcp,udp}.csv  (or gafgyt_attacks.rar)
                   <root>/<Device>/mirai_attacks/{ack,scan,syn,udp,udpplain}.csv (or mirai_attacks.rar)
  2) Kaggle CSVs : <root>/<n>.benign.csv, <n>.gafgyt.combo.csv, <n>.mirai.syn.csv, ...
"""

import os
import re
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

from iot_datasets.common import find_files, PerClassSampler, clean_numeric, FileCache

DEVICES = [
    "Danmini_Doorbell", "Ecobee_Thermostat", "Ennio_Doorbell",
    "Philips_B120N10_Baby_Monitor", "Provision_PT_737E_Security_Camera",
    "Provision_PT_838_Security_Camera", "Samsung_SNH_1011_N_Webcam",
    "SimpleHome_XCS7_1002_WHT_Security_Camera", "SimpleHome_XCS7_1003_WHT_Security_Camera",
]
BLOCK_LEN = 100   # consecutive packets kept per sampled block
GAFGYT = ["combo", "junk", "scan", "tcp", "udp"]
MIRAI = ["ack", "scan", "syn", "udp", "udpplain"]
CLASS_ORDER = ["Benign"] + [f"Gafgyt_{a}" for a in GAFGYT] + [f"Mirai_{a}" for a in MIRAI]


def _label_from_path(path: str) -> Optional[str]:
    """Infer the N-BaIoT class label from a CSV path (UCI or Kaggle naming)."""
    p = path.replace("\\", "/").lower()
    name = os.path.basename(p)
    # Kaggle style: "3.mirai.syn.csv", "1.benign.csv", "2.gafgyt.combo.csv"
    m = re.match(r"^\d+\.(benign|mirai|gafgyt)(?:\.([a-z]+))?\.csv$", name)
    if m:
        fam, atk = m.group(1), m.group(2)
        if fam == "benign":
            return "Benign"
        return f"{fam.capitalize()}_{atk}"
    # UCI style
    if name.startswith("benign"):
        return "Benign"
    stem = name[:-4] if name.endswith(".csv") else name
    if "mirai" in p and stem in MIRAI:
        return f"Mirai_{stem}"
    if ("gafgyt" in p or "bashlite" in p) and stem in GAFGYT:
        return f"Gafgyt_{stem}"
    return None


def _device_from_path(path: str) -> str:
    p = path.replace("\\", "/")
    for d in DEVICES:
        if d.lower() in p.lower():
            return d
    m = re.match(r"^(\d+)\.", os.path.basename(p))
    if m and 1 <= int(m.group(1)) <= len(DEVICES):
        return DEVICES[int(m.group(1)) - 1]
    return "unknown"


def _list_sources(root: str) -> List[Tuple[str, Callable[[], pd.DataFrame], str, str]]:
    """
    List every labelled N-BaIoT table as (virtual_path, reader, cache_file, cache_extra).
    Readers are lazy so cached files are never re-read. RAR archives are only
    opened when their content has not been extracted next to them.
    """
    out = []
    csvs = find_files(root, ["*.csv", "*.CSV"])
    covered = {os.path.normcase(os.path.dirname(os.path.abspath(c))) for c in csvs}
    for c in csvs:
        if _label_from_path(c) is not None:
            out.append((c, (lambda p=c: pd.read_csv(p)), c, ""))
    for r in find_files(root, ["*.rar", "*.RAR"]):
        if os.path.normcase(os.path.abspath(r[:-4])) in covered:
            continue
        try:
            import rarfile
            with rarfile.RarFile(r) as rf:
                names = [i.filename for i in rf.infolist() if i.filename.lower().endswith(".csv")]
        except Exception as e:
            raise RuntimeError(f"'{r}' is a RAR archive that is not extracted and cannot be opened ({e}). "
                               f"Extract it with WinRAR / 7-Zip ('Extract to {os.path.basename(r)[:-4]}\\').") from e
        for n in names:
            vpath = os.path.join(r[:-4], os.path.basename(n))

            def _reader(rp=r, name=n):
                import rarfile
                with rarfile.RarFile(rp) as rf, rf.open(name) as fh:
                    return pd.read_csv(fh)
            out.append((vpath, _reader, r, n))
    return out


def load_n_baiot(
    root: str,
    cap_per_class: int = 40000,
    label_mode: str = "11class",
    devices: Optional[List[str]] = None,
    seed: int = 42,
    verbose: bool = True,
    cache_dir: Optional[str] = None,
    time_budget: Optional[float] = None,
) -> Tuple[pd.DataFrame, np.ndarray, List[str], Dict]:
    """
    Load and sample N-BaIoT.

    label_mode:
      "11class" : Benign + 5 Gafgyt + 5 Mirai attacks (default)
      "3class"  : Benign / Gafgyt / Mirai
      "binary"  : Benign vs Attack
    Returns (X_df, y_labels, feature_names, info_dict).
    """
    sampler = PerClassSampler(cap=cap_per_class, seed=seed)
    cache = FileCache(cache_dir, cap_per_class, time_budget, seed)
    feature_cols: Optional[List[str]] = None
    files_used, total_rows, per_device = [], 0, {}
    sources = [s_ for s_ in _list_sources(root)
               if not devices or _device_from_path(s_[0]) in devices]
    # Time-ordered sampling: every file is a chronological packet capture. Instead of random rows we
    # keep CONTIGUOUS blocks of BLOCK_LEN packets spread evenly over the file, so that
    #   * sequence models (GRU/LSTM/BiGRU-Attention) see real consecutive packets, and
    #   * the train/val/test split can be done by time (position in the file) -> no near-duplicate
    #     neighbouring packets on both sides of the split.
    # Each row keeps _src (file id), _blk (block id) and _pos (relative position 0..1 in its file).
    files_per_label: Dict[str, int] = {}
    for path, _, _, _ in sources:
        lb = _label_from_path(path)
        files_per_label[lb] = files_per_label.get(lb, 0) + 1
    ordered_parts: List[pd.DataFrame] = []
    blk_offset = 0
    for k, (path, reader, cfile, extra) in enumerate(sources):
        lbl = _label_from_path(path)
        dev = _device_from_path(path)
        if label_mode == "3class":
            lab = lbl.split("_")[0]
        elif label_mode == "binary":
            lab = "Benign" if lbl == "Benign" else "Attack"
        else:
            lab = lbl
        ckey = extra + "|" + label_mode + f"|v2blk{BLOCK_LEN}"
        df, meta = cache.get(cfile, ckey)
        cached = df is not None
        if cached:
            n_raw = meta["rows"]
        else:
            df = reader()
            df.columns = [c.strip() for c in df.columns]
            n_raw = len(df)
            quota = int(np.ceil(cap_per_class / files_per_label[lbl]))
            df["_pos"] = (np.arange(n_raw) / max(n_raw - 1, 1)).astype(np.float32)
            if n_raw <= quota:
                df["_blk"] = np.arange(n_raw) // BLOCK_LEN
            else:
                nb = int(np.ceil(quota / BLOCK_LEN))
                starts = np.linspace(0, n_raw - BLOCK_LEN, nb).astype(int)
                idx = np.concatenate([np.arange(s, s + BLOCK_LEN) for s in starts])
                blk = np.repeat(np.arange(nb), BLOCK_LEN)
                idx, first = np.unique(idx, return_index=True)   # overlapping blocks (tiny files)
                df = df.iloc[idx].copy()
                df["_blk"] = blk[first]
            df["label"] = lab
            cache.put(cfile, df.reset_index(drop=True), {"rows": n_raw}, ckey)
        if feature_cols is None:
            feature_cols = [c for c in df.columns if c != "label" and not c.startswith("_")]
        df = df[[c for c in feature_cols if c in df.columns] + ["_pos", "_blk", "label"]].copy()
        df["_src"] = k
        df["_blk"] = df["_blk"].astype(np.int64) + blk_offset
        blk_offset = int(df["_blk"].max()) + 1
        ordered_parts.append(df)
        sampler.seen[lab] = sampler.seen.get(lab, 0) + n_raw
        total_rows += n_raw
        per_device[dev] = per_device.get(dev, 0) + n_raw
        files_used.append(os.path.relpath(path, root))
        if verbose:
            print(f"    [N-BaIoT] {k+1:>3}/{len(sources)} {dev:<42} {lbl:<16} {n_raw:>8,} rows"
                  f"{' (cached)' if cached else ''}")
        cache.check_budget(k + 1, len(sources))

    if feature_cols is None:
        raise FileNotFoundError(
            f"No N-BaIoT CSV found under '{root}'. Expected <Device>/benign_traffic.csv, "
            f"<Device>/mirai_attacks/*.csv, <Device>/gafgyt_attacks/*.csv (or Kaggle n.*.csv).")

    data = pd.concat(ordered_parts, ignore_index=True)
    data, clean_report = clean_numeric(data, feature_cols)   # _pos is unique -> no rows merged here
    y = data["label"].to_numpy()
    X = data[feature_cols + ["_src", "_blk", "_pos"]]
    info = {
        "dataset": "N-BaIoT",
        "source": "https://archive.ics.uci.edu/dataset/442/detection+of+iot+botnet+attacks+n+baiot",
        "files_used": files_used,
        "raw_rows_read": int(total_rows),
        "raw_rows_per_device": per_device,
        "raw_class_counts": {k: int(v) for k, v in sorted(sampler.seen.items())},
        "cap_per_class": cap_per_class,
        "label_mode": label_mode,
        "cleaning": clean_report,
    }
    return X, y, feature_cols, info
