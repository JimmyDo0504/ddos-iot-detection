# Chạy lệnh tấn công của máy Kali-Attacker ngay trên dashboard

Mục này nối thêm một khung điều khiển trên dashboard (`Điều Khiển Kali-Attacker`) để bấm nút
SYN Flood / UDP Flood / ACK Flood / Port Scan / Dừng thay vì gõ `hping3`/`nmap` thủ công trên
máy Kali-Attacker, giống cách khung "Điều Khiển ESP32" đã làm với thiết bị IoT.

## Vì sao không SSH thẳng với lệnh tùy ý

Nếu dashboard được phép gửi *bất kỳ* lệnh shell nào qua SSH, ai chiếm được dashboard (hoặc đoán
được lỗi trong ô nhập liệu) có thể chạy lệnh tùy ý trên máy Kali-Attacker. Thay vào đó khóa SSH
của dashboard bị **ép chỉ được chạy đúng một script** (`attack_wrapper.sh`, qua tùy chọn
`command=` trong `authorized_keys`): dù Gateway gửi gì qua SSH, OpenSSH luôn chạy script này, nội
dung Gateway gửi chỉ nằm trong biến `$SSH_ORIGINAL_COMMAND` để script tự kiểm tra lại (kiểu lệnh
nằm trong danh sách SYN/UDP/ACK/SCAN, IP mục tiêu đúng định dạng IPv4, cổng 1-65535, thời gian
tối đa 60 giây) rồi mới gọi `attack_runner.sh` bằng `sudo` không cần mật khẩu, nhưng `sudo` cũng
chỉ được phép chạy đúng file đó. Đây cùng nguyên tắc với nút ESP32 (lệnh cố định + token), không
phải một kênh điều khiển từ xa mở.

**MobaXterm** (đã có sẵn trên máy bạn) không tham gia vào lúc demo — Flask chạy trên máy ảo
Gateway và tự mở kết nối SSH bằng thư viện chuẩn của Python/OpenSSH, không qua MobaXterm. MobaXterm
chỉ tiện để: (1) SSH thủ công vào hai máy ảo lúc cấu hình một lần dưới đây, và (2) mở thêm tab
`tail -f /tmp/attack_runner.log` trên Kali-Attacker để xem trực tiếp khi demo.

## Cài đặt một lần

### Bước 1 — Trên Kali-Attacker: cài script và quyền sudo

```bash
sudo cp attack_runner.sh /opt/attack_runner.sh
sudo chmod 750 /opt/attack_runner.sh
sudo chown root:root /opt/attack_runner.sh

sudo cp sudoers_attack_runner /etc/sudoers.d/attack_runner
sudo chmod 440 /etc/sudoers.d/attack_runner
sudo visudo -c -f /etc/sudoers.d/attack_runner    # phải in "parsed OK"
```

`attack_wrapper.sh` thì KHÔNG đặt trong `/opt` cùng quyền root — nó chạy dưới quyền user SSH bình
thường (ví dụ `kali`), chỉ `attack_runner.sh` mới cần chạy bằng root (để `hping3`/`nmap` mở raw
socket). Chép nó vào thư mục home của user đó:

```bash
cp attack_wrapper.sh ~/attack_wrapper.sh
chmod 700 ~/attack_wrapper.sh
```

### Bước 2 — Trên Gateway Kali: tạo cặp khóa SSH riêng cho việc này

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_attacker -N "" -C "gateway-dashboard"
cat ~/.ssh/id_ed25519_attacker.pub
```

Chép dòng public key in ra.

### Bước 3 — Trên Kali-Attacker: thêm khóa với forced command

Mở `~/.ssh/authorized_keys` (tạo nếu chưa có, `chmod 700 ~/.ssh && chmod 600
~/.ssh/authorized_keys`), thêm **một dòng** (thay `<public-key>` bằng dòng đã chép ở Bước 2,
thay `/home/kali` bằng home thật của user):

```
command="/home/kali/attack_wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <public-key>
```

`no-pty` khiến phiên SSH này không cấp terminal — đúng ý vì ta chỉ cần chạy một script nền, không
cần gõ tương tác.

### Bước 4 — Trên Gateway: thử kết nối và khai báo IP máy Kali-Attacker

```bash
ssh -i ~/.ssh/id_ed25519_attacker kali@<IP-Kali-Attacker> "attack-run SYN 127.0.0.1 80 3"
```

Phải in ra `started SYN -> 127.0.0.1:80 (3s)` và không hỏi mật khẩu. Nếu máy Kali-Attacker đổi
IP mỗi lần bật (thường vậy với hotspot), gõ thẳng IP vào ô "IP máy Kali-Attacker" trên dashboard
mỗi lần demo — không bắt buộc phải đặt biến môi trường. Muốn dashboard tự điền sẵn, đặt trước khi
chạy `start_monitor.sh`:

```bash
export ATTACKER_HOST=<IP-Kali-Attacker>   # tùy chọn, chỉ để điền sẵn ô nhập trên dashboard
sudo -E ./start_monitor.sh eth0 5000 --block
```

(`ATTACKER_USER` mặc định `kali`, `ATTACKER_SSH_KEY` mặc định `~/.ssh/id_ed25519_attacker`; đổi
qua biến môi trường cùng tên nếu khác.)

## Dùng khi demo

Trên dashboard, khung "Điều Khiển Kali-Attacker": nhập IP máy Kali-Attacker (nếu `ATTACKER_HOST`
chưa đặt), chọn cổng và thời gian, bấm SYN Flood / UDP Flood / ACK Flood / Port Scan. Mục tiêu
tấn công luôn là IP của chính Gateway — dashboard tự lấy, không cho nhập tay, để không ai lợi dụng
gửi tấn công sang máy khác. Mỗi đợt tự dừng sau số giây đã đặt (tối đa 60s); bấm **Dừng** để dừng
ngay (tắt mọi tiến trình `hping3`/`nmap` đang chạy trên Kali-Attacker).

## Kiểm tra sự cố

- **"Không thấy khóa SSH ... trên Gateway"**: chưa làm Bước 2, hoặc `ATTACKER_SSH_KEY` trỏ sai
  đường dẫn.
- **"Kali-Attacker ... không phản hồi (SSH timeout)"**: kiểm tra IP, card mạng Bridged, dịch vụ
  SSH trên Kali-Attacker (`sudo systemctl status ssh`).
- **SSH hỏi mật khẩu thay vì chạy thẳng**: `authorized_keys` sai quyền (`chmod 600`) hoặc
  `~/.ssh` sai quyền (`chmod 700`), hoặc dán sai dòng public key.
- **"permission denied" khi gọi `sudo -n`**: kiểm tra lại `/etc/sudoers.d/attack_runner` bằng
  `sudo visudo -c -f ...`, và đường dẫn trong đó phải khớp y hệt nơi đã `cp` `attack_runner.sh`.
- Xem log của lần chạy gần nhất trên Kali-Attacker: `cat /tmp/attack_runner.log`.
