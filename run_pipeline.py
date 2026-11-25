"""
run_pipeline.py
Master orchestrator for "Deep Learning for DDoS detection in IoT".

Datasets (real, public):
  * CIC-IoT-2023  -> put CSV parts in   datasets/CIC-IoT-2023/
  * N-BaIoT       -> put the UCI/Kaggle files in datasets/N-BaIoT/

Stages
  --check     : verify the downloaded datasets (files, labels, columns) - runs automatically with --all
  --prepare   : read raw files, capped per-class sampling + cleaning -> data/processed/*.parquet
  --train     : EDA, preprocessing, 5 ML baselines vs 4 DL models, evaluation, ONNX export
                (results/<dataset>/..., deploy bundle -> deploy_kali/model/)
  --all       : prepare + train
  --dashboard : start the real-time monitor + web dashboard (Kali, root)
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(cmd):
    print("\n$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description="IoT DDoS detection pipeline (CIC-IoT-2023 + N-BaIoT)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--dashboard", action="store_true")
    ap.add_argument("--dataset", default="all", choices=["all", "cic_iot2023", "n_baiot"])
    ap.add_argument("--cap-per-class", type=int, default=40000)
    ap.add_argument("--interface", default=None)
    a = ap.parse_args()
    if not (a.all or a.check or a.prepare or a.train or a.dashboard):
        ap.print_help()
        return
    py = sys.executable
    if a.all or a.check:
        try:
            run([py, os.path.join(ROOT, "src", "check_data.py"), "--dataset", a.dataset])
        except subprocess.CalledProcessError:
            print("\n[STOP] Dataset check failed - see the ERROR lines above (HUONG_DAN_LAM_BAI.md, muc 2).")
            sys.exit(1)
    if a.all or a.prepare:
        run([py, os.path.join(ROOT, "src", "prepare_data.py"), "--dataset", a.dataset,
             "--cap-per-class", str(a.cap_per_class)])
    if a.all or a.train:
        run([py, os.path.join(ROOT, "src", "run_experiments.py"), "--dataset", a.dataset])
    if a.dashboard:
        cmd = [py, os.path.join(ROOT, "src", "realtime", "web_dashboard.py")]
        if a.interface:
            cmd += ["--interface", a.interface]
        run(cmd)


if __name__ == "__main__":
    main()
