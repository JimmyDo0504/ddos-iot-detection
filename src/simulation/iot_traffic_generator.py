"""
iot_traffic_generator.py
Legitimate IoT Device Traffic Simulator
Simulates benign IoT sensor traffic (MQTT telemetry & HTTP keepalive)
to establish baseline normal network traffic on the gateway.
"""

import time
import socket
import random
import threading
from typing import Optional

def run_mqtt_sensor(
    target_host: str = "127.0.0.1",
    target_port: int = 1883,
    interval_sec: float = 2.0,
    stop_event: Optional[threading.Event] = None
):
    """
    Simulates a Smart Thermostat / Environmental Sensor:
    Sends small periodic MQTT-like packets (80-150 bytes) every 2-3 seconds.
    """
    print(f"[+] Starting Benign IoT Sensor (MQTT Simulator) -> {target_host}:{target_port} (Interval: {interval_sec}s)")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    count = 0
    while stop_event is None or not stop_event.is_set():
        temp = round(random.uniform(22.0, 26.5), 1)
        humidity = round(random.uniform(50.0, 65.0), 1)
        payload = f'{{"device":"thermostat_01","temp":{temp},"humidity":{humidity},"seq":{count}}}'.encode("utf-8")
        
        try:
            sock.sendto(payload, (target_host, target_port))
            count += 1
            if count % 10 == 0:
                print(f"[IoT Normal] Sent {count} benign telemetry packets ({len(payload)} bytes each)")
        except Exception as e:
            pass
            
        time.sleep(interval_sec + random.uniform(-0.2, 0.2))
        
    sock.close()

def run_camera_keepalive(
    target_host: str = "127.0.0.1",
    target_port: int = 8080,
    interval_sec: float = 1.0,
    stop_event: Optional[threading.Event] = None
):
    """
    Simulates a Smart Security Camera:
    Sends periodic HTTP keepalive / frame header telemetry (150-250 bytes) every 1s.
    """
    print(f"[+] Starting Benign IoT Camera (HTTP Keepalive) -> {target_host}:{target_port} (Interval: {interval_sec}s)")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    count = 0
    while stop_event is None or not stop_event.is_set():
        payload = f"GET /camera/status HTTP/1.1\r\nHost: {target_host}\r\nDevice: Cam_04\r\n\r\n".encode("utf-8")
        try:
            sock.sendto(payload, (target_host, target_port))
            count += 1
        except Exception:
            pass
        time.sleep(interval_sec + random.uniform(-0.1, 0.1))
        
    sock.close()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Benign IoT device emulator (MQTT-like sensor + camera keep-alive)")
    ap.add_argument("--target", default="127.0.0.1", help="IP of the Kali gateway / broker in the lab network")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--http-port", type=int, default=8080)
    a = ap.parse_args()
    print(f"[*] Benign IoT device emulation -> {a.target}  (Ctrl+C to stop)")

    stop_evt = threading.Event()
    t1 = threading.Thread(target=run_mqtt_sensor, args=(a.target, a.mqtt_port, 2.0, stop_evt), daemon=True)
    t2 = threading.Thread(target=run_camera_keepalive, args=(a.target, a.http_port, 1.0, stop_evt), daemon=True)
    t1.start()
    t2.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping IoT normal traffic simulation.")
        stop_evt.set()
