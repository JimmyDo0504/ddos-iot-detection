#!/usr/bin/env bash
# ==============================================================================
# setup_kali.sh - Cài môi trường cho Kali Linux làm gateway giám sát IoT
# Chạy từ thư mục deploy_kali:  sudo ./setup_kali.sh
# ==============================================================================
set -e
cd "$(dirname "$0")"

echo "[+] 1. Cài gói hệ thống (python3, pip, scapy, iptables, tcpdump)..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-scapy iptables tcpdump

echo "[+] 2. Cài thư viện Python cho suy luận realtime (requirements-kali.txt)..."
sudo pip3 install --break-system-packages -r ../requirements-kali.txt

echo "[+] 3. Bật IP forwarding (để Kali làm gateway)..."
sudo sysctl -w net.ipv4.ip_forward=1

echo "[+] 4. Kiểm tra môi trường..."
python3 ../check_env.py --kali

chmod +x start_monitor.sh
if [ ! -f model/preprocessor.json ]; then
  echo "[!] Chưa có deploy_kali/model/ (best_dl*.onnx + preprocessor.json)."
  echo "    Hãy chạy huấn luyện (python run_pipeline.py --all) rồi chép cả thư mục dự án sang Kali."
fi
echo "[V] Xong. Khởi động giám sát:  sudo ./start_monitor.sh <interface> [port] [--block]"
