/*
 * =====================================================================
 * KICH BAN ARDUINO CHO ESP32 - THU THAP VA GUI DU LIEU QUA SERIAL (USB)
 * Đã fix nhịp xung cho cảm biến bụi Sharp & Khóa an toàn Relay
 * =====================================================================
 */

#include "DHT.h"

// --- KHAI BÁO CHÂN PHẦN CỨNG ---
#define PIN_GAS_ANALOG   34  // Cảm biến Gas MQ135
#define PIN_DUST_ANALOG  35  // Dây MÀU ĐEN (Đọc tín hiệu bụi)
#define PIN_LED_DUST     5   // Dây MÀU TRẮNG (Công tắc bật/tắt LED của Sharp)
#define RELAY_PIN        26  // Chân điều khiển Relay quạt

#define DHTPIN 4             // Chân DHT11
#define DHTTYPE DHT11

DHT dht(DHTPIN, DHTTYPE);

void setup() {
  Serial.begin(115200);
  delay(1000);
  dht.begin();
  
  pinMode(PIN_GAS_ANALOG, INPUT);
  
  // 1. Cấu hình chân cho Cảm biến bụi Sharp
  pinMode(PIN_DUST_ANALOG, INPUT);
  pinMode(PIN_LED_DUST, OUTPUT);
  digitalWrite(PIN_LED_DUST, HIGH); // Mặc định tắt đèn LED hồng ngoại
  
  // 2. Chốt chặn an toàn cho Relay (Tránh quạt quay tự do)
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);    // Một số mạch Relay kéo HIGH là tắt (nếu quạt tự quay, hãy đổi thành LOW)
}

void loop() {
  // --- ĐỌC NHIỆT ẨM & GAS ---
  float nhietDo = dht.readTemperature();
  float doAm = dht.readHumidity();
  int gasRaw = analogRead(PIN_GAS_ANALOG);

  // Backup dữ liệu ảo nếu DHT11 đọc lỗi
  if (isnan(nhietDo) || isnan(doAm)) {
    nhietDo = 0.0;
    doAm = 0.0;
  }

  // --- CHU TRÌNH VẬT LÝ CỦA CẢM BIẾN BỤI SHARP ---
  digitalWrite(PIN_LED_DUST, LOW);   // 1. Bật đèn LED hồng ngoại soi bụi
  delayMicroseconds(280);            // 2. Chờ đúng 0.28 ms để hạt bụi bay vào vùng phủ sáng
  int dustRaw = analogRead(PIN_DUST_ANALOG); // 3. Chụp ảnh lấy số liệu
  delayMicroseconds(40);             // 4. Chờ thêm 0.04 ms
  digitalWrite(PIN_LED_DUST, HIGH);  // 5. Tắt đèn LED hồng ngoại
  delayMicroseconds(9680);           // 6. Nghỉ chu kỳ trước khi bắn tia tiếp theo
  // ----------------------------------------------

  // --- GỬI DỮ LIỆU LÊN PYTHON ---
  Serial.print(nhietDo, 1);
  Serial.print(",");
  Serial.print(doAm, 1);
  Serial.print(",");
  Serial.print(gasRaw);
  Serial.print(",");
  Serial.println(dustRaw); 

  // Chu kỳ gửi: 30.000 ms (30 giây một lần)
  delay(30000);
}