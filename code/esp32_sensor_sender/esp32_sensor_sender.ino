/*
 * =====================================================================
 * KỊCH BẢN ESP32 KẾT HỢP (HYBRID: WI-FI HTTP POST + USB SERIAL CSV)
 * =====================================================================
 * Tính năng kết hợp cả 2 phiên bản:
 * 1. LUÔN LUÔN xuất dòng dữ liệu chuẩn CSV (Nhiet_Do,Do_Am,Gas_Raw,Dust_Raw)
 *    qua cổng cáp USB Serial ở mọi chu kỳ (như bản cũ) -> Cắm dây là chạy ngay!
 * 2. ĐỒNG THỜI nếu có kết nối Wi-Fi, gửi thêm gói tin HTTP POST lên máy chủ
 *    Flask Python (như bản mới).
 * 3. Chống treo tuyệt đối: Nếu mang đi nơi khác không có Wi-Fi cũ, sau 5 giây
 *    mạch tự bỏ qua Wi-Fi và chạy mượt mà qua cáp USB.
 * 4. Đầy đủ chu trình xung cảm biến bụi Sharp (280us - 40us - 9680us) & khóa Relay.
 * =====================================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include "DHT.h"

// --- CẤU HÌNH MẠNG WI-FI & SERVER ---
const char* ssid = "Trung Tin";                             // Tên Wi-Fi
const char* password = "15032010";                          // Mật khẩu Wi-Fi
const char* serverName = "http://172.17.44.111:8080/data";  // IP máy chủ Python

// --- KHAI BÁO CHÂN PHẦN CỨNG ---
#define PIN_GAS_ANALOG   34  // Cảm biến Gas MQ135
#define PIN_DUST_ANALOG  35  // Dây MÀU ĐEN (Đọc tín hiệu bụi Sharp)
#define PIN_LED_DUST     5   // Dây MÀU TRẮNG (Công tắc bật/tắt LED hồng ngoại Sharp)
#define RELAY_PIN        26  // Chân điều khiển Relay quạt

#define DHTPIN 4             // Chân DHT11
#define DHTTYPE DHT11

DHT dht(DHTPIN, DHTTYPE);

unsigned long previousMillis = 0;
const long interval = 15000; // Chu kỳ đọc và gửi: 15 giây (15000 ms). Có thể chỉnh thành 2000 hoặc 30000 tùy ý

void docVaGuiDuLieu() {
  // 1. ĐỌC NHIỆT ĐỘ, ĐỘ ẨM & KHÍ GAS
  float nhietDo = dht.readTemperature();
  float doAm = dht.readHumidity();
  int gasRaw = analogRead(PIN_GAS_ANALOG);

  // 2. CHU TRÌNH VẬT LÝ CỦA CẢM BIẾN BỤI SHARP (BẢN CŨ CHUẨN XÁC)
  digitalWrite(PIN_LED_DUST, LOW);            // Bật đèn LED hồng ngoại soi bụi
  delayMicroseconds(280);                     // Chờ đúng 0.28 ms để hạt bụi vào vùng phủ sáng
  int dustRaw = analogRead(PIN_DUST_ANALOG);  // Đọc giá trị Analog thô (0 - 4095)
  delayMicroseconds(40);                      // Chờ thêm 0.04 ms
  digitalWrite(PIN_LED_DUST, HIGH);           // Tắt đèn LED hồng ngoại
  delayMicroseconds(9680);                    // Nghỉ hết chu kỳ 10ms của cảm biến Sharp

  // 3. KHÓA AN TOÀN RELAY
  digitalWrite(RELAY_PIN, LOW);

  // 4. [BẢN CŨ] LUÔN LUÔN GỬI CHUỖI CSV QUA CÁP USB SERIAL (NhietDo,DoAm,Gas,Dust)
  // Giúp Python đọc được ngay lập tức qua cổng COM bất kể Wi-Fi đúng hay sai
  Serial.print(nhietDo, 1);
  Serial.print(",");
  Serial.print(doAm, 1);
  Serial.print(",");
  Serial.print(gasRaw);
  Serial.print(",");
  Serial.println(dustRaw);

  // 5. [BẢN MỚI] ĐỒNG THỜI GỬI QUA WI-FI HTTP POST (NẾU ĐANG CÓ WI-FI)
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.setTimeout(2000); // Timeout 2s để không làm chậm vòng lặp nếu khác IP Server
    http.begin(serverName);
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");

    String httpRequestData = "temp=" + String(nhietDo, 1) +
                             "&hum=" + String(doAm, 1) +
                             "&gas=" + String(gasRaw) +
                             "&dust=" + String(dustRaw);

    int httpResponseCode = http.POST(httpRequestData);
    Serial.print("HTTP Code: ");
    Serial.println(httpResponseCode);
    http.end();
  } else {
    Serial.println("--- [CẢNH BÁO: MẤT WI-FI] ĐẨY DỮ LIỆU QUA CÁP USB ---");
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  dht.begin();

  pinMode(PIN_GAS_ANALOG, INPUT);
  pinMode(PIN_DUST_ANALOG, INPUT);
  pinMode(PIN_LED_DUST, OUTPUT);
  digitalWrite(PIN_LED_DUST, HIGH); // Mặc định tắt đèn LED hồng ngoại

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);     // Khóa tắt quạt

  // Thử kết nối Wi-Fi tối đa 5 giây (10 lần x 500ms). Không treo nếu mang đi nơi khác!
  Serial.print("Đang kết nối Wi-Fi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 10) {
    delay(500);
    Serial.print(".");
    retry++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[OK] Đã kết nối Wi-Fi!");
    Serial.print("Địa chỉ IP của bo mạch: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[USB MODE] Không có Wi-Fi, chạy qua cáp USB!");
  }

  // Gửi ngay mẫu đầu tiên sau khi khởi động xong mà không bắt người dùng chờ 15s đầu
  docVaGuiDuLieu();
  previousMillis = millis();
}

void loop() {
  unsigned long currentMillis = millis();

  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    docVaGuiDuLieu();
  }
}
