"""
web_dashboard.py
Real-Time Interactive Web Dashboard for IoT DDoS Monitoring
Flask application rendering responsive status cards, live threat chart,
alert feeds, and automated iptables mitigation controls.
"""

import os
import sys
import time
import json
import threading
from flask import Flask, jsonify, render_template_string, request

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from realtime.detection_engine import IoTRealtimeMonitor

app = Flask(__name__)

# Global Monitor Reference
monitor_instance: IoTRealtimeMonitor = None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hệ Thống Giám Sát Tấn Công DDoS-IoT Thời Gian Thực</title>
    <style>
        :root {
            --bg: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-dim: #94a3b8;
            --accent: #38bdf8;
            --danger: #ef4444;
            --warning: #f59e0b;
            --success: #10b981;
            --border: #334155;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            margin: 0;
            padding: 24px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }
        .header h1 {
            font-size: 24px;
            margin: 0;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .pulse-dot {
            width: 12px;
            height: 12px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--success);
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1.1); box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }
        .grid-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }
        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .card-title {
            font-size: 13px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }
        .card-value {
            font-size: 28px;
            font-weight: 700;
        }
        .status-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
        }
        .status-normal { background: rgba(16, 185, 129, 0.2); color: var(--success); border: 1px solid var(--success); }
        .status-danger { background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid var(--danger); }
        
        .section-title {
            font-size: 18px;
            margin: 24px 0 12px 0;
            color: var(--accent);
        }
        .actions-panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 24px;
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            align-items: center;
        }
        .btn {
            background: #2563eb;
            color: white;
            border: none;
            padding: 10px 18px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }
        .btn:hover { background: #1d4ed8; }
        .btn-danger { background: #dc2626; }
        .btn-danger:hover { background: #b91c1c; }
        .btn-warning { background: #d97706; }
        .btn-warning:hover { background: #b45309; }
        
        table {
            width: 100%;
            border-collapse: collapse;
            background: var(--card-bg);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border);
        }
        th, td {
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 14px;
        }
        th {
            background-color: #273549;
            color: var(--text-dim);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 12px;
        }
        tr:hover { background: rgba(255, 255, 255, 0.02); }
        .pill {
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
        }
        .pill-critical { background: #dc2626; color: white; }
        .pill-high { background: #ea580c; color: white; }
        .pill-medium { background: #d97706; color: white; }
        .two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 20px; }
        .sel { background: #0f172a; color: var(--text-main); border: 1px solid var(--border); border-radius: 8px; padding: 9px 10px; }
        .dim { color: var(--text-dim); font-size: 13px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>
            <span class="pulse-dot" id="live-indicator"></span>
            Hệ Thống Giám Sát IoT Gateway & Phát Hiện DDoS Real-time
        </h1>
        <div>Mô hình đang chạy: <strong id="model-name">-</strong> | Gateway: <strong>Kali Linux</strong></div>
    </div>

    <div class="grid-cards">
        <div class="card">
            <div class="card-title">Trạng Thái An Ninh Mạng</div>
            <div class="card-value" id="status-card">
                <span class="status-badge status-normal">BÌNH THƯỜNG</span>
            </div>
        </div>
        <div class="card">
            <div class="card-title">Tốc Độ Lưu Lượng (Packets/s)</div>
            <div class="card-value" id="rate-card" style="color: var(--accent);">0.0</div>
        </div>
        <div class="card">
            <div class="card-title">Tổng Gói Tin Đã Kiểm Định</div>
            <div class="card-value" id="packets-card">0</div>
        </div>
        <div class="card">
            <div class="card-title">Số Đợt Tấn Công Phát Hiện</div>
            <div class="card-value" id="threats-card" style="color: var(--danger);">0</div>
        </div>
    </div>

    <div class="two-col">
      <div>
        <div class="section-title">Mô Hình Phát Hiện (đổi trực tiếp khi đang chạy)</div>
        <div class="actions-panel">
            <select id="model-select" class="sel"></select>
            <button class="btn" onclick="applyModel()">Áp dụng</button>
            <span id="model-msg" class="dim"></span>
            <div id="model-info" class="dim" style="width:100%;"></div>
        </div>
      </div>
      <div>
        <div class="section-title">Điều Khiển ESP32 (giả lập thiết bị bị chiếm quyền)</div>
        <div class="actions-panel">
            <select id="dev-select" class="sel"></select>
            <input id="dev-manual" class="sel" placeholder="hoặc nhập IP ESP32" style="width:150px;">
            <label class="dim">Thời gian <input id="dev-secs" class="sel" type="number" value="15" min="3" max="120" style="width:60px;"> s</label>
            <button class="btn btn-danger" onclick="esp32('UDP')">UDP Flood</button>
            <button class="btn btn-warning" onclick="esp32('SYN')">TCP SYN Flood</button>
            <button class="btn" onclick="esp32('STOP')">Dừng</button>
            <div id="dev-msg" class="dim" style="width:100%;"></div>
        </div>
      </div>
      <div>
        <div class="section-title">Điều Khiển Kali-Attacker (hping3 / nmap qua SSH)</div>
        <div class="actions-panel">
            <input id="atk-host" class="sel" placeholder="IP máy Kali-Attacker" style="width:150px;">
            <label class="dim">Cổng <input id="atk-port" class="sel" type="number" value="80" min="1" max="65535" style="width:70px;"></label>
            <label class="dim">Thời gian <input id="atk-secs" class="sel" type="number" value="15" min="3" max="60" style="width:60px;"> s</label>
            <button class="btn btn-warning" onclick="attacker('SYN')">SYN Flood</button>
            <button class="btn btn-danger" onclick="attacker('UDP')">UDP Flood</button>
            <button class="btn btn-danger" onclick="attacker('ACK')">ACK Flood</button>
            <button class="btn" onclick="attacker('SCAN')">Port Scan</button>
            <button class="btn" onclick="attacker('STOP')">Dừng</button>
            <div id="atk-msg" class="dim" style="width:100%;"></div>
            <div class="dim" style="width:100%;">Cần cấu hình SSH một lần trên hai máy Kali — xem deploy_kali_attacker/README.md.</div>
        </div>
      </div>
    </div>

    <div class="section-title">IP Đang Bị Chặn (iptables)</div>
    <div class="actions-panel">
        <code id="blocked-ips">không có</code>
        <button class="btn" style="margin-left:auto;" onclick="unblockAll()">Gỡ chặn tất cả</button>
        <span class="dim">Lớp nhận diện: <span id="model-classes">-</span></span>
    </div>

    <div class="section-title">Nhật Ký Cảnh Báo An Ninh Thời Gian Thực (Alert Feed)</div>
    <table>
        <thead>
            <tr>
                <th>Thời Gian</th>
                <th>Mức Độ</th>
                <th>Dạng Tấn Công</th>
                <th>Độ Tin Cậy</th>
                <th>IP Nguồn</th>
                <th>Tốc Độ (pkts/s)</th>
                <th>Độ Trễ Suy Luận</th>
                <th>Mô Hình</th>
            </tr>
        </thead>
        <tbody id="alerts-table">
            <tr><td colspan="8" style="text-align: center; color: var(--text-dim);">Chưa có cảnh báo nào... Mạng IoT đang hoạt động an toàn.</td></tr>
        </tbody>
    </table>

    <script>
        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                document.getElementById('rate-card').innerText = data.current_pkt_rate.toLocaleString();
                document.getElementById('packets-card').innerText = data.total_packets.toLocaleString();
                document.getElementById('threats-card').innerText = data.active_threats_count.toLocaleString();
                
                const statusCard = document.getElementById('status-card');
                const indicator = document.getElementById('live-indicator');
                document.getElementById('model-name').innerText = data.model || '-';
                document.getElementById('model-classes').innerText = (data.classes || []).join(', ');
                document.getElementById('blocked-ips').innerText = (data.blocked_ips || []).map(b => b.ip).join(', ') || 'không có';
                if (data.recent_alerts.length > 0 && (Date.now() - new Date(data.recent_alerts[0].timestamp.replace(' ', 'T')).getTime() < 5000)) {
                    statusCard.innerHTML = '<span class="status-badge status-danger">PHÁT HIỆN TẤN CÔNG</span>';
                    indicator.style.background = '#ef4444';
                } else {
                    statusCard.innerHTML = '<span class="status-badge status-normal">BÌNH THƯỜNG</span>';
                    indicator.style.background = '#10b981';
                }

                // Render alerts
                const tbody = document.getElementById('alerts-table');
                if (data.recent_alerts.length > 0) {
                    tbody.innerHTML = data.recent_alerts.slice(0, 15).map(a => `
                        <tr>
                            <td>${a.timestamp}</td>
                            <td><span class="pill pill-${a.severity.toLowerCase()}">${a.severity}</span></td>
                            <td><strong>${a.attack_type}</strong></td>
                            <td>${a.confidence}%</td>
                            <td><code>${a.dominant_src}</code></td>
                            <td>${a.pkt_rate}</td>
                            <td>${a.inference_latency_ms} ms</td>
                            <td>${a.model || ''}</td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                console.error(err);
            }
        }

        const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
        let MODELS = [];
        function describe(m) {
            if (!m) return '';
            const f = v => (v === undefined || v === null) ? '-' : v;
            return `${esc(m.category || '')} | đầu vào: ${m.input === 'window' ? 'chuỗi gói tin' : '1 gói tin'} | `
                 + `F1 test: ${f(m.test_F1_macro)}% | Accuracy: ${f(m.test_Accuracy)}% | `
                 + `${f(m.size_KB)} KB | ${f(m.onnx_latency_ms)} ms/mẫu` + (m.name === 'KNN' ? ' | (chậm, không hợp với flood lớn)' : '');
        }
        async function loadModels() {
            const r = await (await fetch('/api/models')).json();
            MODELS = r.models;
            const sel = document.getElementById('model-select');
            sel.innerHTML = MODELS.map(m => `<option value="${esc(m.name)}" ${m.name === r.active ? 'selected' : ''}>`
                + `${esc(m.name)} (${m.category === 'Deep Learning' ? 'DL' : 'ML'}${m.test_F1_macro !== undefined ? ', F1 ' + m.test_F1_macro : ''})</option>`).join('');
            document.getElementById('model-info').innerText = describe(MODELS.find(m => m.name === r.active));
        }
        async function applyModel() {
            const name = document.getElementById('model-select').value;
            const r = await (await fetch('/api/model', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                       body: JSON.stringify({name})})).json();
            document.getElementById('model-msg').innerText = r.ok ? 'Đã chuyển sang ' + name : 'Lỗi: ' + r.error;
            document.getElementById('model-info').innerText = describe(r.model);
        }
        async function loadDevices() {
            const r = await (await fetch('/api/devices')).json();
            const sel = document.getElementById('dev-select'), cur = sel.value;
            sel.innerHTML = r.devices.length
                ? r.devices.map(d => `<option value="${esc(d.ip)}">${esc(d.ip)} (telemetry ${d.age}s trước)</option>`).join('')
                : '<option value="">chưa thấy ESP32</option>';
            if (cur) sel.value = cur;
        }
        async function esp32(cmd) {
            const ip = document.getElementById('dev-manual').value.trim() || document.getElementById('dev-select').value;
            const seconds = parseInt(document.getElementById('dev-secs').value || '15');
            if (!ip) { document.getElementById('dev-msg').innerText = 'Chưa có IP ESP32'; return; }
            const r = await (await fetch('/api/esp32', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                       body: JSON.stringify({ip, cmd, seconds})})).json();
            document.getElementById('dev-msg').innerText = (r.ok ? 'ESP32 trả lời: ' + r.reply : 'Lỗi: ' + r.error);
        }
        async function unblockAll() {
            await fetch('/api/unblock', {method: 'POST'});
        }
        async function loadAttackerConfig() {
            const r = await (await fetch('/api/attacker')).json();
            const host = document.getElementById('atk-host');
            if (r.host && !host.value) host.value = r.host;
        }
        async function attacker(cmd) {
            const host = document.getElementById('atk-host').value.trim();
            const port = parseInt(document.getElementById('atk-port').value || '80');
            const seconds = parseInt(document.getElementById('atk-secs').value || '15');
            if (!host) { document.getElementById('atk-msg').innerText = 'Chưa nhập IP máy Kali-Attacker'; return; }
            const r = await (await fetch('/api/attacker', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                       body: JSON.stringify({host, cmd, port, seconds})})).json();
            document.getElementById('atk-msg').innerText = (r.ok ? r.reply : 'Lỗi: ' + r.error);
        }
        setInterval(fetchStats, 1000);
        setInterval(loadDevices, 3000);
        fetchStats(); loadModels(); loadDevices(); loadAttackerConfig();
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/stats")
def get_stats():
    global monitor_instance
    if monitor_instance is None:
        return jsonify({
            "total_packets": 0,
            "current_pkt_rate": 0.0,
            "active_threats_count": 0,
            "recent_alerts": []
        })
    return jsonify(monitor_instance.stats)

ESP32_PORT = int(os.environ.get("ESP32_CTRL_PORT", "4210"))
ESP32_TOKEN = os.environ.get("ESP32_TOKEN", "iotlab2026")


@app.route("/api/models")
def get_models():
    b = monitor_instance.bundle
    return jsonify({"active": b.model_name, "models": b.list_models()})


@app.route("/api/model", methods=["POST"])
def set_model():
    name = (request.get_json(silent=True) or {}).get("name", "")
    try:
        info = monitor_instance.bundle.switch(name)
        print(f"[DASHBOARD] Detection model switched to: {name}")
        return jsonify({"ok": True, "model": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/devices")
def get_devices():
    now = time.time()
    devs = sorted(monitor_instance.iot_devices.items(), key=lambda kv: -kv[1])
    return jsonify({"devices": [{"ip": ip, "age": int(max(0, now - t))} for ip, t in devs]})


@app.route("/api/esp32", methods=["POST"])
def esp32_command():
    """Send a control command to the ESP32 over UDP: 'IOTLAB <token> <UDP|SYN|STOP|STATUS> <seconds>'."""
    import ipaddress
    import socket
    d = request.get_json(silent=True) or {}
    ip, cmd = str(d.get("ip", "")).strip(), str(d.get("cmd", "STATUS")).upper()
    secs = max(1, min(int(d.get("seconds", 15)), 120))      # every attack stops by itself
    if cmd not in ("UDP", "SYN", "STOP", "STATUS"):
        return jsonify({"ok": False, "error": "unknown command"}), 400
    try:
        if not ipaddress.ip_address(ip).is_private:
            return jsonify({"ok": False, "error": "chỉ cho phép IP trong mạng lab (private)"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "IP không hợp lệ"}), 400
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.5)
    try:
        sock.sendto(f"IOTLAB {ESP32_TOKEN} {cmd} {secs}".encode(), (ip, ESP32_PORT))
        reply = sock.recvfrom(256)[0].decode(errors="replace")
        print(f"[DASHBOARD] ESP32 {ip} <- {cmd} {secs}s | reply: {reply}")
        return jsonify({"ok": True, "reply": reply})
    except socket.timeout:
        return jsonify({"ok": False, "error": f"ESP32 {ip} không trả lời (kiểm tra firmware/IP)"})
    finally:
        sock.close()


ATTACKER_HOST = os.environ.get("ATTACKER_HOST", "")
ATTACKER_USER = os.environ.get("ATTACKER_USER", "kali")
ATTACKER_SSH_KEY = os.environ.get("ATTACKER_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519_attacker"))


@app.route("/api/attacker", methods=["GET"])
def attacker_config():
    """Giá trị mặc định để điền sẵn ô IP máy Kali-Attacker trên dashboard."""
    return jsonify({"host": ATTACKER_HOST})


@app.route("/api/attacker", methods=["POST"])
def attacker_command():
    """
    Kích hoạt kịch bản hping3/nmap trên máy Kali-Attacker qua SSH.
    Phía Kali-Attacker PHẢI giới hạn khóa SSH này bằng forced command (command=... trong
    authorized_keys, xem deploy_kali_attacker/README.md) để chỉ chạy đúng attack_wrapper.sh,
    không cho thực thi lệnh shell tùy ý dù lệnh gửi lên là gì.
    """
    import ipaddress
    import subprocess
    d = request.get_json(silent=True) or {}
    host = str(d.get("host") or ATTACKER_HOST).strip()
    cmd = str(d.get("cmd", "")).upper()
    secs = max(1, min(int(d.get("seconds", 15)), 60))
    port = max(1, min(int(d.get("port", 80)), 65535))
    if cmd not in ("SYN", "UDP", "ACK", "SCAN", "STOP"):
        return jsonify({"ok": False, "error": "unknown command"}), 400
    if not host:
        return jsonify({"ok": False, "error": "Chưa nhập/cấu hình IP máy Kali-Attacker"}), 400
    try:
        if not ipaddress.ip_address(host).is_private:
            return jsonify({"ok": False, "error": "chỉ cho phép IP trong mạng lab (private)"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "IP không hợp lệ"}), 400
    if not os.path.exists(ATTACKER_SSH_KEY):
        return jsonify({"ok": False, "error": f"Không thấy khóa SSH {ATTACKER_SSH_KEY} trên Gateway "
                                              "(xem deploy_kali_attacker/README.md)"}), 400

    ssh_base = ["ssh", "-i", ATTACKER_SSH_KEY, "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{ATTACKER_USER}@{host}"]
    if cmd == "STOP":
        try:
            out = subprocess.run(ssh_base + ["attack-stop"], capture_output=True, text=True, timeout=10)
            reply = out.stdout.strip() or out.stderr.strip() or "đã gửi lệnh dừng"
            print(f"[DASHBOARD] Attacker {host} <- STOP | reply: {reply}")
            return jsonify({"ok": out.returncode == 0, "reply": reply})
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": f"Kali-Attacker {host} không phản hồi (SSH timeout)"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # mục tiêu tấn công luôn là IP của chính Gateway (không cho người dùng tự chọn IP khác)
    target = next(iter((monitor_instance.local_ips if monitor_instance else set()) - {"127.0.0.1", "::1"}), None)
    if not target:
        return jsonify({"ok": False, "error": "Không xác định được IP của Gateway để làm mục tiêu"}), 400
    remote_cmd = f"attack-run {cmd} {target} {port} {secs}"
    try:
        p = subprocess.Popen(ssh_base + [remote_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[DASHBOARD] Attacker {host} <- {cmd} target={target}:{port} {secs}s (ssh pid {p.pid})")
        return jsonify({"ok": True, "reply": f"Đã gửi lệnh {cmd} tới {host} (mục tiêu {target}:{port}), "
                                              f"tự dừng sau {secs}s"})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Không tìm thấy lệnh ssh trên Gateway"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/unblock", methods=["POST"])
def unblock_all():
    monitor_instance.mitigation.unblock_all()
    return jsonify({"ok": True})


def run_dashboard(monitor: IoTRealtimeMonitor, port: int = 5000):
    global monitor_instance
    monitor_instance = monitor
    print(f"\n[DASHBOARD] Web Dashboard running at: http://127.0.0.1:{port}")
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # hide per-request "GET /api/stats" lines
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        print("\n[SHUTDOWN] Stopping capture and removing iptables rules added by this run ...")
        monitor.stop()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="IoT DDoS Realtime Monitor Web Dashboard")
    parser.add_argument("--interface", "-i", default=None, help="Network interface to monitor (e.g. wlan0, eth0, lo)")
    parser.add_argument("--port", "-p", type=int, default=5000, help="Web dashboard port")
    parser.add_argument("--block", action="store_true", help="Enable automatic iptables blocking")
    parser.add_argument("--model-name", default=None, help="initial model (default: registry default)")
    parser.add_argument("--int8", action="store_true", help="use INT8 variants of DL models when available")
    parser.add_argument("--local-ip", action="append", default=[],
                        help="IP never alerted/blocked (e.g. the router); repeatable")
    args = parser.parse_args()

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    from realtime.detection_engine import default_bundle_paths
    model_path, prep_path = default_bundle_paths(base_dir)
    print(f"[STARTUP] Network Interface: {args.interface or 'AUTO/DEFAULT'}")
    print(f"[STARTUP] Model ONNX: {model_path}")
    print(f"[STARTUP] Preprocessor: {prep_path}")

    monitor = IoTRealtimeMonitor(
        model_path=model_path,
        prep_path=prep_path,
        interface=args.interface,
        window_sec=1.0,
        enable_mitigation=args.block,
        prefer_int8=args.int8,
        local_ips=args.local_ip,
    )
    if args.model_name:
        monitor.bundle.switch(args.model_name)
    monitor.start()
    run_dashboard(monitor, port=args.port)
