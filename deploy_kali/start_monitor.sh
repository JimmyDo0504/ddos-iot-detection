#!/usr/bin/env bash
# ==============================================================================
# start_monitor.sh - Khởi động giám sát realtime + Web Dashboard trên Kali
#   sudo ./start_monitor.sh eth0 5000            # chỉ cảnh báo
#   sudo ./start_monitor.sh eth0 5000 --block    # cảnh báo + tự chặn IP bằng iptables
# ==============================================================================
cd "$(dirname "$0")"
IFACE="${1:-eth0}"
PORT="${2:-5000}"
BLOCK="${3:-}"

if [ "$EUID" -ne 0 ]; then
  echo "[-] Cần quyền root (bắt gói + iptables): sudo ./start_monitor.sh ${IFACE} ${PORT} ${BLOCK}"
  exit 1
fi
if [ ! -f model/preprocessor.json ] || { [ ! -f model/best_dl_int8.onnx ] && [ ! -f model/best_dl.onnx ]; }; then
  echo "[-] Thiếu gói mô hình trong deploy_kali/model/. Chạy 'python run_pipeline.py --all' trước."
  exit 1
fi
echo "[+] Giám sát trên card mạng: ${IFACE} | Dashboard: http://<ip-kali>:${PORT} | ${BLOCK:-không tự chặn}"
python3 ../src/realtime/web_dashboard.py --interface "${IFACE}" --port "${PORT}" ${BLOCK}
