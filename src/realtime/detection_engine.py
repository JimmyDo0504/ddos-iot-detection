"""
detection_engine.py
Real-time IoT DDoS monitoring engine for the Kali Linux gateway.

Pipeline (identical feature space to training on N-BaIoT):
  packet -> AfterImage (115 N-BaIoT features) -> saved Preprocessor
  (feature selection + log1p + standardisation) -> ONNX Runtime (batched per
  window) -> per-source-IP decision -> alert log / dashboard / optional iptables.

Modes
  live   : capture on an interface (needs root)
  replay : process a .pcap captured in the lab (tcpdump -w lab.pcap) and write
           a JSON report with detections per window -> used to measure detection
           delay and false alarms offline, reproducibly.
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Dict, List, Optional

import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from realtime.afterimage import AfterImageExtractor, packet_fields  # noqa: E402
from realtime.mitigation import AutoMitigationManager  # noqa: E402


def severity_of(label: str) -> str:
    l = label.lower()
    if l == "benign":
        return "INFO"
    if "scan" in l or "recon" in l:
        return "MEDIUM"
    if any(k in l for k in ("syn", "udp", "ack", "ddos", "flood", "plain")):
        return "CRITICAL"
    return "HIGH"


def _softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


class DeployBundle:
    """
    Shared preprocessing (preprocessor.json) + one or more ONNX models.

    * registry mode : deploy_kali/model/registry.json lists every exported model (ML + DL); the active
                      model can be switched at run time (dashboard drop-down) with switch(name).
    * single mode   : a single best_dl*.onnx file (older bundles).
    Each model declares its input: "row" (current packet features) or "window" (last seq_len packets of
    the same host, flattened).
    """

    def __init__(self, model_path: str, prep_path: str, prefer_int8: bool = False):
        import onnxruntime as ort
        self._ort = ort
        with open(prep_path, encoding="utf-8") as f:
            p = json.load(f)
        self.input_features: List[str] = p["input_features"]
        self.features: List[str] = p["features"]
        self.classes: List[str] = p["classes"]
        self.log_mask = np.asarray(p["log_mask"], dtype=bool)
        self.mean = np.asarray(p["mean"], dtype=np.float64)
        self.scale = np.asarray(p["scale"], dtype=np.float64)
        self.dir = os.path.dirname(os.path.abspath(model_path))
        self.prefer_int8 = prefer_int8
        self.sessions: Dict[str, object] = {}
        self.lock = threading.Lock()
        if model_path.endswith("registry.json"):
            with open(model_path, encoding="utf-8") as f:
                self.registry = json.load(f)
            self.models = {m["name"]: m for m in self.registry["models"] if "error" not in m}
            self.seq_len = int(self.registry.get("seq_len", 1))
            default = self.registry.get("default_model")
            self.active = default if default in self.models else next(iter(self.models))
        else:  # single legacy model
            name = os.path.basename(model_path)
            self.registry = {}
            self.models = {name: {"name": name, "file": name, "input": "row"}}
            self.seq_len = 1
            self.active = name
        self._load(self.active)

    # ---------------------------------------------------------------- models
    def _load(self, name: str):
        if name not in self.sessions:
            m = self.models[name]
            fn = m["file"]
            if self.prefer_int8 and isinstance(m.get("int8"), dict) and m["int8"].get("file"):
                fn = m["int8"]["file"]
            sess = self._ort.InferenceSession(os.path.join(self.dir, fn), providers=["CPUExecutionProvider"])
            self.sessions[name] = sess
        return self.sessions[name]

    def switch(self, name: str) -> Dict:
        if name not in self.models:
            raise KeyError(f"unknown model {name}")
        self._load(name)
        with self.lock:
            self.active = name
        return self.model_info(name)

    def model_info(self, name: Optional[str] = None) -> Dict:
        m = dict(self.models[name or self.active])
        return {k: v for k, v in m.items() if not isinstance(v, dict)}

    def list_models(self) -> List[Dict]:
        return [self.model_info(n) for n in self.models]

    @property
    def model_name(self) -> str:
        return self.active

    @property
    def input_kind(self) -> str:
        return self.models[self.active].get("input", "row")

    def bind(self, extractor_feature_names: List[str]):
        missing = [f for f in self.features if f not in extractor_feature_names]
        if missing:
            raise ValueError(f"Extractor does not produce features required by the model: {missing[:5]}")
        self.idx = np.array([extractor_feature_names.index(f) for f in self.features])

    def transform(self, raw: np.ndarray) -> np.ndarray:
        F = raw[:, self.idx].astype(np.float64)
        if self.log_mask.any():
            F[:, self.log_mask] = np.log1p(np.clip(F[:, self.log_mask], 0, None))
        return ((F - self.mean) / self.scale).astype(np.float32)

    def predict_inputs(self, X: np.ndarray, chunk: int = 512) -> np.ndarray:
        """X already matches the active model's input (rows or flat windows). Returns (N, C) probs."""
        with self.lock:
            name = self.active
        sess = self._load(name)
        iname = sess.get_inputs()[0].name
        out = []
        for i in range(0, len(X), chunk):          # chunking bounds memory (KNN / RF on big windows)
            res = sess.run(None, {iname: X[i:i + chunk].astype(np.float32)})
            mats = [o for o in res if isinstance(o, np.ndarray) and o.ndim == 2 and o.dtype.kind == "f"]
            P = mats[-1].astype(np.float64)
            if P.min() < -1e-6 or np.abs(P.sum(1) - 1).max() > 1e-3:   # SVM decision scores
                P = _softmax(P)
            out.append(P)
        return np.vstack(out)

    def predict_proba(self, raw: np.ndarray) -> np.ndarray:
        """Row-input convenience path (raw 115-dim AfterImage vectors)."""
        return self.predict_inputs(self.transform(raw))


class IoTRealtimeMonitor:
    def __init__(self, model_path: str, prep_path: str, interface: Optional[str] = None,
                 log_file: str = "alerts.log", window_sec: float = 1.0, enable_mitigation: bool = False,
                 min_packets: int = 20, attack_fraction: float = 0.6, max_packets_per_window: int = 20000,
                 local_ips: Optional[List[str]] = None, prefer_int8: bool = False):
        self.bundle = DeployBundle(model_path, prep_path, prefer_int8=prefer_int8)
        self.extractor = AfterImageExtractor()
        self.bundle.bind(self.extractor.feature_names)
        self.interface = interface
        self.log_file = log_file
        self.window_sec = window_sec
        self.min_packets = min_packets
        self.attack_fraction = attack_fraction
        self.max_packets_per_window = max_packets_per_window
        # IPs of the monitoring host itself. Its packets still feed the feature extractor (they are
        # part of the channel statistics), but it is never reported or blocked: under a SYN flood
        # the victim's RST replies look like attack traffic, and blocking ourselves would cut the lab.
        self.local_ips = set(local_ips or []) | {"127.0.0.1", "::1"}
        self.mitigation = AutoMitigationManager(enable_iptables=enable_mitigation)
        self.benign_idx = self.bundle.classes.index("Benign") if "Benign" in self.bundle.classes else 0
        self.lock = threading.Lock()
        self.buf_feats: List[np.ndarray] = []
        self.buf_src: List[str] = []
        self.buf_bytes = 0
        self.buf_seen = 0
        self.window_reports: List[Dict] = []
        # per-host history of transformed packet vectors -> windows for sequence models (GRU, ...)
        self.hist: Dict[str, deque] = {}
        # IoT devices discovered from their telemetry (UDP/1883 towards this gateway): ip -> last seen
        self.iot_devices: Dict[str, float] = {}
        self.stats = {
            "model": self.bundle.model_name, "classes": self.bundle.classes,
            "total_windows": 0, "total_packets": 0, "current_pkt_rate": 0.0, "current_byte_rate": 0.0,
            "active_threats_count": 0,
            "attack_breakdown": {c: 0 for c in self.bundle.classes if c != "Benign"},
            "recent_alerts": [], "blocked_ips": [], "status": "RUNNING",
        }
        self.is_running = False

    # ------------------------------------------------------------ per packet
    def on_packet(self, pkt):
        fields = packet_fields(pkt)
        if fields is None:
            return
        with self.lock:
            self.buf_seen += 1
            self.buf_bytes += fields[6]
            if len(self.buf_feats) >= self.max_packets_per_window:
                return  # still counted; extractor skipped to protect the gateway CPU
            vec = self.extractor.update(*fields)
            self.buf_feats.append(vec)
            # group by the REMOTE host of the packet (both directions), like the N-BaIoT captures that
            # were recorded at the device port: a host's window holds its packets and the replies to it
            src, dst = fields[2], fields[3]
            self.buf_src.append(dst if src in self.local_ips and dst not in self.local_ips else src)
            if fields[5] == "1883" and dst in self.local_ips:
                self.iot_devices[src] = float(fields[7])

    # ------------------------------------------------------------ per window
    def process_window(self, window_end: float, duration: float) -> Optional[Dict]:
        with self.lock:
            feats, srcs = self.buf_feats, self.buf_src
            seen, nbytes = self.buf_seen, self.buf_bytes
            self.buf_feats, self.buf_src, self.buf_seen, self.buf_bytes = [], [], 0, 0
        if not feats:
            return None
        raw = np.vstack(feats)
        t0 = time.perf_counter()
        F = self.bundle.transform(raw)
        T = self.bundle.seq_len
        if T > 1:
            if len(self.hist) > 20000:        # bound memory under spoofed-source floods
                self.hist.clear()
            want_win = self.bundle.input_kind == "window"
            W = np.empty((len(F), T * F.shape[1]), dtype=np.float32) if want_win else None
            for k, h in enumerate(srcs):
                dq = self.hist.get(h)
                if dq is None:
                    dq = self.hist[h] = deque(maxlen=T)
                dq.append(F[k])
                if want_win:
                    w = list(dq)
                    if len(w) < T:
                        w = [w[0]] * (T - len(w)) + w
                    W[k] = np.concatenate(w)
            X = W if want_win else F
        else:
            X = F
        probs = self.bundle.predict_inputs(X)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        pred = probs.argmax(1)
        self.stats["model"] = self.bundle.model_name
        self.stats["total_windows"] += 1
        self.stats["total_packets"] += seen
        self.stats["current_pkt_rate"] = round(seen / max(duration, 1e-3), 1)
        self.stats["current_byte_rate"] = round(nbytes / max(duration, 1e-3), 1)

        by_src: Dict[str, List[int]] = defaultdict(list)
        for i, s in enumerate(srcs):
            by_src[s].append(i)
        decisions = []
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(window_end))
        for src, idx in by_src.items():
            if src in self.local_ips:
                continue
            p = pred[idx]
            atk = p[p != self.benign_idx]
            frac = len(atk) / len(p)
            if len(p) >= self.min_packets and frac >= self.attack_fraction:
                cls = int(Counter(atk.tolist()).most_common(1)[0][0])
                label = self.bundle.classes[cls]
                conf = float(probs[idx, cls].mean())
                alert = {"timestamp": ts, "severity": severity_of(label), "attack_type": label,
                         "confidence": round(conf * 100, 1), "attack_fraction": round(frac * 100, 1),
                         "dominant_src": src, "packets": len(p),
                         "pkt_rate": round(len(p) / max(duration, 1e-3), 1),
                         "inference_latency_ms": round(latency_ms, 3), "model": self.bundle.model_name}
                decisions.append(alert)
                self.stats["active_threats_count"] += 1
                self.stats["attack_breakdown"][label] = self.stats["attack_breakdown"].get(label, 0) + 1
                self.stats["recent_alerts"].insert(0, alert)
                del self.stats["recent_alerts"][50:]
                # libpcap sees packets before netfilter, so a source that is already DROPped by
                # iptables keeps appearing in the capture while it keeps sending.
                note = " | already blocked (iptables DROP)" if src in self.mitigation.blocked_ips else ""
                print(f"[{ts}] [{alert['severity']}] {label} from {src} | {len(p)} pkts | "
                      f"{frac*100:.0f}% flagged | conf {conf*100:.1f}% | infer {latency_ms:.2f} ms{note}")
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(alert) + "\n")
                self.mitigation.block_ip(src, label, conf)
        self.mitigation.check_expired_blocks()
        self.stats["blocked_ips"] = self.mitigation.get_active_blocks()
        report = {"window_end": window_end, "packets": seen, "analysed": len(feats),
                  "inference_ms": round(latency_ms, 3),
                  "class_counts": {self.bundle.classes[k]: int(v) for k, v in Counter(pred.tolist()).items()},
                  "alerts": decisions}
        self.window_reports.append(report)
        if not decisions and self.stats["total_windows"] % 5 == 0:
            print(f"[{ts}] [NORMAL] {seen} pkts in window, no alert")
        return report

    # ------------------------------------------------------------------ live
    def start(self):
        from realtime.packet_sniffer import RealTimePacketSniffer
        self.is_running = True
        self.local_ips |= interface_ips(self.interface)
        print(f"[ENGINE] Own IPs (never alerted/blocked): {', '.join(sorted(self.local_ips))}")
        self.sniffer = RealTimePacketSniffer(self.on_packet, interface=self.interface)
        self.sniffer.start()

        def _loop():
            print(f"[ENGINE] Monitoring with {self.bundle.model_name} | window {self.window_sec}s | "
                  f"{len(self.bundle.features)} features | classes: {', '.join(self.bundle.classes)}")
            last = time.time()
            while self.is_running:
                time.sleep(self.window_sec)
                now = time.time()
                self.process_window(now, now - last)
                last = now

        self.worker = threading.Thread(target=_loop, daemon=True)
        self.worker.start()

    def stop(self):
        self.is_running = False
        self.sniffer.stop()
        self.mitigation.unblock_all()

    # ---------------------------------------------------------------- replay
    def replay(self, pcap_path: str, report_path: Optional[str] = None) -> Dict:
        """Process a capture file using packet timestamps to delimit windows."""
        from scapy.all import PcapReader  # scapy.all registers L2/L3 layers for decoding
        win_end, first_t = None, None
        t_wall = time.perf_counter()
        with PcapReader(pcap_path) as reader:
            for pkt in reader:
                t = float(pkt.time)
                if first_t is None:
                    first_t, win_end = t, t + self.window_sec
                while t >= win_end:
                    self.process_window(win_end, self.window_sec)
                    win_end += self.window_sec
                self.on_packet(pkt)
        if win_end is not None:
            self.process_window(win_end, self.window_sec)
        summary = {
            "pcap": os.path.basename(pcap_path), "model": self.bundle.model_name,
            "windows": len(self.window_reports), "packets": self.stats["total_packets"],
            "alerts": sum(len(w["alerts"]) for w in self.window_reports),
            "first_alert_offset_s": next((round(w["window_end"] - first_t, 2) for w in self.window_reports
                                          if w["alerts"]), None),
            "processing_seconds": round(time.perf_counter() - t_wall, 2),
            "mean_inference_ms_per_window": round(float(np.mean([w["inference_ms"] for w in self.window_reports]))
                                                  if self.window_reports else 0.0, 3),
            "per_window": self.window_reports,
        }
        if report_path:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
        return summary


def interface_ips(interface: Optional[str]) -> set:
    """IPv4 addresses of the capture interface (or of all interfaces when none is given)."""
    ips = set()
    try:
        from scapy.all import get_if_addr, get_if_list
        for ifc in ([interface] if interface else get_if_list()):
            try:
                ip = get_if_addr(ifc)
                if ip and ip != "0.0.0.0":
                    ips.add(ip)
            except Exception:
                pass
    except Exception:
        pass
    return ips


def default_bundle_paths(base_dir: str):
    for d in (os.path.join(base_dir, "deploy_kali", "model"), os.path.join(base_dir, "results", "n_baiot", "models"),
              os.getcwd()):
        p = os.path.join(d, "preprocessor.json")
        if os.path.exists(os.path.join(d, "registry.json")) and os.path.exists(p):
            return os.path.join(d, "registry.json"), p
        m = os.path.join(d, "best_dl_int8.onnx")
        if not os.path.exists(m):
            m = os.path.join(d, "best_dl.onnx")
        if os.path.exists(m) and os.path.exists(p):
            return m, p
    raise FileNotFoundError("No deploy bundle (best_dl*.onnx + preprocessor.json) found.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="IoT DDoS detection engine (N-BaIoT / AfterImage features)")
    ap.add_argument("--interface", "-i", default=None)
    ap.add_argument("--pcap", default=None, help="replay a capture file instead of live capture")
    ap.add_argument("--report", default="replay_report.json")
    ap.add_argument("--model", default=None)
    ap.add_argument("--prep", default=None)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--block", action="store_true", help="enable iptables blocking (live mode, root)")
    ap.add_argument("--local-ip", action="append", default=[],
                    help="IP of the monitoring host (never alerted/blocked); repeatable. "
                         "Live mode adds the interface IP automatically; give it explicitly for --pcap replay")
    ap.add_argument("--attack-fraction", type=float, default=0.6,
                    help="share of a source's packets in a window that must be classified as attack")
    ap.add_argument("--min-packets", type=int, default=20, help="min packets from a source in a window")
    ap.add_argument("--model-name", default=None, help="registry model to use (default: registry default)")
    ap.add_argument("--int8", action="store_true", help="use the INT8 variant of DL models when available")
    a = ap.parse_args()
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    mp, pp = (a.model, a.prep) if a.model and a.prep else default_bundle_paths(base)
    mon = IoTRealtimeMonitor(mp, pp, interface=a.interface, window_sec=a.window, enable_mitigation=a.block,
                             local_ips=a.local_ip, attack_fraction=a.attack_fraction,
                             min_packets=a.min_packets, prefer_int8=a.int8)
    if a.model_name:
        mon.bundle.switch(a.model_name)
    if a.pcap:
        s = mon.replay(a.pcap, a.report)
        print(json.dumps({k: v for k, v in s.items() if k != "per_window"}, indent=2))
    else:
        mon.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            mon.stop()
