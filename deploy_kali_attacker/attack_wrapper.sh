#!/usr/bin/env bash
# ==============================================================================
# attack_wrapper.sh - Forced command cho khóa SSH của Gateway dashboard.
#
# Đây là script DUY NHẤT mà khóa SSH của dashboard được phép chạy (ràng buộc bằng
# command="..." trong authorized_keys, xem README.md). Bất kể Gateway gửi lệnh gì
# qua SSH, OpenSSH đặt nội dung đó vào biến $SSH_ORIGINAL_COMMAND và luôn thực thi
# script này thay vì lệnh gửi lên -> dashboard không thể chạy shell tùy ý trên máy
# này, chỉ có thể gọi đúng attack_runner.sh với tham số đã được kiểm tra chặt dưới
# đây.
#
# Cú pháp lệnh hợp lệ (những gì dashboard gửi, qua $SSH_ORIGINAL_COMMAND):
#   attack-run <SYN|UDP|ACK|SCAN> <target-ipv4> <port 1-65535> <secs 1-60>
#   attack-stop
# ==============================================================================
set -u
read -r ACTION ARG1 ARG2 ARG3 ARG4 _ <<< "${SSH_ORIGINAL_COMMAND:-}"

case "$ACTION" in
  attack-run)
    CMD="$ARG1"; TARGET="$ARG2"; PORT="$ARG3"; SECS="$ARG4"
    [[ "$CMD" =~ ^(SYN|UDP|ACK|SCAN)$ ]] || { echo "lệnh không hợp lệ" >&2; exit 1; }
    [[ "$TARGET" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "IP mục tiêu không hợp lệ" >&2; exit 1; }
    [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo "cổng không hợp lệ" >&2; exit 1; }
    [[ "$SECS" =~ ^[0-9]{1,2}$ ]] || { echo "thời gian không hợp lệ" >&2; exit 1; }
    (( SECS > 60 )) && SECS=60
    (( SECS < 1 )) && SECS=1
    nohup sudo -n /opt/attack_runner.sh "$CMD" "$TARGET" "$PORT" "$SECS" \
        >/tmp/attack_runner.log 2>&1 < /dev/null &
    disown
    echo "started $CMD -> $TARGET:$PORT (${SECS}s)"
    ;;
  attack-stop)
    sudo -n pkill -x hping3 >/dev/null 2>&1
    sudo -n pkill -x nmap >/dev/null 2>&1
    echo "stopped"
    ;;
  *)
    echo "rejected: only attack-run / attack-stop are allowed" >&2
    exit 1
    ;;
esac
