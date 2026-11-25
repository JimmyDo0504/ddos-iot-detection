# HƯỚNG DẪN KẾT NỐI ESP32 VÀO MẠNG LAB KALI GATEWAY

Nếu bạn có sẵn mạch **ESP32** (hoặc ESP8266), bạn có thể dùng nó làm **thiết bị IoT vật lý thực tế** trong đồ án để tăng tính thuyết phục và điểm số thực nghiệm.

---

## 1. MÔ HÌNH KẾT NỐI KHI CÓ ESP32 THẬT

Cấu hình đang dùng: **Kali chạy trong VMware (Bridged qua card Wi-Fi của laptop)**, ESP32 và laptop cùng kết nối Wi-Fi `Sentosa`. ESP32 gửi gói tin **thẳng tới IP của Kali**, nên Kali bắt được toàn bộ lưu lượng của ESP32 trên `eth0`.

```
 [ ESP32 ] --Wi-Fi--> [ Router/Hotspot "Sentosa" 192.168.100.x ] <--Wi-Fi-- [ Laptop Windows ]
 telemetry 2s/lần                                                            └─ VMware Bridged
 (gõ 'A' -> UDP flood)                                                          └─ Kali VM 192.168.100.74 (eth0)
                                                                                   AfterImage + ONNX + dashboard
```

Lưu ý:
- **ESP32 chỉ hỗ trợ Wi-Fi 2.4 GHz.** Nếu router phát cả 2.4 GHz và 5 GHz chung tên thì vẫn được; nếu chỉ phát 5 GHz thì ESP32 không kết nối được.
- **Không dùng NAT** cho máy ảo Kali (NAT làm mất IP nguồn thật của ESP32 → không phân biệt/chặn được thiết bị).
- IP của Kali do DHCP cấp, có thể đổi giữa các lần khởi động → kiểm tra bằng `ip -4 addr show eth0` và sửa `gateway_ip` trong `.ino` nếu khác.
- Chỉ demo trong mạng lab bạn kiểm soát, không dùng Wi-Fi trường/mạng công cộng.

---

## 2. CÁC BƯỚC NẠP CODE CHO ESP32

1. Cài đặt **Arduino IDE** (hoặc VS Code với PlatformIO).
2. Thêm bo mạch ESP32 trong Arduino IDE:
   - Vào `File` -> `Preferences` -> dán URL: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Vào `Tools` -> `Board Manager` -> tìm và cài đặt `esp32 by Espressif`.
3. Mở tệp `esp32_iot_node.ino`:
   - Đã cấu hình sẵn: `ssid = "yuki"`, `gateway_ip = "10.55.62.242"` (sửa lại nếu đổi mạng hoặc IP Kali đổi).
4. Cắm cáp Micro-USB nối ESP32 với máy tính, chọn đúng Cổng COM và bấm **Upload**.
5. Mở Serial Monitor (tốc độ 115200 baud) để xem log gửi gói tin:
   ```
   [V] Ket noi thanh cong vao Kali Gateway!
   [ESP32] IP cua thiet bi ESP32: 192.168.100.xx
   [NORMAL] Gui telemetry #1 (8x bytes) toi Gateway.
   ```
6. Trên Kali kiểm tra gói tin đã tới:
   ```bash
   sudo tcpdump -i eth0 -n udp port 1883
   ```
7. Chạy giám sát (card mạng là `eth0` vì Kali chạy trong VMware):
   ```bash
   sudo ./start_monitor.sh eth0 5000 --block
   ```
   Dashboard: `http://10.55.62.242:5000`.

---

## 3. KÍCH HOẠT TẤN CÔNG KHI DEMO

Có hai cách, chọn một:

- **Từ Web Dashboard (khuyên dùng khi demo):** trong khung "Điều Khiển ESP32", chọn IP thiết bị rồi bấm **UDP Flood** hoặc **TCP SYN Flood**. Dashboard gửi lệnh UDP tới ESP32 (cổng `4210`), tấn công tự dừng sau số giây đã đặt. Không cần chạm vào Serial Monitor.
- **Từ Serial Monitor:** gõ `A` = UDP flood, `S` = SYN flood, `N` = dừng, `?` = xem trạng thái. Mỗi đợt tấn công tự dừng sau 15 giây.

Khi flood, ESP32 bơm lưu lượng tới cổng 80 của Kali; Gateway phát hiện, cảnh báo và tự chặn IP của ESP32 bằng iptables.

> Token điều khiển mặc định là `iotlab2026` (hằng `CONTROL_TOKEN` trong `.ino`, phải khớp biến môi trường `ESP32_TOKEN` của dashboard). Đây chỉ là cơ chế đơn giản cho mạng lab, không phải bảo mật thật.
