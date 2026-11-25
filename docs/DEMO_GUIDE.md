# Hướng dẫn cài đặt và chạy demo

Tài liệu này giả định máy đã có sẵn:

- Ít nhất **một máy ảo Kali Linux** (VMware Workstation/Player) — dùng làm **Gateway**.
- **Arduino IDE** đã cài sẵn trên máy Windows/host.
- Một mạch **ESP32 DevKit V1** (hoặc tương đương) nối qua cáp USB.

Để chạy đủ 4 kịch bản demo, cần thêm **một máy ảo Kali Linux thứ hai** làm máy tấn
công (**Kali-Attacker**) — có thể clone từ máy Kali đã có.

---

## 1. Chuẩn bị mạng (Bridged)

Hai máy ảo Kali và ESP32 phải nằm **cùng một mạng LAN thật** (không dùng NAT), để
Gateway bắt được đúng gói tin có IP nguồn thật.

1. Bật một mạng **Wi-Fi 2,4 GHz** (hotspot hoặc router) — ESP32 chỉ hỗ trợ 2,4 GHz.
2. Trong VMware: **Edit → Virtual Network Editor → Change Settings** (cần quyền
   Admin). Nếu danh sách **không có dòng Bridged nào**, bấm **Add Network**, chọn
   radio **Bridged**, ở ô **Bridged to** chọn **đúng card Wi-Fi của laptop** (không
   để "Automatic" — chế độ tự động hay bắc cầu nhầm card khác khi máy có nhiều card
   mạng, gây mất mạng dù máy ảo vẫn có IP).
3. Với **từng máy ảo Kali**: Settings → Network Adapter → **Custom: Specific virtual
   network** → chọn đúng mạng Bridged vừa tạo.
4. Nếu card Wi-Fi dùng chip **Killer** và vẫn không có mạng sau bước trên: Control
   Panel → Network Connections → chuột phải card Wi-Fi → Properties → tick
   **"VMware Bridge Protocol"**.

Kiểm tra trên từng máy Kali:

```bash
ip -4 addr show eth0
ping -c 3 1.1.1.1
```

`ping` phải có phản hồi. Nếu không, xem lại bước 2–4 trước khi tiếp tục.

---

## 2. Lấy mã nguồn

Trên máy Windows (host):

```powershell
git clone [<URL-repo-này>](https://github.com/JimmyDo0504/ddos-iot-detection.git) ddos-iot
```

Trên **Kali Gateway**, bật SSH rồi chép repo sang (không cần `datasets/`, `data/`,
`results/` — các script dưới đây không dùng tới):

```bash
sudo systemctl enable --now ssh
```

Từ Windows:

```powershell
cd ddos-iot
ssh kali@<IP-Kali-Gateway> "mkdir -p ~/ddos_iot"
scp check_env.py requirements-kali.txt kali@<IP-Kali-Gateway>:~/ddos_iot/
scp -r src deploy_kali kali@<IP-Kali-Gateway>:~/ddos_iot/
```

---

## 3. Cài đặt Gateway

Trên **Kali Gateway**:

```bash
cd ~/ddos_iot/deploy_kali
chmod +x setup_kali.sh start_monitor.sh
sudo ./setup_kali.sh
```

Script cài `scapy`, `iptables`, `tcpdump`, thư viện Python suy luận (ONNX Runtime,
Flask), bật IP forwarding, và kiểm tra gói mô hình (`model/registry.json`) đã có
sẵn trong `deploy_kali/`.

---

## 4. Cài đặt Kali-Attacker (để bấm tấn công ngay trên dashboard)

Nếu chỉ cần chạy tay `hping3`/`nmap` thì có thể bỏ qua bước này và làm thủ công ở
Mục 7. Để bấm nút trên dashboard, làm theo đúng
**[deploy_kali_attacker/README.md](../deploy_kali_attacker/README.md)** — tóm tắt:

1. Chép `deploy_kali_attacker/` sang Kali-Attacker, cài `attack_runner.sh` +
   quyền `sudo` giới hạn, chép `attack_wrapper.sh`.
2. Trên Kali Gateway, tạo khóa SSH riêng cho việc này.
3. Trên Kali-Attacker, thêm khóa vào `authorized_keys` với **forced command** (khóa
   chỉ được phép chạy đúng `attack_wrapper.sh`, không chạy được lệnh tùy ý).
4. Thử kết nối từ Gateway, phải thấy `started ...` và không hỏi mật khẩu.

Cài thêm công cụ tấn công trên Kali-Attacker:

```bash
sudo apt update
sudo apt install -y hping3 nmap openssh-server
sudo systemctl enable --now ssh
```

---

## 5. Nạp firmware cho ESP32

1. Cắm ESP32 vào máy Windows qua USB.
2. Mở `deploy_esp32/esp32_iot_node/esp32_iot_node.ino` bằng Arduino IDE.
3. Nếu chưa có board ESP32: **File → Preferences**, thêm URL Board Manager:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`,
   sau đó **Tools → Board Manager** cài gói `esp32 by Espressif`.
4. Sửa 3 dòng cho khớp mạng và IP Gateway thật của bạn:
   ```cpp
   const char* ssid = "<tên Wi-Fi 2,4 GHz>";
   const char* password = "<mật khẩu Wi-Fi>";
   const char* gateway_ip = "<IP của Kali Gateway>";
   ```
5. **Tools → Board**: chọn **ESP32 Dev Module**. **Tools → Port**: chọn đúng cổng
   COM. Bấm **Upload**.
6. Mở **Serial Monitor** (115200 baud), phải thấy:
   ```
   [V] Ket noi thanh cong vao Kali Gateway!
   [NORMAL] Gui telemetry #1 ...
   ```

Chi tiết và cách điều khiển tấn công từ Serial Monitor: xem
[deploy_esp32/README.md](../deploy_esp32/README.md).

---

## 6. Khởi động giám sát và mở dashboard

Trên **Kali Gateway**:

```bash
cd ~/ddos_iot/deploy_kali
sudo iptables -F INPUT
export ATTACKER_HOST=<IP-Kali-Attacker>   # bỏ qua nếu không làm Mục 4
sudo -E ./start_monitor.sh eth0 5000 --block
```

Phải thấy đúng các dòng:

```
[ENGINE] Own IPs (never alerted/blocked): ...
[STARTUP] Model ONNX: .../deploy_kali/model/registry.json
[ENGINE] Monitoring with BiGRU-Attention | window 1.0s | ...
```

Trên Windows, mở trình duyệt: `http://<IP-Kali-Gateway>:5000`.

Nếu lưu lượng bình thường (router, mDNS...) gây cảnh báo giả, thêm tham số
`--local-ip <IP-cần-bỏ-qua>` (lặp lại được nhiều lần) khi khởi động, ví dụ:

```bash
sudo -E python3 ../src/realtime/web_dashboard.py --interface eth0 --port 5000 \
    --block --local-ip 192.168.1.1
```

---

## 7. Bốn kịch bản demo

Trước mỗi kịch bản, bấm **"Gỡ chặn tất cả"** trên dashboard (hoặc
`sudo iptables -F INPUT`) để màn hình gọn.

### Kịch bản 1 — Lưu lượng bình thường
Để ESP32 chạy yên 1–2 phút. Kỳ vọng: thẻ trạng thái **BÌNH THƯỜNG**, **0 cảnh báo**.

### Kịch bản 2 — UDP Flood từ ESP32
Khung "Điều Khiển ESP32": chọn IP ESP32, đặt số giây (ví dụ 15), bấm **UDP Flood**.
Kỳ vọng: cảnh báo `Mirai_udpplain`, độ tin cậy trên 98%, IP ESP32 bị chặn.

### Kịch bản 3 — SYN Flood từ Kali-Attacker
Khung "Điều Khiển Kali-Attacker": nhập IP Attacker (nếu chưa tự điền), bấm
**SYN Flood**. Không có khung này (bỏ qua Mục 4) thì chạy tay trên Kali-Attacker:

```bash
sudo timeout 15s hping3 -S -p 80 --flood <IP-Kali-Gateway>
```

Kỳ vọng: có cảnh báo (thường gán nhãn `Gafgyt_junk` — mô hình nhận diện theo hành vi
gần giống nhất trong dữ liệu huấn luyện, không theo đúng tên công cụ tấn công), độ
trễ suy luận cao hơn hẳn kịch bản 2 vì `hping3 --flood` gửi nhanh hơn ESP32 nhiều lần.

### Kịch bản 4 — Đổi thuật toán khi đang chạy
Khung "Mô Hình Phát Hiện": chọn mô hình khác (ví dụ LSTM), bấm **Áp dụng** — không
cần khởi động lại hệ thống. Lặp lại Kịch bản 2 để thấy cột "Mô Hình" trong bảng cảnh
báo đổi theo đúng mô hình vừa chọn.

---

## 8. Kết thúc demo

```bash
# trên dashboard: bấm "Dừng" nếu ESP32 còn đang flood
# trên terminal Kali Gateway:
Ctrl+C
sudo iptables -L INPUT -n   # phải thấy danh sách trống
```

`Ctrl+C` khiến chương trình tự gỡ mọi luật `iptables` nó đã thêm trong phiên chạy.

---

## 9. Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách sửa |
|---|---|
| `ping 1.1.1.1` không phản hồi dù có IP | Xem lại Mục 1 (Bridged chưa đúng card, hoặc card Killer cần bật VMware Bridge Protocol) |
| `apt update` báo "Temporary failure resolving" | Mạng chưa thông thật sự (Mục 1), không phải lỗi DNS riêng lẻ |
| Dashboard không thấy ESP32 | Sai SSID/mật khẩu/`gateway_ip` trong `.ino`, hoặc hotspot không phải 2,4 GHz |
| `Model ONNX: .../best_dl_int8.onnx` thay vì `.../registry.json` | Thiếu file `deploy_kali/model/registry.json` — chép lại đúng thư mục `deploy_kali/` |
| Nút Kali-Attacker báo lỗi SSH | Xem mục "Kiểm tra sự cố" trong `deploy_kali_attacker/README.md` |
| Port Scan (`nmap -sS`) không ra cảnh báo, hoặc chỉ 1-2 cửa sổ có cảnh báo | Theo đúng cơ chế hiện tại: quét cổng gửi ít gói mỗi giây, thường dưới ngưỡng phát hiện (20 gói/nguồn, 60% tỷ lệ gói bị gắn nhãn tấn công trong 1 cửa sổ) |

---

## 10. Huấn luyện lại từ đầu (tùy chọn)

Không bắt buộc để chạy demo — bộ mô hình trong `deploy_kali/model/` đã đủ dùng.
Muốn huấn luyện lại:

1. Tải N-BaIoT và CIC-IoT-2023 (nguồn trong docstring của từng file ở
   `src/iot_datasets/`), đặt vào `datasets/N-BaIoT/` và `datasets/CIC-IoT-2023/`.
2. Cài `pip install -r requirements.txt` trên máy huấn luyện (CPU là đủ, không bắt
   buộc GPU).
3. Chạy:
   ```bash
   python run_pipeline.py --all
   ```
   Kết quả nằm trong `results/<dataset>/`, bộ mô hình ONNX mới được ghi đè vào
   `deploy_kali/model/`.
