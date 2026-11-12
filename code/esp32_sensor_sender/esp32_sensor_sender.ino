/*
 * =====================================================================
 * VỊ TRÍ FILE: code/esp32_sensor_sender/esp32_sensor_sender.ino
 * CHỨC NĂNG : THU THẬP CẢM BIẾN & ĐẶT QUẠT DƯỚI QUYỀN ĐIỀU KHIỂN CỦA AI
 * =====================================================================
 * Nguyên lý đảm bảo bật/tắt quạt chính xác tuyệt đối:
 * 1. Đã xóa bỏ lệnh digitalWrite(RELAY_PIN, LOW) gây ép tắt quạt mỗi 15s.
 * 2. Tách 2 luồng thời gian thực bằng millis() (Non-blocking):
 *    - Mỗi 3 giây : Gọi HTTPS lên AI Server 2 lấy lệnh đóng/ngắt Relay (GPIO 26).
 *    - Mỗi 15 giây: Đọc DHT11, MQ135, Sharp GP2Y1010AU0F -> Xuất USB Serial CSV
 *                   (tương thích 100% với lay_data.py) và đẩy lên Server.
 * 3. Hỗ trợ nhận lệnh điều khiển quạt qua cả 3 kênh:
 *    - Kênh 1 (Chính): Trực tiếp từ Cloud AI Server 2 (/api/v1/esp32/command).
 *    - Kênh 2 (Phụ)  : Phản hồi từ máy chủ nội bộ (igf_server.py).
 *    - Kênh 3 (USB)  : Nhận chuỗi "FAN:ON" / "FAN:OFF" qua cáp USB Serial.
 * =====================================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include "DHT.h"

// --- 1. CẤU HÌNH WI-FI & ĐƯỜNG DẪN CLOUD RENDER ---
const char* ssid = "Trung Tin";
const char* password = "15032010";

// Máy chủ Flask nội bộ của bạn cùng nhóm (giữ nguyên để tương thích luồng cũ)
const char* localServerName = "http://172.17.44.111:8080/data";

// [CẬP NHẬT 2 ĐƯỜNG DẪN RENDER CỦA BẠN VÀO ĐÂY]
const char* cloudIngestUrl = "https://server-1-2e5x.onrender.com/";
const char* cloudAiFanUrl  = "https://server-2-tai8.onrender.com/";

// --- 2. KHAI BÁO CHÂN PHẦN CỨNG ---
#define PIN_GAS_ANALOG   34  // Cảm biến khí MQ135
#define PIN_DUST_ANALOG  35  // Dây ĐEN cảm biến bụi Sharp
#define PIN_LED_DUST     5   // Dây TRẮNG điều khiển xung LED hồng ngoại Sharp
#define RELAY_PIN        26  // Chân GPIO 26 đóng/ngắt Relay quạt 5V

#define DHTPIN 4             // Chân cảm biến nhiệt ẩm DHT11
#define DHTTYPE DHT11

DHT dht(DHTPIN, DHTTYPE);

// --- 3. BIẾN QUẢN LÝ NHỊP THỜI GIAN & TRẠNG THÁI QUẠT AI ---
unsigned long lastSensorMillis = 0;
const unsigned long SENSOR_INTERVAL = 15000;   // 15 giây đọc và gửi dữ liệu 1 lần

unsigned long lastFanCheckMillis = 0;
const unsigned long FAN_CHECK_INTERVAL = 3000; // 3 giây kiểm tra lệnh AI 1 lần

bool aiFanState = false;                       // false = TẮT (LOW), true = BẬT (HIGH)
int consecutiveNetworkFailures = 0;            // Đếm lỗi mạng để bảo vệ an toàn

// Hàm duy nhất có quyền thay đổi trạng thái vật lý của chân RELAY_PIN (GPIO 26)
void thucThiLenhQuatAI(bool batQuat) {
  if (aiFanState != batQuat) {
    aiFanState = batQuat;
    digitalWrite(RELAY_PIN, aiFanState ? HIGH : LOW);
  } else {
    // Luôn củng cố mức điện áp chân GPIO 26 đúng với trạng thái AI hiện hành
    digitalWrite(RELAY_PIN, aiFanState ? HIGH : LOW);
  }
}

// Kênh dự phòng: Nhận lệnh từ cổng USB Serial (không làm ảnh hưởng luồng CSV)
void kiemTraLenhQuatQuaCapUSB() {
  if (Serial.available() > 0) {
    String usbCmd = Serial.readStringUntil('\n');
    usbCmd.trim();
    if (usbCmd.indexOf("FAN:ON") >= 0) {
      thucThiLenhQuatAI(true);
    } else if (usbCmd.indexOf("FAN:OFF") >= 0) {
      thucThiLenhQuatAI(false);
    }
  }
}

// Kênh chính: Hỏi lệnh điều khiển trực tiếp từ AI Server 2 trên Render mỗi 3 giây
void dongBoLenhQuatTuAICloud() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  WiFiClientSecure client;
  client.setInsecure(); // Cho phép kết nối HTTPS tới Render không cần nạp CA cert
  HTTPClient https;
  https.setTimeout(2500);

  if (https.begin(client, cloudAiFanUrl)) {
    int httpCode = https.GET();
    if (httpCode == 200) {
      consecutiveNetworkFailures = 0;
      String payload = https.getString();

      // Phân tích chính xác trường "fan_command" hoặc "relay_gpio_level" từ Server 2
      if (payload.indexOf("\"fan_command\":\"ON\"") >= 0 ||
          payload.indexOf("\"fan_command\": \"ON\"") >= 0 ||
          payload.indexOf("\"relay_gpio_level\":1") >= 0 ||
          payload.indexOf("\"relay_gpio_level\": 1") >= 0) {
        thucThiLenhQuatAI(true);
      } else if (payload.indexOf("\"fan_command\":\"OFF\"") >= 0 ||
                 payload.indexOf("\"fan_command\": \"OFF\"") >= 0 ||
                 payload.indexOf("\"relay_gpio_level\":0") >= 0 ||
                 payload.indexOf("\"relay_gpio_level\": 0") >= 0) {
        thucThiLenhQuatAI(false);
      }
    } else {
      consecutiveNetworkFailures++;
    }
    https.end();
  }
}

void docVaGuiDuLieuCamBien() {
  // 1. Đọc nhiệt độ, độ ẩm và khí gas
  float nhietDo = dht.readTemperature();
  float doAm = dht.readHumidity();
  int gasRaw = analogRead(PIN_GAS_ANALOG);

  // Kiểm tra trường hợp DHT11 đọc lỗi NaN thì gán giá trị an toàn mặc định
  if (isnan(nhietDo)) nhietDo = 28.0;
  if (isnan(doAm)) doAm = 60.0;

  // 2. Chu trình xung chuẩn xác 10ms của cảm biến bụi Sharp GP2Y1010AU0F
  digitalWrite(PIN_LED_DUST, LOW);
  delayMicroseconds(280);
  int dustRaw = analogRead(PIN_DUST_ANALOG);
  delayMicroseconds(40);
  digitalWrite(PIN_LED_DUST, HIGH);
  delayMicroseconds(9680);

  // 3. GIỮ NGUYÊN TRẠNG THÁI QUẠT CỦA AI (Tuyệt đối không ghi đè LOW như bản cũ)
  digitalWrite(RELAY_PIN, aiFanState ? HIGH : LOW);

  // 4. Xuất đúng 4 cột CSV qua cổng USB Serial cho script lay_data.py
  Serial.print(nhietDo, 1);
  Serial.print(",");
  Serial.print(doAm, 1);
  Serial.print(",");
  Serial.print(gasRaw);
  Serial.print(",");
  Serial.println(dustRaw);

  // 5. Đẩy dữ liệu qua Wi-Fi
  if (WiFi.status() == WL_CONNECTED) {
    String formData = "temp=" + String(nhietDo, 1) +
                      "&hum=" + String(doAm, 1) +
                      "&gas=" + String(gasRaw) +
                      "&dust=" + String(dustRaw);

    // 5a. Gửi về máy cục bộ (igf_server.py) nếu đang mở
    HTTPClient httpLocal;
    httpLocal.setTimeout(1200);
    if (httpLocal.begin(localServerName)) {
      httpLocal.addHeader("Content-Type", "application/x-www-form-urlencoded");
      int localCode = httpLocal.POST(formData);
      if (localCode > 0) {
        String localResp = httpLocal.getString();
        if (localResp.indexOf("FAN:ON") >= 0) thucThiLenhQuatAI(true);
        else if (localResp.indexOf("FAN:OFF") >= 0) thucThiLenhQuatAI(false);
      }
      httpLocal.end();
    }

    // 5b. Đẩy thẳng lên Render Server 1 để làm sạch và kích hoạt AI tức thì
    WiFiClientSecure client;
    client.setInsecure();
    HTTPClient httpsCloud;
    httpsCloud.setTimeout(2500);
    if (httpsCloud.begin(client, cloudIngestUrl)) {
      httpsCloud.addHeader("Content-Type", "application/x-www-form-urlencoded");
      httpsCloud.POST(formData);
      httpsCloud.end();
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  dht.begin();

  pinMode(PIN_GAS_ANALOG, INPUT);
  pinMode(PIN_DUST_ANALOG, INPUT);
  pinMode(PIN_LED_DUST, OUTPUT);
  digitalWrite(PIN_LED_DUST, HIGH);

  pinMode(RELAY_PIN, OUTPUT);
  thucThiLenhQuatAI(false); // Khởi động ở trạng thái tắt an toàn

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 10) {
    delay(500);
    retry++;
  }

  // Lấy lệnh quạt từ AI ngay khi khởi động xong và gửi mẫu dữ liệu đầu tiên
  dongBoLenhQuatTuAICloud();
  docVaGuiDuLieuCamBien();

  lastSensorMillis = millis();
  lastFanCheckMillis = millis();
}

void loop() {
  unsigned long currentMillis = millis();

  // 1. Kiểm tra lệnh điều khiển qua cáp USB ở mọi vòng lặp
  kiemTraLenhQuatQuaCapUSB();

  // 2. Mỗi 3 giây: Tự động kết nối Wi-Fi lại nếu rớt mạng và hỏi lệnh quạt từ AI
  if (currentMillis - lastFanCheckMillis >= FAN_CHECK_INTERVAL) {
    lastFanCheckMillis = currentMillis;
    if (WiFi.status() != WL_CONNECTED) {
      WiFi.reconnect();
    } else {
      dongBoLenhQuatTuAICloud();
    }
  }

  // 3. Mỗi 15 giây: Đọc cảm biến và đẩy dữ liệu lên Server
  if (currentMillis - lastSensorMillis >= SENSOR_INTERVAL) {
    lastSensorMillis = currentMillis;
    docVaGuiDuLieuCamBien();
  }
}