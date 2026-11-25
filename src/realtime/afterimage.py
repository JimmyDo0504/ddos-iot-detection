"""
afterimage.py
Re-implementation of Kitsune's "AfterImage" incremental statistics, i.e. the
exact feature extractor used to build the N-BaIoT dataset (115 features).

References
  * Mirsky et al., "Kitsune: An Ensemble of Autoencoders for Online Network
    Intrusion Detection", NDSS 2018  (AfterImage / netStat).
  * Meidan et al., "N-BaIoT: Network-based Detection of IoT Botnet Attacks
    Using Deep Autoencoders", IEEE Pervasive Computing 2018.

Every packet updates damped incremental statistics over 5 time windows
(decay lambda = 5, 3, 1, 0.1, 0.01). A statistic decays as 2^(-lambda * dt),
so L5 summarizes roughly the last ~100 ms and L0.01 the last ~1 minute.

Feature groups (per lambda), named exactly like the N-BaIoT CSV columns:
  MI_dir_L{l}_{weight,mean,variance}   size stats of the source MAC+IP
  H_L{l}_{weight,mean,variance}        size stats of the source IP
  HH_L{l}_{weight,mean,std,magnitude,radius,covariance,pcc}
                                       2-D stats of channel srcIP <-> dstIP
  HH_jit_L{l}_{weight,mean,variance}   jitter (inter-arrival time) of srcIP->dstIP
  HpHp_L{l}_{weight,mean,std,magnitude,radius,covariance,pcc}
                                       2-D stats of socket srcIP:port <-> dstIP:port
The packet "size" is the full frame length in bytes; timestamps are seconds.
"""

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

LAMBDAS = [5, 3, 1, 0.1, 0.01]
LAMBDA_TAGS = ["L5", "L3", "L1", "L0.1", "L0.01"]
STATS_1D = ["weight", "mean", "variance"]
STATS_2D = ["weight", "mean", "std", "magnitude", "radius", "covariance", "pcc"]


def nbaiot_feature_names() -> List[str]:
    """The 115 N-BaIoT feature names in canonical CSV order."""
    names = []
    for prefix, stats in (("MI_dir", STATS_1D), ("H", STATS_1D), ("HH", STATS_2D),
                          ("HH_jit", STATS_1D), ("HpHp", STATS_2D)):
        for tag in LAMBDA_TAGS:
            for s in stats:
                names.append(f"{prefix}_{tag}_{s}")
    return names


class IncStat:
    """Damped incremental 1-D statistic (Kitsune incStat)."""
    __slots__ = ("ID", "lam", "CF1", "CF2", "w", "last_t", "is_diff", "covs")

    def __init__(self, ID: str, lam: float, t0: float, is_diff: bool = False):
        self.ID, self.lam = ID, lam
        self.CF1 = 0.0
        self.CF2 = 0.0
        self.w = 1e-20
        self.last_t = t0
        self.is_diff = is_diff
        self.covs: set = set()   # IncStatCov links (set: O(1) removal when pruning)

    def decay(self, t: float) -> float:
        dt = t - self.last_t
        if dt > 0:
            f = 2.0 ** (-self.lam * dt)
            self.CF1 *= f
            self.CF2 *= f
            self.w *= f
            self.last_t = t
            return f
        return 1.0

    def insert(self, v: float, t: float, cov: Optional["IncStatCov"] = None):
        if self.is_diff:
            dt = t - self.last_t
            v = dt if dt > 0 else 0.0
        self.decay(t)
        self.CF1 += v
        self.CF2 += v * v
        self.w += 1.0
        # Kitsune updates the covariance with EVERY partner stream here. Under a flood with random
        # source ports the victim socket gains one partner per packet, making each insert O(n) and
        # the whole capture O(n^2) (minutes for a 30 s SYN flood). We update only the covariance of
        # the channel this packet belongs to. weight/mean/std/magnitude/radius are unaffected (they
        # do not depend on the cross term); only *_covariance/*_pcc of OTHER channels lose residual
        # updates - none of those features is among the ones selected for the deployed model.
        if cov is not None:
            cov.update(self.ID, v, t)

    def mean(self) -> float:
        w = self.w if self.w > 1e-20 else 1e-20   # guard against decay underflow
        return self.CF1 / w

    def var(self) -> float:
        w = self.w if self.w > 1e-20 else 1e-20
        m = self.CF1 / w
        return abs(self.CF2 / w - m * m)

    def std(self) -> float:
        return math.sqrt(self.var())


class IncStatCov:
    """Damped incremental covariance between two IncStat streams (Kitsune incStat_cov)."""
    __slots__ = ("s", "last_res", "CF3", "w3", "last_t")

    def __init__(self, s1: IncStat, s2: IncStat, t0: float):
        self.s = [s1, s2]
        self.last_res = [0.0, 0.0]
        self.CF3 = 0.0
        self.w3 = 1e-20
        self.last_t = t0

    def _decay(self, t: float, idx: int):
        dt = t - self.last_t
        if dt > 0:
            f = 2.0 ** (-self.s[idx].lam * dt)
            self.CF3 *= f
            self.w3 *= f
            self.last_t = t
            self.last_res[idx] *= f

    def update(self, ID: str, v: float, t: float):
        i = 0 if ID == self.s[0].ID else 1
        j = 1 - i
        self.s[j].decay(t)
        self._decay(t, i)
        res = v - self.s[i].mean()
        self.CF3 += res * self.last_res[j]
        self.w3 += 1.0
        self.last_res[i] = res

    def stats_2d(self) -> Tuple[float, float, float, float]:
        a, b = self.s
        ma, mb = a.mean(), b.mean()
        va, vb = a.var(), b.var()
        magnitude = math.sqrt(ma * ma + mb * mb)
        radius = math.sqrt(va + vb)
        cov = self.CF3 / (self.w3 if self.w3 > 1e-20 else 1e-20)
        ss = math.sqrt(va) * math.sqrt(vb)
        pcc = cov / ss if ss != 0 else 0.0
        return magnitude, radius, cov, pcc


class IncStatDB:
    """Dictionary of IncStat streams keyed by (ID, lambda) with optional covariance links."""

    def __init__(self, max_streams: int = 200000):
        self.streams: Dict[Tuple[str, float], IncStat] = {}
        self.covs: Dict[Tuple[str, str, float], IncStatCov] = {}
        self.max_streams = max_streams

    def get(self, ID: str, lam: float, t: float, is_diff: bool = False) -> IncStat:
        key = (ID, lam)
        s = self.streams.get(key)
        if s is None:
            s = IncStat(ID, lam, t, is_diff)
            self.streams[key] = s
        return s

    def stats_1d(self, ID: str, t: float, v: float, lam: float, is_diff: bool = False) -> Tuple[float, float, float]:
        s = self.get(ID, lam, t, is_diff)
        s.insert(v, t)
        return s.w, s.mean(), s.var()

    def stats_1d2d(self, ID1: str, ID2: str, t: float, v: float, lam: float) -> Tuple[float, ...]:
        s1 = self.get(ID1, lam, t)
        s2 = self.get(ID2, lam, t)
        key = (ID1, ID2, lam) if ID1 <= ID2 else (ID2, ID1, lam)
        c = self.covs.get(key)
        if c is None:
            c = IncStatCov(s1, s2, t)
            self.covs[key] = c
            s1.covs.add(c)
            s2.covs.add(c)
        s1.insert(v, t, c)
        mag, rad, cov, pcc = c.stats_2d()
        return (s1.w, s1.mean(), s1.std(), mag, rad, cov, pcc)

    def prune(self, t: float, min_weight: float = 1e-3):
        """Drop streams whose damped weight has (almost) vanished, to bound memory."""
        dead = []
        for k, s in self.streams.items():
            if s.w * 2.0 ** (-s.lam * max(0.0, t - s.last_t)) < min_weight:
                dead.append(k)
        if not dead:
            return 0
        dead_ids = set(dead)
        for k in dead:
            del self.streams[k]
        for ck in [ck for ck, c in self.covs.items()
                   if (c.s[0].ID, c.s[0].lam) in dead_ids or (c.s[1].ID, c.s[1].lam) in dead_ids]:
            c = self.covs.pop(ck)
            for s in c.s:
                s.covs.discard(c)
        return len(dead)


class AfterImageExtractor:
    """
    Per-packet N-BaIoT feature extractor.

    update(...) consumes one packet and returns the 115-dim feature vector
    (float64, canonical N-BaIoT order = nbaiot_feature_names()).
    """

    def __init__(self, lambdas: Optional[List[float]] = None):
        self.lambdas = lambdas or LAMBDAS
        self.db_mi = IncStatDB()
        self.db_h = IncStatDB()      # host (source IP) 1-D stats
        self.db_hh = IncStatDB()     # host <-> host 2-D stats
        self.db_jit = IncStatDB()
        self.db_hp = IncStatDB()
        self.feature_names = nbaiot_feature_names()
        self.n_packets = 0

    def update(self, src_mac: str, dst_mac: str, src_ip: str, dst_ip: str,
               src_proto: str, dst_proto: str, size: float, t: float) -> np.ndarray:
        L = self.lambdas
        mi = [x for lam in L for x in self.db_mi.stats_1d(src_mac + src_ip, t, size, lam)]
        h = [x for lam in L for x in self.db_h.stats_1d(src_ip, t, size, lam)]
        hh = [x for lam in L for x in self.db_hh.stats_1d2d(src_ip, dst_ip, t, size, lam)]
        jit = [x for lam in L for x in self.db_jit.stats_1d(src_ip + "->" + dst_ip, t, 0.0, lam, is_diff=True)]
        if src_proto == "arp":
            hp = [x for lam in L for x in self.db_hp.stats_1d2d(src_mac, dst_mac, t, size, lam)]
        else:
            hp = [x for lam in L for x in self.db_hp.stats_1d2d(src_ip + ":" + src_proto,
                                                                  dst_ip + ":" + dst_proto, t, size, lam)]
        self.n_packets += 1
        if self.n_packets % 5000 == 0:
            for db in (self.db_mi, self.db_h, self.db_hh, self.db_jit, self.db_hp):
                db.prune(t)
        return np.asarray(mi + h + hh + jit + hp, dtype=np.float64)


def packet_fields(pkt) -> Optional[Tuple[str, str, str, str, str, str, int, float]]:
    """
    Extract the fields AfterImage needs from a Scapy packet, mirroring
    Kitsune's FeatureExtractor. Returns None for packets without an Ethernet/IP context.
    """
    from scapy.layers.l2 import Ether, ARP
    from scapy.layers.inet import IP, TCP, UDP, ICMP
    try:
        from scapy.layers.inet6 import IPv6
    except Exception:  # pragma: no cover
        IPv6 = None

    src_mac = pkt[Ether].src if pkt.haslayer(Ether) else ""
    dst_mac = pkt[Ether].dst if pkt.haslayer(Ether) else ""
    src_ip = dst_ip = ""
    if pkt.haslayer(IP):
        src_ip, dst_ip = pkt[IP].src, pkt[IP].dst
    elif IPv6 is not None and pkt.haslayer(IPv6):
        src_ip, dst_ip = pkt[IPv6].src, pkt[IPv6].dst
    src_proto = dst_proto = ""
    if pkt.haslayer(TCP):
        src_proto, dst_proto = str(pkt[TCP].sport), str(pkt[TCP].dport)
    elif pkt.haslayer(UDP):
        src_proto, dst_proto = str(pkt[UDP].sport), str(pkt[UDP].dport)
    if src_proto == "":
        if pkt.haslayer(ARP):
            src_proto = dst_proto = "arp"
            src_ip, dst_ip = pkt[ARP].psrc, pkt[ARP].pdst
        elif pkt.haslayer(ICMP):
            src_proto = dst_proto = "icmp"
        elif src_ip + dst_ip == "":
            src_ip, dst_ip = src_mac, dst_mac
    if src_ip == "" and src_mac == "":
        return None
    return src_mac, dst_mac, src_ip, dst_ip, src_proto, dst_proto, len(pkt), float(pkt.time)
