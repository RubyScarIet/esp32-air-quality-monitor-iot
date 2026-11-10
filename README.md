# 🌿 ESP32 Air Quality & Environmental Monitoring IoT System

> **Hệ thống giám sát chất lượng không khí thời gian thực (Real-time IoT Environmental Monitor) sử dụng ESP32 kết hợp Python GUI Dashboard và tự động thu thập Dataset CSV.**

---

## 📌 1. Giới thiệu dự án

Dự án này là giải pháp trạm đo chất lượng không khí toàn diện:
- **Phần cứng (ESP32):** Thu thập dữ liệu từ cảm biến Nhiệt độ - Độ ẩm (**DHT11**), cảm biến Khí độc / Gas (**MQ-135**), cảm biến Bụi mịn PM2.5/PM10 (**Sharp GP2Y1010AU0F**) và chân điều khiển Quạt thông gió (**Relay**).
- **Phần mềm (Python GUI Dashboard):** Giao diện máy tính thời gian thực trực quan, tự động lọc sạch các byte rác khởi động của ESP32, tự động kích hoạt mạch Auto-Reset mà không cần bấm nút vật lý, và lưu dữ liệu nối tiếp vào file `.csv` chuẩn hoá theo ngày - giờ để phục vụ huấn luyện mô hình Machine Learning / AI.

---

## 📁 2. Cấu trúc thư mục

```text
esp32-air-quality-monitor-iot/
├── .gitignore
├── requirements.txt            # Thư viện Python cần thiết (pyserial)
├── chay_thu_thap.bat           # File nhấp đúp 1-click để khởi động trên Windows
├── kich_hoat_venv.bat          # File mở nhanh Terminal với venv kích hoạt
├── README.md                   # Hướng dẫn chi tiết dự án
├── code/
│   ├── lay_data.py             # Ứng dụng Python GUI Dashboard & Thu thập dữ liệu
│   └── esp32_sensor_sender/
│       └── esp32_sensor_sender.ino  # Mã nguồn nạp cho vi điều khiển ESP32
└── data/                       # Thư mục chứa các file Dataset CSV thu thập được
    ├── data_2026-10-19_lan_01_(02h32-06h42).csv
    └── ...
```

---

## 🔌 3. Sơ đồ đấu nối phần cứng (Pinout)

| Linh kiện / Chân | Chân ESP32 (GPIO) | Ghi chú |
| :--- | :---: | :--- |
| **DHT11 (Data)** | `GPIO 4` | Cảm biến Nhiệt độ & Độ ẩm (Pull-up 10k lên 3.3V nếu cần) |
| **MQ-135 (AOUT)** | `GPIO 34` | Đọc tín hiệu Analog khí Gas / Khói |
| **Cảm biến Bụi Sharp (Vo)** | `GPIO 35` | Dây **MÀU ĐEN** - Chân Analog đọc nồng độ bụi |
| **Cảm biến Bụi Sharp (LED)** | `GPIO 5` | Dây **MÀU TRẮNG** - Điều khiển phát xung LED hồng ngoại |
| **Relay Quạt** | `GPIO 26` | Điều khiển đóng/ngắt quạt thông gió (Khóa an toàn HIGH) |
| **Nguồn cấp (VCC/GND)** | `5V / 3.3V / GND` | Cấp nguồn phù hợp từng module |

---

## 🛠️ 4. Hướng dẫn nạp code ESP32 (Arduino IDE)

### Bước 1: Chuẩn bị môi trường Arduino IDE
1. Mở **Arduino IDE** (phiên bản 1.8.x hoặc 2.x).
2. Cài đặt gói vi điều khiển **ESP32**:
   * Vào `File` $\rightarrow$ `Preferences` $\rightarrow$ thêm URL vào *Additional Board Manager URLs*:
     ```text
     https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
     ```
   * Vào `Tools` $\rightarrow$ `Board` $\rightarrow$ `Boards Manager...` $\rightarrow$ Tìm `esp32` của Espressif và cài đặt.
3. Cài đặt thư viện cảm biến qua `Sketch` $\rightarrow$ `Include Library` $\rightarrow$ `Manage Libraries...`:
   * Tìm và cài đặt: **DHT sensor library** (bởi Adafruit).

### Bước 2: Nạp chương trình
1. Mở file [code/esp32_sensor_sender/esp32_sensor_sender.ino](code/esp32_sensor_sender/esp32_sensor_sender.ino).
2. Cắm cáp USB nối ESP32 với máy tính.
3. Cấu hình trên menu `Tools`:
   * **Board:** `ESP32 Arduino` $\rightarrow$ `ESP32 Dev Module`
   * **Port:** Chọn cổng COM tương ứng của ESP32 (ví dụ: `COM5`).
4. Nhấn nút **Upload** (Mũi tên sang phải $\rightarrow$ hoặc phím tắt `Ctrl + U`).
5. ⚠️ **BƯỚC BẮT BUỘC:** Sau khi nạp xong, hãy **ĐÓNG CỬA SỔ SERIAL MONITOR** trên Arduino IDE để tránh xung đột cổng COM với Python.

---

## 💻 5. Hướng dẫn chạy Dashboard Python

### Cách 1: Chạy nhanh bằng 1-Click (Khuyên dùng)
* Vào thư mục gốc của dự án, nhấp đúp chuột vào file:
  ```text
  chay_thu_thap.bat
  ```
* Ứng dụng GUI sẽ tự động khởi động, kích hoạt cổng Serial và hiển thị dữ liệu ngay lập tức.

### Cách 2: Chạy qua dòng lệnh (Terminal / PowerShell)

1. Mở terminal tại thư mục dự án và tạo môi trường ảo (nếu chưa có):
   ```powershell
   python -m venv code\venv
   ```
2. Cài đặt thư viện cần thiết:
   ```powershell
   .\code\venv\Scripts\pip install -r requirements.txt
   ```
3. Khởi động chương trình:
   ```powershell
   .\code\venv\Scripts\python code\lay_data.py
   ```

---

## 📊 6. Cơ chế thu thập & Chuẩn hoá dữ liệu (Dataset Format)

- **Lọc sạch dữ liệu rác:** Tự động nhận diện và loại bỏ bootloader logs của ESP32 (`rst:0x1`, `load:0x...`).
- **Tự động đánh số và đặt tên:**
  - Trong lúc đang chạy: `data_YYYY-MM-DD_lan_01_(dang_chay).csv`
  - Khi nhấn **"DỪNG & HOÀN TẤT LƯU FILE"** (hoặc `Ctrl + C`): Tự động đổi tên gắn mốc thời gian thực hiện:
    `data_YYYY-MM-DD_lan_01_(20h51-20h54).csv`
- **Cấu trúc cột dữ liệu trong file CSV:**
  ```csv
  Thoi_Gian_Thuc,Nhiet_Do_C,Do_Am_%,Gas_Raw,Dust_Raw
  2026-10-23 20:51:30,28.1,22.0,159,32
  2026-10-23 20:52:00,28.0,22.0,160,33
  ```

---

## 👤 Tác giả

* **GitHub:** [@RubyScarIet](https://github.com/RubyScarIet)
* **Kho lưu trữ:** [esp32-air-quality-monitor-iot](https://github.com/RubyScarIet/esp32-air-quality-monitor-iot)
