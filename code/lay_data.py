"""
=============================================================================
MÁY CHỦ THU THẬP DỮ LIỆU IOT LOCAL SERVER (FLASK + SERIAL + TKINTER GUI)
=============================================================================
Tính năng nổi bật:
1. Hiển thị trực quan chế độ kết nối thời gian thực: Đang nhận qua [WI-FI HTTP] hay [USB SERIAL (COM)].
2. Tự động dò tìm chính xác Địa chỉ IPv4 LAN/Wi-Fi của máy tính và hiển thị đầy đủ:
   - Đường dẫn xem Web Dashboard / JSON trên trình duyệt (có nút bấm mở nhanh & copy).
   - Đường dẫn Endpoint Server chính xác để nạp vào code ESP32 (`http://<IP>:8080/data`).
3. Hỗ trợ song song cả 2 luồng:
   - Luồng mạng Flask (Port 8080) nhận gói tin HTTP POST qua Wi-Fi.
   - Luồng đọc cổng USB Serial (COM) dự phòng khi ESP32 mất Wi-Fi hoặc cắm cáp trực tiếp.
4. Hiển thị thẳng giá trị thô (Raw) và tự động lưu file CSV chuẩn hóa theo ngày & giờ chạy.
=============================================================================
"""

import os
import re
import json
import time
import socket
import logging
import threading
import webbrowser
import urllib.request
import urllib.error
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from flask import Flask, request, jsonify, render_template_string
import serial
import serial.tools.list_ports

# Tắt log mặc định của Werkzeug để console gọn gàng
log_werkzeug = logging.getLogger('werkzeug')
log_werkzeug.setLevel(logging.ERROR)

# =============================================================================
# CẤU HÌNH HỆ THỐNG, LƯU TRỮ CSV & SUPABASE CLOUD
# =============================================================================
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080
BAUD_RATE = 115200

SUPABASE_URL = "https://cyjzrllfgiggtolweyfp.supabase.co"
SUPABASE_KEY = "sb_publishable_xDLmWmu4Ksi8J9F4wNRrcQ_n3T_JDdJ"
SUPABASE_TABLE = "sensor_data"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'data'))
HEADER_CSV = "Thoi_Gian_Thuc,Nhiet_Do_C,Do_Am_%,Gas_Raw,Dust_Raw\n"


def day_mau_len_supabase_async(thoi_gian, temp_val, hum_val, gas_val, dust_val, nguon_nhan, callback_log=None):
    """
    Đẩy 1 mẫu dữ liệu vừa nhận được lên bảng `sensor_data` của Supabase trong luồng ngầm (Non-blocking).
    """
    def _worker():
        try:
            payload = {
                "thoi_gian_thuc": str(thoi_gian),
                "nhiet_do": float(temp_val),
                "do_am": float(hum_val),
                "gas_raw": int(float(gas_val)),
                "dust_raw": int(float(dust_val)),
                "nguon_nhan": str(nguon_nhan)
            }
            endpoint = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            }
            data_bytes = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=8):
                pass
        except Exception as e:
            if callback_log:
                callback_log(f"   [CẢNH BÁO SUPABASE] Không thể đẩy mẫu lên Cloud: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def lay_tat_ca_ip_may_tinh():
    """
    Tự động dò tìm chính xác địa chỉ IPv4 Wi-Fi / LAN thực tế của máy tính.
    Trả về (ip_chinh, danh_sach_ip).
    """
    ip_list = []
    primary_ip = "127.0.0.1"

    # Cách 1: Thử kết nối UDP giả lập tới DNS Google để lấy IP card mạng chính đang ra Internet/Wi-Fi
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and primary_ip not in ip_list:
            ip_list.append(primary_ip)
    except Exception:
        pass

    # Cách 2: Quét tất cả interface của máy tính
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in ip_list:
                ip_list.append(ip)
    except Exception:
        pass

    # Ưu tiên dải mạng LAN/Wi-Fi phổ biến (192.168.x.x hoặc 172.20.x.x hoặc 10.x.x.x)
    for ip in ip_list:
        if ip.startswith("192.168.") or ip.startswith("172.20."):
            primary_ip = ip
            break

    if not ip_list:
        ip_list = ["127.0.0.1"]

    return primary_ip, ip_list


# =============================================================================
# BỘ NHỚ ĐỆM DICTIONARY DÙNG CHUNG GIỮA CÁC LUỒNG (SHARED DICTIONARY BUFFER)
# =============================================================================
buffer_lock = threading.Lock()
shared_sensor_buffer = {
    "temp": "--",
    "hum": "--",
    "gas": "--",
    "dust": "--",
    "timestamp": "--",
    "source_type": "WAITING",   # 'WIFI', 'USB', hoặc 'WAITING'
    "source_detail": "Đang chờ kết nối...",  # IP của ESP32 hoặc tên cổng COM (vd: COM5)
    "packet_id": 0
}


# =============================================================================
# KHỞI TẠO FLASK WEB SERVER (CÓ TRANG WEB XEM TRỰC TIẾP TRÊN TRÌNH DUYỆT)
# =============================================================================
app = Flask(__name__)
flask_app = app

WEB_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="2">
    <title>ESP32 IoT Web Monitor</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #1E1E2E; color: #CDD6F4; margin: 0; padding: 24px; }
        .container { max-width: 820px; margin: auto; }
        .header { background: #2A2A3E; padding: 18px 24px; border-radius: 10px; margin-bottom: 20px; border-left: 5px solid #89B4FA; }
        h1 { margin: 0; font-size: 22px; color: #89B4FA; }
        .badge { display: inline-block; padding: 5px 12px; border-radius: 6px; font-weight: bold; font-size: 13px; margin-top: 8px; }
        .wifi { background: #A6E3A1; color: #11111B; }
        .usb { background: #FAB387; color: #11111B; }
        .wait { background: #45475A; color: #CDD6F4; }
        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
        .card { background: #2A2A3E; padding: 18px; border-radius: 10px; text-align: center; }
        .card-title { font-size: 12px; color: #BAC2DE; font-weight: bold; }
        .card-val { font-size: 28px; font-weight: bold; margin-top: 8px; }
        .info-box { background: #181825; padding: 16px; border-radius: 10px; font-family: Consolas, monospace; font-size: 13px; line-height: 1.7; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>TRẠM GIÁM SÁT IOT ESP32 (WEB REAL-TIME)</h1>
            {% if data.source_type == 'WIFI' %}
                <span class="badge wifi">📶 ĐANG KẾT NỐI: WI-FI ({{ data.source_detail }})</span>
            {% elif data.source_type == 'USB' %}
                <span class="badge usb">🔌 ĐANG KẾT NỐI: CÁP USB ({{ data.source_detail }})</span>
            {% else %}
                <span class="badge wait">⏳ ĐANG CHỜ TÍN HIỆU TỪ ESP32...</span>
            {% endif %}
        </div>
        <div class="grid">
            <div class="card"><div class="card-title">NHIỆT ĐỘ</div><div class="card-val" style="color:#F38BA8">{{ data.temp }} °C</div></div>
            <div class="card"><div class="card-title">ĐỘ ẨM</div><div class="card-val" style="color:#89DCEB">{{ data.hum }} %</div></div>
            <div class="card"><div class="card-title">KHÍ GAS (RAW)</div><div class="card-val" style="color:#FAB387">{{ data.gas }}</div></div>
            <div class="card"><div class="card-title">BỤI (DUST RAW)</div><div class="card-val" style="color:#A6E3A1">{{ data.dust }}</div></div>
        </div>
        <div class="info-box">
            <div><b>Gói tin mới nhất (#{{ data.packet_id }}):</b> {{ data.timestamp }}</div>
            <div><b>Nguồn gửi:</b> {{ data.source_type }} - {{ data.source_detail }}</div>
            <div><b>API Endpoint nhận POST:</b> <code>http://{{ host_ip }}:{{ port }}/data</code></div>
            <div><b>API JSON:</b> <a href="/api/status" style="color:#89B4FA">/api/status</a></div>
        </div>
    </div>
</body>
</html>
"""


@app.route('/', methods=['GET'])
def web_dashboard():
    """Trang chủ hiển thị Web Dashboard trực quan khi mở trên trình duyệt."""
    with buffer_lock:
        snapshot = dict(shared_sensor_buffer)
    primary_ip, _ = lay_tat_ca_ip_may_tinh()
    return render_template_string(WEB_DASHBOARD_HTML, data=snapshot, host_ip=primary_ip, port=SERVER_PORT)


@app.route('/api/status', methods=['GET'])
def api_status():
    """Trả về JSON trạng thái hiện tại."""
    with buffer_lock:
        snapshot = dict(shared_sensor_buffer)
    return jsonify({
        "service": "ESP32 IoT Local Server",
        "status": "running",
        "shared_dictionary_buffer": snapshot
    }), 200


@app.route('/data', methods=['GET', 'POST'])
def receive_sensor_data():
    """
    Route '/data' hứng dữ liệu HTTP POST từ ESP32 qua Wi-Fi.
    Nếu mở bằng trình duyệt (GET không tham số), hiển thị trạng thái JSON.
    """
    if request.method == 'GET' and not request.args:
        with buffer_lock:
            snapshot = dict(shared_sensor_buffer)
        return jsonify({
            "service": "ESP32 IoT Local Server (/data endpoint)",
            "status": "listening_for_esp32_post",
            "latest_data": snapshot
        }), 200

    try:
        form_data = request.form if request.form else request.args

        temp_str = form_data.get('temp', None)
        hum_str = form_data.get('hum', None)
        gas_str = form_data.get('gas', None)
        dust_str = form_data.get('dust', None)

        if None in (temp_str, hum_str, gas_str, dust_str):
            return jsonify({
                "status": "error",
                "message": "Thieu tham so! Yeu cau du 4 tham so: temp, hum, gas, dust"
            }), 400

        # Giữ nguyên giá trị thô (Raw) từ ESP32, không tính toán lại
        temp_val = str(temp_str).strip()
        hum_val = str(hum_str).strip()
        gas_val = str(gas_str).strip()
        dust_val = str(dust_str).strip()

        float(temp_val)
        float(hum_val)
        float(gas_val)
        float(dust_val)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_ip = request.remote_addr or "Unknown"

        with buffer_lock:
            shared_sensor_buffer["temp"] = temp_val
            shared_sensor_buffer["hum"] = hum_val
            shared_sensor_buffer["gas"] = gas_val
            shared_sensor_buffer["dust"] = dust_val
            shared_sensor_buffer["timestamp"] = now_str
            shared_sensor_buffer["source_type"] = "WIFI"
            shared_sensor_buffer["source_detail"] = f"ESP32 IP: {client_ip}"
            shared_sensor_buffer["packet_id"] += 1
            current_id = shared_sensor_buffer["packet_id"]

        return jsonify({"status": "ok", "packet_id": current_id}), 200

    except ValueError as ve:
        return jsonify({"status": "error", "message": f"Du lieu khong phai la so hop le: {ve}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# CÁC HÀM QUẢN LÝ FILE CSV & CỔNG COM USB
# =============================================================================
def tim_cong_com_esp32():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    esp_keywords = ["cp210", "ch340", "usb", "serial", "uart", "esp"]
    for p in ports:
        desc = (p.description or "").lower()
        if any(kw in desc for kw in esp_keywords):
            return p.device
    return ports[0].device


def lay_ten_file_moi(thu_muc_data, ngay_str):
    os.makedirs(thu_muc_data, exist_ok=True)
    pattern = re.compile(rf"^data_{re.escape(ngay_str)}_lan_(\d+)")
    cac_so_thu_tu = []
    for ten_file in os.listdir(thu_muc_data):
        match = pattern.match(ten_file)
        if match:
            try:
                cac_so_thu_tu.append(int(match.group(1)))
            except ValueError:
                pass
    so_thu_tu_tiep = max(cac_so_thu_tu, default=0) + 1
    ten_file_tam = f"data_{ngay_str}_lan_{so_thu_tu_tiep:02d}_(dang_chay).csv"
    duong_dan_tam = os.path.join(thu_muc_data, ten_file_tam)
    return so_thu_tu_tiep, duong_dan_tam


def doi_ten_file_khi_ket_thuc(duong_dan_tam, thu_muc_data, ngay_str, so_thu_tu, start_dt, end_dt):
    gio_bat_dau = start_dt.strftime("%Hh%M")
    gio_ket_thuc = end_dt.strftime("%Hh%M")
    ten_chinh_thuc = f"data_{ngay_str}_lan_{so_thu_tu:02d}_({gio_bat_dau}-{gio_ket_thuc}).csv"
    duong_dan_chinh_thuc = os.path.join(thu_muc_data, ten_chinh_thuc)

    if os.path.exists(duong_dan_chinh_thuc):
        gio_bat_dau_giay = start_dt.strftime("%Hh%M%S")
        gio_ket_thuc_giay = end_dt.strftime("%Hh%M%S")
        ten_chinh_thuc = f"data_{ngay_str}_lan_{so_thu_tu:02d}_({gio_bat_dau_giay}-{gio_ket_thuc_giay}).csv"
        duong_dan_chinh_thuc = os.path.join(thu_muc_data, ten_chinh_thuc)

    try:
        if os.path.exists(duong_dan_tam):
            os.rename(duong_dan_tam, duong_dan_chinh_thuc)
            return duong_dan_chinh_thuc
    except Exception as e:
        print(f"[CẢNH BÁO] Không thể đổi tên file: {e}")
    return duong_dan_tam


# =============================================================================
# GIAO DIỆN CHÍNH TKINTER (HIỂN THỊ RÕ KẾT NỐI WI-FI / USB & URL CHÍNH XÁC)
# =============================================================================
class AppIoTLocalServer:
    def __init__(self, root):
        self.root = root
        self.root.title("Trạm Giám Sát & Thu Thập Dữ Liệu IoT ESP32 (Wi-Fi HTTP & USB Serial)")
        self.root.geometry("900x660")
        self.root.minsize(840, 620)
        self.root.configure(bg="#1E1E2E")

        self.local_ip, self.all_ips = lay_tat_ca_ip_may_tinh()
        self.web_url = f"http://{self.local_ip}:{SERVER_PORT}/"
        self.post_url = f"http://{self.local_ip}:{SERVER_PORT}/data"

        self.file_handle = None
        self.ser = None
        self.usb_port_name = None
        self.running = True
        self.start_dt = datetime.now()
        self.ngay_hom_nay = self.start_dt.strftime("%Y-%m-%d")
        self.so_thu_tu, self.duong_dan_tam = lay_ten_file_moi(DATA_DIR, self.ngay_hom_nay)
        self.so_dong = 0
        self.last_processed_packet_id = 0
        self.current_mode = "WAITING"

        try:
            self.file_handle = open(self.duong_dan_tam, mode='w', encoding='utf-8')
            self.file_handle.write(HEADER_CSV)
            self.file_handle.flush()
        except Exception as e:
            messagebox.showerror("Lỗi File", f"Không thể tạo file lưu dữ liệu:\n{e}")

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 1. Khởi chạy Luồng Mạng Flask (Wi-Fi)
        self.khoi_chay_luong_flask_ngam()

        # 2. Khởi chạy Luồng Đọc Cáp USB Serial (COM) tự động dự phòng
        self.khoi_chay_luong_usb_ngam()

        # 3. Vòng lặp cập nhật UI mỗi 100ms
        self.doc_dictionary_cap_nhat_ui()
        self.cap_nhat_thoi_gian_chay()

    def setup_ui(self):
        # 1. THANH TIÊU ĐỀ & HUY HIỆU TRẠNG THÁI KẾT NỐI (WI-FI HAY USB)
        header_frame = tk.Frame(self.root, bg="#2A2A3E", padx=20, pady=12)
        header_frame.pack(fill=tk.X)

        top_row = tk.Frame(header_frame, bg="#2A2A3E")
        top_row.pack(fill=tk.X)

        title_lbl = tk.Label(
            top_row,
            text="TRẠM THU THẬP & GIÁM SÁT DỮ LIỆU IOT ESP32",
            font=("Segoe UI", 15, "bold"), fg="#89B4FA", bg="#2A2A3E"
        )
        title_lbl.pack(side=tk.LEFT)

        # Huy hiệu nổi bật báo đang kết nối Wi-Fi hay USB
        self.badge_conn = tk.Label(
            top_row,
            text=" ⏳ ĐANG CHỜ TÍN HIỆU (WI-FI / USB) ",
            font=("Segoe UI", 10, "bold"),
            bg="#45475A", fg="#F9E2AF", padx=12, pady=4
        )
        self.badge_conn.pack(side=tk.RIGHT)

        self.status_lbl = tk.Label(
            header_frame,
            text=f"Phiên làm việc: Lần #{self.so_thu_tu:02d} ngày {self.ngay_hom_nay}  |  Đang lắng nghe cả Wi-Fi (Port {SERVER_PORT}) và cổng USB COM...",
            font=("Segoe UI", 9), fg="#BAC2DE", bg="#2A2A3E"
        )
        self.status_lbl.pack(anchor="w", pady=(6, 0))

        # 2. KHUNG ĐƯỜNG DẪN WEB & ĐỊA CHỈ SERVER CHÍNH XÁC
        net_frame = tk.LabelFrame(
            self.root,
            text=" 🌐 ĐƯỜNG DẪN TRUY CẬP WEB & ĐỊA CHỈ SERVER CHÍNH XÁC CỦA MÁY TÍNH ",
            font=("Segoe UI", 10, "bold"), fg="#89DCEB", bg="#1E1E2E", padx=15, pady=10
        )
        net_frame.pack(fill=tk.X, padx=20, pady=(12, 5))

        # Dòng 1: Đường dẫn mở trình duyệt Web Dashboard
        row_web = tk.Frame(net_frame, bg="#1E1E2E")
        row_web.pack(fill=tk.X, pady=2)

        tk.Label(
            row_web, text="1. Đường dẫn xem trên Web:", width=26, anchor="w",
            font=("Segoe UI", 9, "bold"), fg="#CDD6F4", bg="#1E1E2E"
        ).pack(side=tk.LEFT)

        self.entry_web_url = tk.Entry(
            row_web, font=("Consolas", 10, "bold"), bg="#11111B", fg="#A6E3A1",
            readonlybackground="#11111B", relief=tk.FLAT, width=38
        )
        self.entry_web_url.insert(0, self.web_url)
        self.entry_web_url.config(state="readonly")
        self.entry_web_url.pack(side=tk.LEFT, padx=(0, 8), ipady=3)

        tk.Button(
            row_web, text="🌐 Mở Web ngay", font=("Segoe UI", 9, "bold"),
            bg="#89B4FA", fg="#11111B", relief=tk.FLAT, cursor="hand2", padx=10,
            command=lambda: webbrowser.open(self.web_url)
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            row_web, text="📋 Copy", font=("Segoe UI", 9),
            bg="#313244", fg="#CDD6F4", relief=tk.FLAT, cursor="hand2", padx=8,
            command=lambda: self.copy_to_clipboard(self.web_url)
        ).pack(side=tk.LEFT, padx=3)

        # Dòng 2: Đường dẫn Server nhận dữ liệu từ ESP32 (serverName trong code Arduino)
        row_post = tk.Frame(net_frame, bg="#1E1E2E")
        row_post.pack(fill=tk.X, pady=4)

        tk.Label(
            row_post, text="2. Server URL (nạp vào ESP32):", width=26, anchor="w",
            font=("Segoe UI", 9, "bold"), fg="#CDD6F4", bg="#1E1E2E"
        ).pack(side=tk.LEFT)

        self.entry_post_url = tk.Entry(
            row_post, font=("Consolas", 10, "bold"), bg="#11111B", fg="#F9E2AF",
            readonlybackground="#11111B", relief=tk.FLAT, width=38
        )
        self.entry_post_url.insert(0, self.post_url)
        self.entry_post_url.config(state="readonly")
        self.entry_post_url.pack(side=tk.LEFT, padx=(0, 8), ipady=3)

        tk.Button(
            row_post, text="📋 Copy URL cho ESP32", font=("Segoe UI", 9),
            bg="#313244", fg="#CDD6F4", relief=tk.FLAT, cursor="hand2", padx=10,
            command=lambda: self.copy_to_clipboard(self.post_url)
        ).pack(side=tk.LEFT, padx=3)

        # Dòng 3: Hiển thị tất cả IP của máy và trạng thái cổng USB
        all_ips_str = ", ".join(self.all_ips)
        self.lbl_net_detail = tk.Label(
            net_frame,
            text=f"IPv4 của máy tính: [{all_ips_str}]   |   Cổng USB Serial: Đang quét tự động...",
            font=("Consolas", 9), fg="#A6ADC8", bg="#1E1E2E", anchor="w"
        )
        self.lbl_net_detail.pack(fill=tk.X, pady=(2, 0))

        # 3. KHUNG 4 THẺ CHỈ SỐ CẢM BIẾN THỜI GIAN THỰC
        cards_frame = tk.Frame(self.root, bg="#1E1E2E", padx=20, pady=10)
        cards_frame.pack(fill=tk.X)
        cards_frame.columnconfigure((0, 1, 2, 3), weight=1, uniform="card")

        self.card_temp = self.create_sensor_card(cards_frame, 0, "NHIỆT ĐỘ (TEMP)", "-- °C", "#F38BA8")
        self.card_hum = self.create_sensor_card(cards_frame, 1, "ĐỘ ẨM (HUM)", "-- %", "#89DCEB")
        self.card_gas = self.create_sensor_card(cards_frame, 2, "KHÍ GAS (RAW)", "--", "#FAB387")
        self.card_dust = self.create_sensor_card(cards_frame, 3, "BỤI (DUST RAW)", "--", "#A6E3A1")

        # 4. KHUNG THỐNG KÊ PHIÊN CHẠY
        info_frame = tk.Frame(self.root, bg="#2A2A3E", padx=15, pady=8)
        info_frame.pack(fill=tk.X, padx=20, pady=4)

        self.lbl_stats = tk.Label(
            info_frame,
            text=f"Phương thức: Chưa nhận dữ liệu | Số mẫu đã lưu: 0 dòng | Thời gian chạy: 00:00:00 | File: {os.path.basename(self.duong_dan_tam)}",
            font=("Segoe UI", 9, "bold"), fg="#F9E2AF", bg="#2A2A3E", anchor="w"
        )
        self.lbl_stats.pack(fill=tk.X)

        # 5. KHUNG NHẬT KÝ GÓI TIN (LOG)
        log_frame = tk.LabelFrame(
            self.root, text=" NHẬT KÝ DỮ LIỆU ĐẾN (TỰ ĐỘNG GHI NHẬN TỪ WI-FI HOẶC CÁP USB) ",
            font=("Segoe UI", 10, "bold"), fg="#CDD6F4", bg="#1E1E2E", padx=10, pady=5
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=6)

        self.log_text = tk.Text(
            log_frame, bg="#11111B", fg="#A6ADC8", font=("Consolas", 9), height=7, relief=tk.FLAT
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        # 6. THANH NÚT BẤM ĐIỀU KHIỂN
        btn_frame = tk.Frame(self.root, bg="#1E1E2E", padx=20, pady=10)
        btn_frame.pack(fill=tk.X)

        self.btn_stop = tk.Button(
            btn_frame, text="DỪNG GHI & HOÀN TẤT LƯU FILE", font=("Segoe UI", 10, "bold"),
            bg="#F38BA8", fg="#11111B", activebackground="#EBA0AC", padx=18, pady=6,
            relief=tk.FLAT, cursor="hand2", command=self.dung_thu_thap
        )
        self.btn_stop.pack(side=tk.RIGHT)

        btn_open_folder = tk.Button(
            btn_frame, text="📁 Mở thư mục Data", font=("Segoe UI", 10),
            bg="#313244", fg="#CDD6F4", padx=14, pady=6,
            relief=tk.FLAT, cursor="hand2", command=self.mo_thu_muc_data
        )
        btn_open_folder.pack(side=tk.LEFT)

    def create_sensor_card(self, parent, col, title, initial_val, color):
        card = tk.Frame(parent, bg="#2A2A3E", padx=15, pady=12, relief=tk.FLAT)
        card.grid(row=0, column=col, padx=6, sticky="nsew")

        lbl_t = tk.Label(card, text=title, font=("Segoe UI", 9, "bold"), fg="#BAC2DE", bg="#2A2A3E")
        lbl_t.pack(anchor="w")

        lbl_v = tk.Label(card, text=initial_val, font=("Segoe UI", 20, "bold"), fg=color, bg="#2A2A3E")
        lbl_v.pack(anchor="w", pady=(6, 0))

        return lbl_v

    def copy_to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log(f"[*] Đã copy đường dẫn vào bộ nhớ tạm: {text}")

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def mo_thu_muc_data(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.startfile(DATA_DIR)

    # =========================================================================
    # LUỒNG 1: FLASK WEB SERVER (NHẬN DỮ LIỆU WI-FI)
    # =========================================================================
    def khoi_chay_luong_flask_ngam(self):
        def run_flask():
            try:
                app.run(host='0.0.0.0', port=SERVER_PORT, use_reloader=False)
            except Exception as e:
                self.root.after(0, self.log, f"[LỖI FLASK SERVER] {e}")

        print("\n[OK] MÁY CHỦ ĐÃ MỞ: Đang chờ gói tin POST từ ESP32 qua mạng Wi-Fi tại cổng 8080...", flush=True)

        self.flask_thread = threading.Thread(target=run_flask, name="FlaskDaemonThread", daemon=True)
        self.flask_thread.start()

        self.log(f"[WI-FI SERVER] Đã mở cổng {SERVER_PORT} thành công!")
        self.log(f"   -> Xem giao diện Web tại : {self.web_url}")
        self.log(f"   -> Địa chỉ POST cho ESP32: {self.post_url}")

    # =========================================================================
    # LUỒNG 2: ĐỌC CÁP USB SERIAL (DỰ PHÒNG HOẶC KHI CẮM DÂY TRỰC TIẾP)
    # =========================================================================
    def khoi_chay_luong_usb_ngam(self):
        def run_usb_listener():
            # Hỗ trợ cả 2 định dạng Serial của ESP32:
            # 1) Chuỗi CSV: "28.5,65.0,210,730"
            # 2) Chuỗi tiếng Việt khi mất Wi-Fi: "Nhiệt độ: 28.5 *C | Độ ẩm: 65.0 % | Gas (Raw): 210 | Bụi (Raw): 730"
            pattern_csv = re.compile(r"^\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*$")
            pattern_vn = re.compile(
                r"Nhiệt độ:\s*([-+]?\d*\.?\d+).*?Độ ẩm:\s*([-+]?\d*\.?\d+).*?Gas.*?:\s*([-+]?\d*\.?\d+).*?Bụi.*?:\s*([-+]?\d*\.?\d+)",
                re.IGNORECASE
            )

            while self.running:
                if self.ser is None or not self.ser.is_open:
                    port = tim_cong_com_esp32()
                    if port:
                        try:
                            self.ser = serial.Serial(port, BAUD_RATE, timeout=1)
                            # Tự động kích xung DTR/RTS để khởi động lại ESP32
                            self.ser.setDTR(False)
                            self.ser.setRTS(False)
                            time.sleep(0.1)
                            self.ser.setDTR(True)
                            self.ser.setRTS(True)
                            time.sleep(0.1)
                            self.ser.setDTR(False)
                            self.ser.setRTS(False)

                            self.usb_port_name = port
                            self.root.after(0, self.cap_nhat_trang_thai_usb, f"Đã kết nối cáp tại {port}")
                            self.root.after(0, self.log, f"[USB SERIAL] Đã tự động kết nối cổng {port} (Baud: {BAUD_RATE}).")
                        except Exception:
                            self.usb_port_name = None
                            self.root.after(0, self.cap_nhat_trang_thai_usb, f"Phát hiện {port} (Đang bận/Chờ)")
                            time.sleep(3)
                            continue
                    else:
                        self.usb_port_name = None
                        self.root.after(0, self.cap_nhat_trang_thai_usb, "Không cắm cáp USB (Chỉ dùng Wi-Fi)")
                        time.sleep(2)
                        continue

                try:
                    if self.ser and self.ser.in_waiting > 0:
                        raw_line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                        if not raw_line:
                            continue

                        # Nếu ESP32 chỉ in "HTTP Code: 200" qua Serial nghĩa là nó đang gửi qua Wi-Fi
                        if raw_line.startswith("HTTP Code:") or raw_line.startswith("[OK]") or raw_line.startswith("Địa chỉ IP"):
                            self.root.after(0, self.log, f"   [ESP32 Serial Log] {raw_line}")
                            continue

                        m_csv = pattern_csv.match(raw_line)
                        m_vn = pattern_vn.search(raw_line)
                        match = m_csv or m_vn

                        if match:
                            t_val, h_val, g_val, d_val = match.group(1), match.group(2), match.group(3), match.group(4)
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            with buffer_lock:
                                shared_sensor_buffer["temp"] = t_val
                                shared_sensor_buffer["hum"] = h_val
                                shared_sensor_buffer["gas"] = g_val
                                shared_sensor_buffer["dust"] = d_val
                                shared_sensor_buffer["timestamp"] = now_str
                                shared_sensor_buffer["source_type"] = "USB"
                                shared_sensor_buffer["source_detail"] = f"Cổng {self.usb_port_name}"
                                shared_sensor_buffer["packet_id"] += 1
                        else:
                            # Hiển thị thông báo trạng thái từ ESP32 (ví dụ: Đang kết nối Wi-Fi...)
                            if not any(k in raw_line for k in ["ets ", "rst:", "configsip:", "clk_drv:", "mode:DIO", "load:0x", "entry 0x", "ho 0 tail"]):
                                self.root.after(0, self.log, f"   [ESP32 Serial] {raw_line}")
                    else:
                        time.sleep(0.05)
                except Exception:
                    try:
                        if self.ser:
                            self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                    time.sleep(2)

        self.usb_thread = threading.Thread(target=run_usb_listener, name="USBDaemonThread", daemon=True)
        self.usb_thread.start()

    def cap_nhat_trang_thai_usb(self, usb_status_text):
        all_ips_str = ", ".join(self.all_ips)
        self.lbl_net_detail.config(
            text=f"IPv4 của máy tính: [{all_ips_str}]   |   Trạng thái cáp USB: {usb_status_text}"
        )

    # =========================================================================
    # ĐỒNG BỘ TỪ DICTIONARY BUFFER LÊN GIAO DIỆN & LƯU CSV
    # =========================================================================
    def doc_dictionary_cap_nhat_ui(self):
        if not self.running:
            return

        snapshot = None
        with buffer_lock:
            if shared_sensor_buffer["packet_id"] > self.last_processed_packet_id:
                snapshot = dict(shared_sensor_buffer)
                self.last_processed_packet_id = snapshot["packet_id"]

        if snapshot is not None:
            temp_val = snapshot["temp"]
            hum_val = snapshot["hum"]
            gas_val = snapshot["gas"]
            dust_val = snapshot["dust"]
            now_str = snapshot["timestamp"]
            source_type = snapshot["source_type"]
            source_detail = snapshot["source_detail"]

            # 1. Cập nhật 4 thẻ chỉ số cảm biến
            self.card_temp.config(text=f"{temp_val} °C")
            self.card_hum.config(text=f"{hum_val} %")
            self.card_gas.config(text=f"{gas_val}")
            self.card_dust.config(text=f"{dust_val}")

            # 2. Cập nhật Huy hiệu (Badge) báo rõ đang kết nối qua WI-FI hay USB
            self.current_mode = f"{source_type} ({source_detail})"
            if source_type == "WIFI":
                self.badge_conn.config(
                    text=f" 📶 ĐANG KẾT NỐI: WI-FI ({source_detail}) ",
                    bg="#A6E3A1", fg="#11111B"
                )
            elif source_type == "USB":
                self.badge_conn.config(
                    text=f" 🔌 ĐANG KẾT NỐI: CÁP USB ({source_detail}) ",
                    bg="#FAB387", fg="#11111B"
                )

            # 3. Ghi xuống file CSV cục bộ
            if self.file_handle and not self.file_handle.closed:
                dong_csv = f"{now_str},{temp_val},{hum_val},{gas_val},{dust_val}\n"
                self.file_handle.write(dong_csv)
                self.file_handle.flush()
                self.so_dong += 1

            # 4. Đồng thời đẩy thẳng mẫu dữ liệu lên Supabase Cloud (chạy ngầm không gây trễ UI)
            day_mau_len_supabase_async(
                thoi_gian=now_str,
                temp_val=temp_val,
                hum_val=hum_val,
                gas_val=gas_val,
                dust_val=dust_val,
                nguon_nhan=os.path.basename(self.duong_dan_tam),
                callback_log=lambda msg: self.root.after(0, self.log, msg)
            )

            # 5. Ghi vào bảng nhật ký
            self.log(
                f"[{self.so_dong:04d}] {now_str} | [{source_type}] -> "
                f"Temp: {temp_val}°C | Hum: {hum_val}% | Gas: {gas_val} | Dust: {dust_val}  [☁️ Đã lưu CSV + Supabase]"
            )

        self.root.after(100, self.doc_dictionary_cap_nhat_ui)

    def cap_nhat_thoi_gian_chay(self):
        if self.running and self.start_dt:
            delta = datetime.now() - self.start_dt
            s = int(delta.total_seconds())
            h, rem = divmod(s, 3600)
            m, sec = divmod(rem, 60)
            tg_str = f"{h:02d}:{m:02d}:{sec:02d}"
            self.lbl_stats.config(
                text=f"Nguồn nhận: {self.current_mode} | Số mẫu đã lưu: {self.so_dong} dòng | Thời gian chạy: {tg_str} | File: {os.path.basename(self.duong_dan_tam)}"
            )
            self.root.after(1000, self.cap_nhat_thoi_gian_chay)

    def dung_thu_thap(self):
        if not self.running:
            return
        self.running = False
        now_end = datetime.now()

        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

        if self.file_handle and not self.file_handle.closed:
            try:
                self.file_handle.flush()
                self.file_handle.close()
            except Exception:
                pass

        file_cuoi = doi_ten_file_khi_ket_thuc(
            self.duong_dan_tam, DATA_DIR, self.ngay_hom_nay, self.so_thu_tu, self.start_dt, now_end
        )

        self.badge_conn.config(text=" 🛑 ĐÃ DỪNG THU THẬP ", bg="#F38BA8", fg="#11111B")
        self.btn_stop.config(text="ĐÃ DỪNG", state=tk.DISABLED, bg="#45475A", fg="#A6ADC8")

        self.log("=" * 85)
        self.log(f"[*] Đã dừng phiên thu thập và lưu thành công file: {os.path.basename(file_cuoi)}")
        self.log(f"[*] Tổng số mẫu đã lưu: {self.so_dong} dòng.")

        messagebox.showinfo("Hoàn Tất", f"Đã thu thập xong {self.so_dong} mẫu!\n\nFile lưu tại:\n{file_cuoi}")

    def on_close(self):
        if self.running:
            if messagebox.askyesno(
                "Xác nhận thoát",
                "Hệ thống đang thu thập dữ liệu.\nBạn có muốn dừng và hoàn tất lưu file CSV trước khi thoát?"
            ):
                self.dung_thu_thap()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    app_ui = AppIoTLocalServer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
