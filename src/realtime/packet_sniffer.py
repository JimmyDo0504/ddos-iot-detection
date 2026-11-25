"""
packet_sniffer.py
Live packet capture (Scapy) or offline replay of a .pcap file, feeding every
packet to a callback. Used by the detection engine on the Kali gateway.
"""

import threading
import time
from typing import Callable, Optional


class RealTimePacketSniffer:
    def __init__(self, on_packet: Callable, interface: Optional[str] = None, bpf_filter: Optional[str] = None):
        self.on_packet = on_packet
        self.interface = interface
        self.bpf_filter = bpf_filter
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        import scapy.all as scapy
        self.is_running = True
        print(f"[SNIFFER] Capturing on interface '{self.interface or 'DEFAULT'}'"
              f"{' filter=' + self.bpf_filter if self.bpf_filter else ''} ...")

        def _worker():
            try:
                scapy.sniff(iface=self.interface, filter=self.bpf_filter, prn=self.on_packet, store=False,
                            stop_filter=lambda p: not self.is_running)
            except Exception as e:
                print(f"[SNIFFER] Capture error: {e}")

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        print("[SNIFFER] Capture stopped.")


def replay_pcap(path: str, on_packet: Callable, limit: Optional[int] = None) -> int:
    """Feed packets of a capture file to `on_packet` in order (their original timestamps are kept)."""
    from scapy.all import PcapReader  # scapy.all registers L2/L3 layers for decoding
    n = 0
    with PcapReader(path) as reader:
        for pkt in reader:
            on_packet(pkt)
            n += 1
            if limit and n >= limit:
                break
    return n
