"""
check_env.py
Checks that the Python environment can run the project.

    python check_env.py          # training machine (Windows / Linux / Colab)
    python3 check_env.py --kali  # Kali gateway (real-time monitor only)
"""
import argparse
import importlib
import sys

TRAIN = ["numpy", "pandas", "pyarrow", "sklearn", "xgboost", "imblearn", "torch", "onnx", "onnxruntime",
         "onnxscript", "matplotlib"]
KALI = ["numpy", "onnxruntime", "scapy", "flask"]
OPTIONAL = {"rarfile": "only needed if N-BaIoT .rar files are not extracted"}


def check(mods):
    bad = 0
    for m in mods:
        try:
            mod = importlib.import_module(m)
            ver = getattr(mod, "__version__", "")
            if m == "torch":  # make sure the native library really loads (e.g. not blocked by AppLocker)
                mod.zeros(2, 2) @ mod.zeros(2, 2)
            print(f"  [OK]    {m:<12} {ver}")
        except Exception as e:
            bad += 1
            print(f"  [FAIL]  {m:<12} {type(e).__name__}: {str(e)[:90]}")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kali", action="store_true")
    a = ap.parse_args()
    print(f"Python {sys.version.split()[0]}")
    if sys.version_info < (3, 9):
        print("  [FAIL]  Python >= 3.9 required")
    mods = KALI if a.kali else TRAIN
    print("Required packages:")
    bad = check(mods)
    print("Optional:")
    for m, why in OPTIONAL.items():
        try:
            importlib.import_module(m)
            print(f"  [OK]    {m}")
        except Exception:
            print(f"  [--]    {m:<12} not installed ({why})")
    if bad:
        req = "requirements-kali.txt" if a.kali else "requirements.txt"
        print(f"\n{bad} package(s) missing -> pip install -r {req}")
        sys.exit(1)
    print("\nEnvironment OK")
