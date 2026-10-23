"""
=============================================================================
CHUONG TRINH THU THAP DU LIEU CAM BIEN TU ESP32 VA LUU VAO FILE CSV
CO GIAO DIEN (GUI) HIEN THI CHI SO THEO THOI GIAN THUC
=============================================================================
- Giao dien truc quan: Nhiet do, Do am, Gas Raw, Bui Raw, Tong so mau, Thoi gian chay.
- Tu dong xac dinh thu muc luu: iot/data/
- Tu dong danh so thu tu theo ngay (lan_01, lan_02,...)
- Tu dong doi ten file khi ket thuc: data_YYYY-MM-DD_lan_01_(08h30-09h15).csv
- Tu dong tim cong COM ESP32.
=============================================================================
"""

import os
import sys
import re
import time
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports

# =============================================================================
# CAU HINH HE THONG
# =============================================================================
CONG_COM = 'AUTO'
BAUD_RATE = 115200

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'data'))
HEADER_CSV = "Thoi_Gian_Thuc,Nhiet_Do_C,Do_Am_%,Gas_Raw,Dust_Raw\n"
# =============================================================================

def tim_cong_com_esp32():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None, []
    esp_keywords = ["cp210", "ch340", "usb", "serial", "uart", "esp"]
    for p in ports:
        desc = (p.description or "").lower()
        if any(kw in desc for kw in esp_keywords):
            return p.device, ports
    return ports[0].device, ports

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
        print(f"[CANH BAO] Khong the doi ten file: {e}")
    return duong_dan_tam


class AppThuThapData:
    def __init__(self, root):
        self.root = root
        self.root.title("Giám Sát & Thu Thập Dữ Liệu IoT - ESP32")
        self.root.geometry("780x560")
        self.root.minsize(720, 520)
        self.root.configure(bg="#1E1E2E")

        self.ser = None
        self.file_handle = None
        self.running = False
        self.start_dt = None
        self.so_thu_tu = 1
        self.duong_dan_tam = ""
        self.ngay_hom_nay = ""
        self.so_dong = 0

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Tu dong bat dau sau khi giao dien hien thi
        self.root.after(500, self.bat_dau_thu_thap)

    def setup_ui(self):
        # Tieu de tren cung
        header_frame = tk.Frame(self.root, bg="#2A2A3E", padx=20, pady=12)
        header_frame.pack(fill=tk.X)

        title_lbl = tk.Label(header_frame, text="HỆ THỐNG GIÁM SÁT CẢM BIẾN REAL-TIME", 
                             font=("Segoe UI", 16, "bold"), fg="#89B4FA", bg="#2A2A3E")
        title_lbl.pack(anchor="w")
        
        self.status_lbl = tk.Label(header_frame, text="Trạng thái: Đang khởi tạo...", 
                                   font=("Segoe UI", 10), fg="#A6ADC8", bg="#2A2A3E")
        self.status_lbl.pack(anchor="w", pady=(3, 0))

        # Khung cac Card hien thi chi so Realtime
        cards_frame = tk.Frame(self.root, bg="#1E1E2E", padx=20, pady=15)
        cards_frame.pack(fill=tk.X)
        cards_frame.columnconfigure((0, 1, 2, 3), weight=1, uniform="card")

        self.card_temp = self.create_sensor_card(cards_frame, 0, "NHIỆT ĐỘ", "-- °C", "#F38BA8")
        self.card_hum = self.create_sensor_card(cards_frame, 1, "ĐỘ ẨM", "-- %", "#89DCEB")
        self.card_gas = self.create_sensor_card(cards_frame, 2, "GAS (MQ)", "--", "#FAB387")
        self.card_dust = self.create_sensor_card(cards_frame, 3, "BỤI (DUST)", "--", "#A6E3A1")

        # Khung Thong Tin Phien Chay
        info_frame = tk.LabelFrame(self.root, text=" THÔNG TIN PHIÊN THU THẬP ", 
                                   font=("Segoe UI", 10, "bold"), fg="#CDD6F4", bg="#1E1E2E", padx=15, pady=10)
        info_frame.pack(fill=tk.X, padx=20, pady=5)

        self.lbl_file = tk.Label(info_frame, text="File đang lưu: Đang chờ...", font=("Consolas", 9), fg="#BAC2DE", bg="#1E1E2E", anchor="w")
        self.lbl_file.pack(fill=tk.X)

        self.lbl_stats = tk.Label(info_frame, text="Cổng COM: -- | Số mẫu đã lưu: 0 dòng | Thời gian chạy: 00:00:00", 
                                  font=("Segoe UI", 10, "bold"), fg="#F9E2AF", bg="#1E1E2E", anchor="w", pady=4)
        self.lbl_stats.pack(fill=tk.X)

        # Bang Log Du Lieu Nhan Duoc Gan Nhat
        log_frame = tk.LabelFrame(self.root, text=" NHẬT KÝ DỮ LIỆU ĐẾN (LOG MỚI NHẤT) ", 
                                  font=("Segoe UI", 10, "bold"), fg="#CDD6F4", bg="#1E1E2E", padx=10, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        self.log_text = tk.Text(log_frame, bg="#11111B", fg="#A6ADC8", font=("Consolas", 9), height=7, relief=tk.FLAT)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        # Thanh nut bam duoi cung
        btn_frame = tk.Frame(self.root, bg="#1E1E2E", padx=20, pady=10)
        btn_frame.pack(fill=tk.X)

        self.btn_stop = tk.Button(btn_frame, text="DỪNG & HOÀN TẤT LƯU FILE", font=("Segoe UI", 11, "bold"),
                                  bg="#F38BA8", fg="#11111B", activebackground="#EBA0AC", padx=20, pady=6,
                                  relief=tk.FLAT, cursor="hand2", command=self.dung_thu_thap)
        self.btn_stop.pack(side=tk.RIGHT)

        btn_open_folder = tk.Button(btn_frame, text="Mở thư mục Data", font=("Segoe UI", 10),
                                    bg="#313244", fg="#CDD6F4", padx=15, pady=6,
                                    relief=tk.FLAT, cursor="hand2", command=self.mo_thu_muc_data)
        btn_open_folder.pack(side=tk.LEFT)

    def create_sensor_card(self, parent, col, title, initial_val, color):
        card = tk.Frame(parent, bg="#2A2A3E", padx=15, pady=15, relief=tk.FLAT)
        card.grid(row=0, column=col, padx=6, sticky="nsew")

        lbl_t = tk.Label(card, text=title, font=("Segoe UI", 9, "bold"), fg="#BAC2DE", bg="#2A2A3E")
        lbl_t.pack(anchor="w")

        lbl_v = tk.Label(card, text=initial_val, font=("Segoe UI", 20, "bold"), fg=color, bg="#2A2A3E")
        lbl_v.pack(anchor="w", pady=(8, 0))

        return lbl_v

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def mo_thu_muc_data(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.startfile(DATA_DIR)

    def bat_dau_thu_thap(self):
        cong = CONG_COM
        if CONG_COM.upper() == 'AUTO':
            self.log("[*] Đang tự động tìm cổng COM ESP32...")
            cong_tim_thay, danh_sach = tim_cong_com_esp32()
            if not cong_tim_thay:
                self.status_lbl.config(text="LỖI: Không tìm thấy cổng COM của ESP32!", fg="#F38BA8")
                self.log("[LỖI] Không thấy cổng COM. Hãy cắm lại dây USB ESP32 và tắt Serial Monitor bên Arduino IDE.")
                messagebox.showerror("Lỗi Cổng COM", "Không tìm thấy cổng COM của ESP32!\n\n1. Kiểm tra cáp USB.\n2. Tắt Serial Monitor trong Arduino IDE.")
                return
            cong = cong_tim_thay

        self.cong_hien_tai = cong
        self.start_dt = datetime.now()
        self.ngay_hom_nay = self.start_dt.strftime("%Y-%m-%d")
        self.so_thu_tu, self.duong_dan_tam = lay_ten_file_moi(DATA_DIR, self.ngay_hom_nay)

        self.lbl_file.config(text=f"File đang lưu: {os.path.basename(self.duong_dan_tam)}")
        self.status_lbl.config(text=f"Trạng thái: Đang kết nối {cong}...", fg="#F9E2AF")

        try:
            self.ser = serial.Serial(cong, BAUD_RATE, timeout=1)
            
            # --- TU DONG RESET ESP32 (KHONG CAN AN NUT EN BANG TAY) ---
            # Keo chan DTR/RTS de kich hoat mach auto-reset tren board ESP32
            self.ser.setDTR(False)
            self.ser.setRTS(False)
            time.sleep(0.1)
            self.ser.setDTR(True)
            self.ser.setRTS(True)
            time.sleep(0.1)
            self.ser.setDTR(False)
            self.ser.setRTS(False)
            # -----------------------------------------------------------
            
            time.sleep(1.5)  # Cho ESP32 khoi dong xong
            self.file_handle = open(self.duong_dan_tam, mode='w', encoding='utf-8')
            self.file_handle.write(HEADER_CSV)
            self.file_handle.flush()
        except Exception as e:
            self.status_lbl.config(text=f"Lỗi mở cổng {cong}: {e}", fg="#F38BA8")
            self.log(f"[LỖI] Không thể mở {cong}: {e}")
            messagebox.showerror("Lỗi Serial", f"Không thể mở {cong}!\nNguyên nhân thường gặp: Serial Monitor bên Arduino IDE đang mở.\nHãy tắt nó đi rồi thử lại.")
            return

        self.running = True
        self.status_lbl.config(text=f"Trạng thái: Đang thu thập dữ liệu từ {cong} (Lần #{self.so_thu_tu:02d} trong ngày)", fg="#A6E3A1")
        self.log(f"[OK] Đã mở cổng {cong} thành công. Bắt đầu ghi file...")

        # Chay thread doc du lieu ngam de giao dien khong bi do (lag)
        self.thread = threading.Thread(target=self.vong_lap_doc_serial, daemon=True)
        self.thread.start()

        # Timer cap nhat thoi gian chay
        self.cap_nhat_thoi_gian()

    def cap_nhat_thoi_gian(self):
        if self.running and self.start_dt:
            delta = datetime.now() - self.start_dt
            s = int(delta.total_seconds())
            h, rem = divmod(s, 3600)
            m, sec = divmod(rem, 60)
            tg_str = f"{h:02d}:{m:02d}:{sec:02d}"
            self.lbl_stats.config(text=f"Cổng COM: {self.cong_hien_tai} | Số mẫu đã lưu: {self.so_dong} dòng | Thời gian chạy: {tg_str}")
            self.root.after(1000, self.cap_nhat_thoi_gian)

    def vong_lap_doc_serial(self):
        # Bieu thuc chinh quy kiem tra dong du lieu cam bien hop le: gom 4 con so cach nhau boi dau phay
        # Vi du hop le: 29.1,22.0,218,0
        pattern_data = re.compile(r"^\s*[-+]?\d*\.?\d+\s*,\s*[-+]?\d*\.?\d+\s*,\s*[-+]?\d*\.?\d+\s*,\s*[-+]?\d*\.?\d+\s*$")

        while self.running:
            try:
                if self.ser and self.ser.in_waiting > 0:
                    raw = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if raw:
                        # Kiem tra neu dong khong phai la data cam bien hop le (la log khoi dong/rac cua ESP32) -> bo qua
                        if not pattern_data.match(raw):
                            continue

                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        dong = f"{now_str},{raw}\n"
                        self.file_handle.write(dong)
                        self.file_handle.flush()
                        self.so_dong += 1

                        # Cap nhat giao dien
                        self.root.after(0, self.cap_nhat_chi_so, raw, now_str)
                time.sleep(0.01)
            except Exception as e:
                if self.running:
                    self.root.after(0, self.log, f"[LỖI ĐỌC] {e}")
                break

    def cap_nhat_chi_so(self, raw, now_str):
        parts = [p.strip() for p in raw.split(',')]
        # Format mong muon: NhietDo, DoAm, Gas, Dust
        try:
            if len(parts) >= 1 and parts[0]:
                self.card_temp.config(text=f"{float(parts[0]):.1f} °C")
            if len(parts) >= 2 and parts[1]:
                self.card_hum.config(text=f"{float(parts[1]):.1f} %")
            if len(parts) >= 3 and parts[2]:
                self.card_gas.config(text=f"{int(float(parts[2]))}")
            if len(parts) >= 4 and parts[3]:
                self.card_dust.config(text=f"{int(float(parts[3]))}")
        except Exception:
            pass

        self.log(f"[{self.so_dong:04d}] {now_str} -> {raw}")

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

        self.status_lbl.config(text="Trạng thái: ĐÃ DỪNG THU THẬP", fg="#F38BA8")
        self.lbl_file.config(text=f"File đã lưu: {os.path.basename(file_cuoi)}")
        self.btn_stop.config(text="ĐÃ DỪNG", state=tk.DISABLED, bg="#45475A", fg="#A6ADC8")
        
        self.log("=" * 60)
        self.log(f"[*] Đã dừng và hoàn tất lưu file: {os.path.basename(file_cuoi)}")
        self.log(f"[*] Tổng số mẫu thu thập được: {self.so_dong} dòng.")
        
        messagebox.showinfo("Hoàn Tất", f"Đã thu thập xong {self.so_dong} mẫu!\n\nFile lưu tại:\n{file_cuoi}")

    def on_close(self):
        if self.running:
            if messagebox.askyesno("Xác nhận thoát", "Đang thu thập dữ liệu. Bạn có muốn dừng và lưu file lại trước khi thoát?"):
                self.dung_thu_thap()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    app = AppThuThapData(root)
    root.mainloop()

if __name__ == "__main__":
    main()
