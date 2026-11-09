"""
=============================================================================
KỊCH BẢN ĐẨY TOÀN BỘ DỮ LIỆU CSV LÊN SUPABASE THEO THỨ TỰ THỜI GIAN TĂNG DẦN
=============================================================================
Tổng cộng 7 file CSV (4,266 dòng dữ liệu):
1. data_2026-10-23_lan_01_(20h51-20h54).csv (6 dòng)
2. data_2026-10-24_lan_01_(08h54-08h56).csv (4 dòng)
3. data_2026-10-24_lan_02_(20h28-22h09).csv (202 dòng)
4. data_2026-10-31_lan_01_(00h32-06h38).csv (1,407 dòng)
5. data_2026-10-31_lan_03_(09h37-09h40).csv (13 dòng)
6. data_2026-11-01_lan_01_(03h12-13h14).csv (2,408 dòng)
7. data_2026-11-01_lan_02_(13h54-14h51).csv (226 dòng)
=============================================================================
"""

import os
import csv
import json
import urllib.request
import urllib.error

SUPABASE_URL = "https://cyjzrllfgiggtolweyfp.supabase.co"
SUPABASE_KEY = "sb_publishable_xDLmWmu4Ksi8J9F4wNRrcQ_n3T_JDdJ"
TABLE_NAME = "sensor_data"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'data'))

# Danh sách 7 file sắp xếp đúng theo thứ tự ngày & giờ tăng dần
ORDERED_FILES = [
    "data_2026-10-23_lan_01_(20h51-20h54).csv",
    "data_2026-10-24_lan_01_(08h54-08h56).csv",
    "data_2026-10-24_lan_02_(20h28-22h09).csv",
    "data_2026-10-31_lan_01_(00h32-06h38).csv",
    "data_2026-10-31_lan_03_(09h37-09h40).csv",
    "data_2026-11-01_lan_01_(03h12-13h14).csv",
    "data_2026-11-01_lan_02_(13h54-14h51).csv",
]


def doc_va_sap_xep_du_lieu():
    all_records = []
    for filename in ORDERED_FILES:
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            print(f"[CẢNH BÁO] Không tìm thấy file: {filename}")
            continue

        with open(filepath, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            count_in_file = 0
            for row in reader:
                if len(row) < 5:
                    continue
                try:
                    thoi_gian = row[0].strip()
                    nhiet_do = float(row[1].strip())
                    do_am = float(row[2].strip())
                    gas_raw = int(float(row[3].strip()))
                    dust_raw = int(float(row[4].strip()))

                    all_records.append({
                        "thoi_gian_thuc": thoi_gian,
                        "nhiet_do": nhiet_do,
                        "do_am": do_am,
                        "gas_raw": gas_raw,
                        "dust_raw": dust_raw,
                        "nguon_nhan": filename
                    })
                    count_in_file += 1
                except ValueError:
                    continue
            print(f"[+] Đã đọc {count_in_file:4d} dòng từ file: {filename}")

    # Đảm bảo sắp xếp tăng dần theo cột thời gian thực (YYYY-MM-DD HH:MM:SS)
    all_records.sort(key=lambda x: x["thoi_gian_thuc"])
    return all_records


def push_len_supabase(records, api_key):
    endpoint = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
    batch_size = 500
    total = len(records)
    pushed = 0

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    print(f"\n[*] Bắt đầu đẩy tổng cộng {total} dòng lên bảng '{TABLE_NAME}' tại Supabase...")

    for i in range(0, total, batch_size):
        batch = records[i:i + batch_size]
        data_bytes = json.dumps(batch).encode('utf-8')
        req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method='POST')

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                pushed += len(batch)
                print(f"   -> Đã push thành công: {pushed}/{total} dòng ({pushed * 100 // total}%)")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='ignore')
            print(f"\n[LỖI HTTP {e.code}] {err_body}")
            return False
        except Exception as ex:
            print(f"\n[LỖI KẾT NỐI] {ex}")
            return False

    print(f"\n[HOÀN TẤT] Đã đẩy toàn bộ {pushed}/{total} dòng dữ liệu lên Supabase theo đúng thứ tự ngày!")
    return True


if __name__ == "__main__":
    records = doc_va_sap_xep_du_lieu()
    print(f"[*] Tổng số dòng hợp lệ chuẩn bị push: {len(records)} dòng.")
    if records:
        push_len_supabase(records, SUPABASE_KEY)

