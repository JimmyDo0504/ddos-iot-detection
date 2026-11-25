/*
 * esp32_iot_node.ino
 * Firmware cho ESP32 đóng vai thiết bị IoT thật trong bài lab phát hiện DDoS-IoT.
 *
 * Hai chế độ hoạt động:
 *   - Bình thường : gửi telemetry cảm biến (UDP tới cổng 1883 của Kali) mỗi 2 giây -> Gateway nhận "Benign".
 *   - Bị chiếm quyền: bơm UDP flood (cổng 80) hoặc TCP SYN-like flood -> Gateway nhận tấn công & chặn.
 *
 * Kích hoạt tấn công bằng MỘT trong hai cách:
 *   1) Gõ 'A' (UDP) / 'S' (SYN) / 'N' (dừng) trong Serial Monitor.
 *   2) Điều khiển từ xa qua Web Dashboard: dashboard gửi gói UDP
 *        "IOTLAB <token> <UDP|SYN|STOP|STATUS> <seconds>"
 *      tới cổng CONTROL_PORT của ESP32; tấn công tự dừng sau <seconds> giây.
 *   Nhờ cách 2, khi demo không cần chạm vào Serial Monitor.
 */

#include <WiFi.h>
#include <WiFiUdp.h>

// 1. Cấu hình Wi-Fi: ESP32 và laptop (Kali VM ở chế độ Bridged) cùng kết nối vào mạng này.
//    Lưu ý: ESP32 chỉ hỗ trợ Wi-Fi 2.4 GHz.
const char* ssid     = "Sentosa";           // Tên Wi-Fi lab (hotspot điện thoại, băng tần 2.4 GHz)
const char* password = "khongchoigame";       // Mật khẩu Wi-Fi

// 2. Địa chỉ IP của máy ảo Kali (xem bằng: ip -4 addr show eth0).
//    IP do DHCP cấp nên có thể đổi -> kiểm tra lại trước mỗi buổi demo.
const char* gateway_ip = "192.168.100.74";   // IP Kali trên hotspot "yuki"
const int mqtt_port    = 1883;             // cổng telemetry (giống MQTT)
const int attack_port  = 80;               // cổng bị flood khi tấn công

// 3. Điều khiển từ xa từ Web Dashboard (phải khớp ESP32_CTRL_PORT / ESP32_TOKEN của dashboard).
const int   CONTROL_PORT = 4210;
const char* CONTROL_TOKEN = "iotlab2026";

WiFiUDP udp;         // gửi telemetry và flood
WiFiUDP ctrl;        // nhận lệnh điều khiển từ dashboard

unsigned long lastSendTime = 0;
int  attackMode = 0;                 // 0 = bình thường, 1 = UDP flood, 2 = SYN flood
unsigned long attackUntil = 0;       // mốc thời gian (millis) tự dừng tấn công
int  packetSeq = 0;
char ctrlBuf[128];

void printStatus() {
  Serial.print("[STATUS] che do = ");
  Serial.print(attackMode == 0 ? "BINH THUONG" : attackMode == 1 ? "UDP FLOOD" : "SYN FLOOD");
  if (attackMode != 0) {
    Serial.print(" (con ");
    Serial.print((attackUntil - millis()) / 1000);
    Serial.print("s)");
  }
  Serial.println();
}

void startAttack(int mode, int seconds) {
  attackMode = mode;
  attackUntil = millis() + (unsigned long)seconds * 1000UL;
  Serial.print("\n[!] >>> KICH HOAT ");
  Serial.print(mode == 1 ? "UDP FLOOD" : "TCP SYN FLOOD");
  Serial.print(" trong ");
  Serial.print(seconds);
  Serial.println("s <<<");
}

void stopAttack() {
  if (attackMode != 0) Serial.println("\n[V] ESP32 tro lai che do Binh thuong (Normal IoT).");
  attackMode = 0;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n==================================================");
  Serial.println("[ESP32] Khoi dong Node IoT thuc nghiem phong Lab...");

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("[ESP32] Dang ket noi vao Wi-Fi: ");
  Serial.println(ssid);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n[V] Ket noi thanh cong vao Kali Gateway!");
  Serial.print("[ESP32] IP cua thiet bi ESP32: ");
  Serial.println(WiFi.localIP());
  Serial.print("[ESP32] IP cua Kali (dich):    ");
  Serial.println(gateway_ip);
  Serial.print("[ESP32] IP cua router Wi-Fi:   ");
  Serial.println(WiFi.gatewayIP());
  ctrl.begin(CONTROL_PORT);
  Serial.print("[ESP32] Lang nghe lenh dieu khien tu Dashboard tai cong UDP ");
  Serial.println(CONTROL_PORT);
  Serial.println("==================================================");
  Serial.println("[DIEU KHIEN] Serial: A=UDP flood, S=SYN flood, N=dung. Hoac dieu khien tu Web Dashboard.");
}

// Nhận lệnh "IOTLAB <token> <CMD> <seconds>" từ dashboard và trả lời.
void handleControl() {
  int n = ctrl.parsePacket();
  if (n <= 0) return;
  int len = ctrl.read(ctrlBuf, sizeof(ctrlBuf) - 1);
  if (len <= 0) return;
  ctrlBuf[len] = '\0';

  char tag[16] = "", token[32] = "", cmd[16] = "";
  int seconds = 15;
  sscanf(ctrlBuf, "%15s %31s %15s %d", tag, token, cmd, &seconds);
  if (strcmp(tag, "IOTLAB") != 0 || strcmp(token, CONTROL_TOKEN) != 0) {
    ctrl.beginPacket(ctrl.remoteIP(), ctrl.remotePort());
    ctrl.print("ERR token");
    ctrl.endPacket();
    return;
  }
  if (seconds < 1) seconds = 1;
  if (seconds > 120) seconds = 120;

  char reply[64];
  if (strcmp(cmd, "UDP") == 0)       { startAttack(1, seconds); snprintf(reply, sizeof(reply), "OK UDP %ds", seconds); }
  else if (strcmp(cmd, "SYN") == 0)  { startAttack(2, seconds); snprintf(reply, sizeof(reply), "OK SYN %ds", seconds); }
  else if (strcmp(cmd, "STOP") == 0) { stopAttack();            snprintf(reply, sizeof(reply), "OK STOP"); }
  else                               { snprintf(reply, sizeof(reply), "OK STATUS mode=%d", attackMode); }

  ctrl.beginPacket(ctrl.remoteIP(), ctrl.remotePort());
  ctrl.print(reply);
  ctrl.endPacket();
}

void handleSerial() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == 'A' || c == 'a') startAttack(1, 15);
  else if (c == 'S' || c == 's') startAttack(2, 15);
  else if (c == 'N' || c == 'n') stopAttack();
  else if (c == '?') printStatus();
}

void sendTelemetry() {
  packetSeq++;
  float temperature = 26.5 + (random(-20, 20) / 10.0);
  float humidity    = 60.0 + (random(-40, 40) / 10.0);
  char payload[160];
  snprintf(payload, sizeof(payload),
    "{\"dev_id\":\"ESP32_DHT11\",\"temp\":%.1f,\"hum\":%.1f,\"seq\":%d,\"status\":\"HEALTHY\"}",
    temperature, humidity, packetSeq);
  udp.beginPacket(gateway_ip, mqtt_port);
  udp.write((const uint8_t*)payload, strlen(payload));
  udp.endPacket();
  Serial.print("[NORMAL] Gui telemetry #");
  Serial.print(packetSeq);
  Serial.print(" (");
  Serial.print(strlen(payload));
  Serial.println(" bytes) toi Gateway.");
}

void floodUDP() {
  static uint8_t buf[512];
  memset(buf, 0x58, sizeof(buf));
  udp.beginPacket(gateway_ip, attack_port);
  udp.write(buf, sizeof(buf));
  udp.endPacket();
  delay(2);                         // ~500 goi/giay
}

// "SYN-like" flood: nhiều gói UDP nhỏ, mỗi gói một cổng đích khác nhau -> giống SYN flood quét cổng.
void floodSYN() {
  static uint8_t buf[2];
  static uint16_t port = 1024;
  buf[0] = 0x02; buf[1] = 0x00;
  port = 1024 + (port - 1023) % 64000;
  udp.beginPacket(gateway_ip, port++);
  udp.write(buf, sizeof(buf));
  udp.endPacket();
  delayMicroseconds(1000);          // ~1000 goi/giay
}

void loop() {
  handleControl();
  handleSerial();

  if (attackMode != 0 && millis() > attackUntil) stopAttack();

  if (attackMode == 0) {
    if (millis() - lastSendTime >= 2000) {
      lastSendTime = millis();
      sendTelemetry();
    }
  } else if (attackMode == 1) {
    floodUDP();
  } else {
    floodSYN();
  }
}
