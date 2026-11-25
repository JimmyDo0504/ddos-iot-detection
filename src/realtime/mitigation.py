"""
mitigation.py
Automated Defense & Mitigation Engine using Linux iptables
Blocks compromised IoT botnet nodes and external attackers upon detection.
Includes whitelist protection, rule deduplication, and cooldown timer.
"""

import os
import sys
import time
import subprocess
from typing import Dict, List, Set

WHITELIST_IPS: Set[str] = {
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "::1",
    "192.168.1.1",    # Gateway IP
    "8.8.8.8",        # Google DNS
    "1.1.1.1"         # Cloudflare DNS
}

class AutoMitigationManager:
    def __init__(self, cooldown_seconds: int = 120, enable_iptables: bool = True):
        self.cooldown_seconds = cooldown_seconds
        self.enable_iptables = enable_iptables and (sys.platform != "win32")
        self.blocked_ips: Dict[str, float] = {} # ip -> timestamp
        self.blocked_history: List[Dict] = []

    def block_ip(self, ip_address: str, attack_type: str, confidence: float = 1.0) -> bool:
        """
        Block attacker IP using iptables DROP rules.
        """
        if ip_address in WHITELIST_IPS:
            return False

        now = time.time()
        if ip_address in self.blocked_ips:
            # Refresh block timestamp
            self.blocked_ips[ip_address] = now
            return False

        self.blocked_ips[ip_address] = now
        entry = {
            "ip": ip_address,
            "attack_type": attack_type,
            "confidence": round(confidence * 100, 1),
            "blocked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "timestamp": now,
            "status": "ACTIVE_BLOCK"
        }
        self.blocked_history.append(entry)

        if self.enable_iptables:
            try:
                # Add iptables DROP rules for both INPUT and FORWARD chains (Gateway protection)
                cmd_input = ["iptables", "-A", "INPUT", "-s", ip_address, "-j", "DROP"]
                cmd_forward = ["iptables", "-A", "FORWARD", "-s", ip_address, "-j", "DROP"]
                subprocess.run(cmd_input, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(cmd_forward, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[MITIGATION] [LINUX IPTABLES] Successfully blocked attacker IP: {ip_address} (DROP)")
            except Exception as e:
                print(f"[MITIGATION] Warning: Failed to execute iptables command: {e}")
        else:
            print(f"[MITIGATION] [SIMULATION/DRY-RUN] Blocked attacker IP: {ip_address} | Reason: {attack_type}")

        return True

    def unblock_ip(self, ip_address: str) -> bool:
        """Remove iptables block rule manually or after cooldown."""
        if ip_address not in self.blocked_ips:
            return False

        del self.blocked_ips[ip_address]

        if self.enable_iptables:
            try:
                cmd_input = ["iptables", "-D", "INPUT", "-s", ip_address, "-j", "DROP"]
                cmd_forward = ["iptables", "-D", "FORWARD", "-s", ip_address, "-j", "DROP"]
                subprocess.run(cmd_input, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(cmd_forward, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[MITIGATION] Unblocked IP via iptables: {ip_address}")
            except Exception as e:
                pass
        else:
            print(f"[MITIGATION] [SIMULATION] Unblocked IP: {ip_address}")

        return True

    def check_expired_blocks(self):
        """Periodically check and release expired blocks after cooldown period."""
        now = time.time()
        expired = [ip for ip, t in self.blocked_ips.items() if now - t > self.cooldown_seconds]
        for ip in expired:
            print(f"[MITIGATION] Cooldown period expired for {ip}. Releasing block...")
            self.unblock_ip(ip)

    def unblock_all(self):
        """Remove every rule this engine added (called on shutdown so no stale DROP rules remain)."""
        for ip in list(self.blocked_ips):
            self.unblock_ip(ip)

    def get_active_blocks(self) -> List[Dict]:
        return [
            {"ip": ip, "blocked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))}
            for ip, t in self.blocked_ips.items()
        ]
