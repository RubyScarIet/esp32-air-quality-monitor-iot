# HƯỚNG DẪN CHẠY CODE THU THẬP DỮ LIỆU IOT

Vui lòng xem tài liệu hướng dẫn đầy đủ tại [README.md chính của dự án](file:///c:/Users/tinca/OneDrive/Desktop/iot/README.md).

### Tóm tắt các thao tác nhanh:
1. **Nạp code cho ESP32:** Mở [esp32_sensor_sender.ino](file:///c:/Users/tinca/OneDrive/Desktop/iot/code/esp32_sensor_sender.ino) bằng Arduino IDE -> Chọn cổng COM -> Nhấn **Upload**.
2. **ĐÓNG SERIAL MONITOR** trên Arduino IDE.
3. **Chạy thu thập dữ liệu:**
   - Cách 1: Chạy file [chay_thu_thap.bat](file:///c:/Users/tinca/OneDrive/Desktop/iot/chay_thu_thap.bat) ở thư mục gốc `iot/`.
   - Cách 2: Chạy lệnh `..\venv\Scripts\python lay_data.py`.
4. **Dừng thu thập:** Bấm `Ctrl + C` trên bàn phím. File data sẽ tự động được lưu và đổi tên với đầy đủ ngày tháng, số thứ tự lần đo và khoảng thời gian chạy tại thư mục [iot/data](file:///c:/Users/tinca/OneDrive/Desktop/iot/data).
