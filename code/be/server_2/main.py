"""
File: code/be/server_2/main.py (Phiên bản v4.0 - Full Auth, Clean Sensor Explorer, Weather-Style Hourly Forecast & Fan Activity Log Dashboard)
Chức năng toàn diện của Server 2:
  1. Đăng ký tài khoản mới (`POST /api/v1/auth/register`) với đầy đủ các trường bệnh án hô hấp/phế quản & tự động nội suy ngưỡng an toàn nếu bỏ trống.
  2. Đăng nhập (`POST /api/v1/auth/login`), xem & cập nhật hồ sơ (`GET/PUT /api/v1/users/{user_id}/profile`).
  3. Theo dõi chi tiết các trường chỉ số đã được Clean (`GET /api/v1/sensors/clean-feed`) kèm thông điệp cảnh báo động bám sát từng dòng.
  4. Widget Dự báo chất lượng không khí theo giờ kiểu Thời tiết (`GET /api/v1/dashboard/hourly-forecast`): Cột đầu tiên luôn là "Bây giờ" khớp 100% thực tế, nối tiếp là 12 SA, 1 SA, 2 SA... [cite: 12]
  5. Dashboard Nhật ký Bật/Tắt Quạt (`GET /api/v1/fan/history-dashboard`): Theo dõi quạt đã bật lúc nào, tắt lúc nào, bật trong bao lâu (phút/giây), giảm được bao nhiêu µg/m³ bụi PM2.5.
"""

import hashlib
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Deque
import pandas as pd
from sqlalchemy import text
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai_engine import (
    PersonalizedAirAIEngine,
    get_user_engine,
    get_clean_engine,
    calculate_personalized_safe_thresholds,
    VN_TZ,
)

app = FastAPI(
    title="AuraTrack - Server 2 (Auth, Personalized Medical AI, Hourly Forecast & Fan Activity Dashboard)",
    version="4.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ai_engine = PersonalizedAirAIEngine()

# 1. Bộ đệm In-Memory Cache giúp truy xuất < 1ms
USER_PROFILES_CACHE: Dict[str, Dict[str, Any]] = {}
LATEST_CLEAN_SENSOR_CACHE: Optional[Dict[str, Any]] = None
UNFILTERED_HISTORY_BUFFER: Deque[Dict[str, Any]] = deque(maxlen=240)

# 2. Trạng thái Bộ điều khiển Quạt ESP32 & Phiên bật quạt hiện tại
ESP32_FAN_CONTROLLER_STATE: Dict[str, Any] = {
    "active_user_id": "USR_00002",
    "fan_command": "OFF",
    "is_filtering_locked": False,
    "safe_streak_count": 0,
    "active_log_id": None,
    "active_fan_started_at": None,
    "last_ai_evaluation": None,
    "updated_at": None,
}

# =====================================================================
# KHỞI TẠO BẢNG DATABASE CHO TÀI KHOẢN ĐĂNG NHẬP & NHẬT KÝ HOẠT ĐỘNG QUẠT
# =====================================================================
INIT_EXTRA_TABLES_SQL = """
ALTER TABLE public.user_medical_profiles ADD COLUMN IF NOT EXISTS username VARCHAR(100) UNIQUE;
ALTER TABLE public.user_medical_profiles ADD COLUMN IF NOT EXISTS password_hash VARCHAR(256);
ALTER TABLE public.user_medical_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

CREATE TABLE IF NOT EXISTS public.fan_activity_logs (
    log_id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(30),
    ho_ten VARCHAR(120),
    ten_benh VARCHAR(150),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_seconds INT,
    duration_minutes FLOAT,
    trigger_reason TEXT,
    trigger_pm25_ugm3 FLOAT,
    trigger_gas_ppm FLOAT,
    trigger_risk_score FLOAT,
    end_pm25_ugm3 FLOAT,
    end_gas_ppm FLOAT,
    status VARCHAR(20) DEFAULT 'RUNNING'
);
CREATE INDEX IF NOT EXISTS idx_fan_logs_started_at ON public.fan_activity_logs(started_at DESC);
"""


def _hash_password(raw_password: str) -> str:
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


def ensure_auth_and_fan_tables():
    engine = get_user_engine()
    with engine.begin() as conn:
        for stmt in INIT_EXTRA_TABLES_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))


# =====================================================================
# SCHEMAS ĐĂNG KÝ / ĐĂNG NHẬP TÀI KHOẢN NGƯỜI DÙNG CHI TIẾT
# =====================================================================
class UserRegisterRequest(BaseModel):
    username: str = Field(..., description="Tên đăng nhập hoặc Email (duy nhất)")
    password: str = Field(..., min_length=4, description="Mật khẩu đăng nhập")
    ho_ten: str = Field(..., description="Họ và tên người dùng")
    gioi_tinh: str = Field("Nam", description="Nam hoặc Nữ")
    tuoi: int = Field(30, ge=1, le=110)
    chieu_cao_cm: float = Field(168.0, ge=50.0, le=230.0)
    can_nang_kg: float = Field(62.0, ge=10.0, le=200.0)
    ma_benh_icd10: Optional[str] = Field("J45.0", description="Mã bệnh ICD-10 (VD: J45.0, J44.9, J20.9, Z00.0)")
    ten_benh_ho_hap_phe_quan: str = Field("Hen phế quản dị ứng", description="Tên bệnh lý hô hấp/phế quản")
    nhom_benh: Optional[str] = Field("Hen phế quản (Asthma)", description="Nhóm bệnh hô hấp")
    muc_do_benh_1_4: int = Field(2, ge=1, le=4, description="Mức độ bệnh từ 1 (Nhẹ/Khỏe mạnh) đến 4 (Rất nặng)")
    benh_kem_theo: Optional[str] = Field("Không", description="Bệnh lý nền kèm theo (VD: Viêm mũi dị ứng, Tăng huyết áp)")
    chi_so_fev1_phan_tram: float = Field(78.0, ge=25.0, le=120.0, description="Chỉ số dung tích thở ra gắng sức FEV1 (%)")
    spo2_nen_phan_tram: float = Field(97.0, ge=85.0, le=100.0, description="Chỉ số SpO2 nền (%)")
    nhip_tho_phut: int = Field(18, ge=10, le=40, description="Nhịp thở lúc nghỉ (lần/phút)")
    do_nhay_cam_phe_quan_0_100: int = Field(65, ge=0, le=100, description="Độ nhạy cảm phế quản với bụi/khí (0 - 100)")
    su_dung_binh_xit_gian_phe_quan: int = Field(1, ge=0, le=1, description="Có dùng bình xịt giãn phế quản không (0 hoặc 1)")
    so_dot_cap_trong_nam: int = Field(1, ge=0, le=20, description="Số đợt cấp khởi phát trong 1 năm qua")
    tinh_trang_hut_thuoc: Optional[str] = Field("Không hút", description="Không hút / Hút thụ động / Đang hút / Đã cai")
    nguong_pm25_an_toan_ugm3: Optional[float] = Field(None, description="Ngưỡng PM2.5 an toàn cá nhân (Bỏ trống hệ thống tự tính)")
    nguong_gas_an_toan_ppm: Optional[float] = Field(None, description="Ngưỡng Gas an toàn cá nhân (Bỏ trống hệ thống tự tính)")


class UserLoginRequest(BaseModel):
    username_or_userid: str = Field(..., description="Nhập username đã đăng ký hoặc mã user_id mẫu (VD: USR_00002)")
    password: Optional[str] = Field("", description="Mật khẩu (đối với tài khoản mẫu USR_00001..USR_05000 có thể để trống)")


# =====================================================================
# HÀM HỖ TRỢ CACHE & GHI NHẬT KÝ HOẠT ĐỘNG QUẠT (FAN ACTIVITY TRACKER)
# =====================================================================
def get_cached_user_profile(user_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh and user_id in USER_PROFILES_CACHE:
        return USER_PROFILES_CACHE[user_id]

    engine = get_user_engine()
    df = pd.read_sql(
        text("SELECT * FROM public.user_medical_profiles WHERE user_id = :uid OR username = :uid LIMIT 1"),
        engine,
        params={"uid": user_id}
    )
    if df.empty:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tài khoản hoặc hồ sơ bệnh nhân '{user_id}'")

    profile = df.iloc[0].to_dict()
    profile.pop("password_hash", None)
    USER_PROFILES_CACHE[profile["user_id"]] = profile
    return profile


def get_cached_latest_clean_sensor(force_db: bool = False) -> Dict[str, Any]:
    global LATEST_CLEAN_SENSOR_CACHE
    if not force_db and LATEST_CLEAN_SENSOR_CACHE is not None:
        return LATEST_CLEAN_SENSOR_CACHE

    engine = get_clean_engine()
    df = pd.read_sql(
        text("SELECT * FROM public.sensor_data_clean ORDER BY raw_sensor_id DESC LIMIT 1"),
        engine
    )
    if df.empty:
        raise HTTPException(status_code=404, detail="Bảng sensor_data_clean chưa có dữ liệu.")

    row_dict = df.iloc[0].to_dict()
    for k, v in row_dict.items():
        if hasattr(v, "isoformat"):
            row_dict[k] = v.isoformat()
    LATEST_CLEAN_SENSOR_CACHE = row_dict
    return LATEST_CLEAN_SENSOR_CACHE


def _record_fan_turn_on(ai_eval: Dict[str, Any], clean_sensor: Dict[str, Any]):
    """Ghi nhận thời điểm bắt đầu BẬT quạt vào bảng `fan_activity_logs`."""
    now_vn = datetime.now(VN_TZ)
    ESP32_FAN_CONTROLLER_STATE["active_fan_started_at"] = now_vn
    try:
        engine = get_user_engine()
        with engine.begin() as conn:
            new_log_id = conn.execute(
                text("""
                    INSERT INTO public.fan_activity_logs (
                        user_id, ho_ten, ten_benh, started_at, trigger_reason,
                        trigger_pm25_ugm3, trigger_gas_ppm, trigger_risk_score, status
                    )
                    VALUES (:uid, :ht, :tb, :st, :reason, :pm25, :gas, :score, 'RUNNING')
                    RETURNING log_id;
                """),
                {
                    "uid": ai_eval["user_id"],
                    "ht": ai_eval["ho_ten"],
                    "tb": ai_eval["ten_benh"],
                    "st": now_vn,
                    "reason": ai_eval["medical_warning_message"],
                    "pm25": float(clean_sensor["pm25_ugm3"]),
                    "gas": float(clean_sensor["gas_ppm"]),
                    "score": float(ai_eval["personalized_risk_score_0_100"]),
                }
            ).scalar()
            ESP32_FAN_CONTROLLER_STATE["active_log_id"] = int(new_log_id)
    except Exception as e:
        print(f"[Fan Log Start Warning]: {e}")


def _record_fan_turn_off(clean_sensor: Dict[str, Any]):
    """Cập nhật thời điểm TẮT quạt và tổng thời gian quạt đã chạy (giây/phút) vào `fan_activity_logs`."""
    now_vn = datetime.now(VN_TZ)
    log_id = ESP32_FAN_CONTROLLER_STATE.get("active_log_id")
    started_at = ESP32_FAN_CONTROLLER_STATE.get("active_fan_started_at")

    if log_id and started_at:
        dur_secs = max(1, int((now_vn - started_at).total_seconds()))
        dur_mins = round(dur_secs / 60.0, 2)
        try:
            engine = get_user_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE public.fan_activity_logs
                        SET ended_at = :et,
                            duration_seconds = :ds,
                            duration_minutes = :dm,
                            end_pm25_ugm3 = :pm25,
                            end_gas_ppm = :gas,
                            status = 'COMPLETED'
                        WHERE log_id = :lid
                    """),
                    {
                        "et": now_vn,
                        "ds": dur_secs,
                        "dm": dur_mins,
                        "pm25": float(clean_sensor["pm25_ugm3"]),
                        "gas": float(clean_sensor["gas_ppm"]),
                        "lid": log_id,
                    }
                )
        except Exception as e:
            print(f"[Fan Log Stop Warning]: {e}")

    ESP32_FAN_CONTROLLER_STATE["active_log_id"] = None
    ESP32_FAN_CONTROLLER_STATE["active_fan_started_at"] = None


def update_hysteresis_fan_state(ai_eval: Dict[str, Any], clean_sensor: Dict[str, Any]) -> Dict[str, Any]:
    """Điều khiển Vùng trễ (Hysteresis) kết hợp tự động ghi lịch sử thời gian bật quạt."""
    prev_cmd = ESP32_FAN_CONTROLLER_STATE["fan_command"]
    risk_score = ai_eval["personalized_risk_score_0_100"]
    ai_rec = ai_eval["ai_fan_recommend"]

    if not ESP32_FAN_CONTROLLER_STATE["is_filtering_locked"]:
        if ai_rec == "ON" or risk_score >= 62.0:
            ESP32_FAN_CONTROLLER_STATE["fan_command"] = "ON"
            ESP32_FAN_CONTROLLER_STATE["is_filtering_locked"] = True
            ESP32_FAN_CONTROLLER_STATE["safe_streak_count"] = 0
        else:
            ESP32_FAN_CONTROLLER_STATE["fan_command"] = "OFF"
    else:
        if risk_score <= 46.0 and ai_rec == "OFF":
            ESP32_FAN_CONTROLLER_STATE["safe_streak_count"] += 1
            if ESP32_FAN_CONTROLLER_STATE["safe_streak_count"] >= 3:
                ESP32_FAN_CONTROLLER_STATE["fan_command"] = "OFF"
                ESP32_FAN_CONTROLLER_STATE["is_filtering_locked"] = False
                ESP32_FAN_CONTROLLER_STATE["safe_streak_count"] = 0
        else:
            ESP32_FAN_CONTROLLER_STATE["safe_streak_count"] = 0
            ESP32_FAN_CONTROLLER_STATE["fan_command"] = "ON"

    new_cmd = ESP32_FAN_CONTROLLER_STATE["fan_command"]

    # Phát hiện chuyển trạng thái OFF -> ON hoặc ON -> OFF để lưu nhật ký hoạt động quạt
    if prev_cmd == "OFF" and new_cmd == "ON":
        _record_fan_turn_on(ai_eval, clean_sensor)
    elif prev_cmd == "ON" and new_cmd == "OFF":
        _record_fan_turn_off(clean_sensor)

    if new_cmd == "OFF":
        if not UNFILTERED_HISTORY_BUFFER or UNFILTERED_HISTORY_BUFFER[-1].get("raw_sensor_id") != clean_sensor.get("raw_sensor_id"):
            UNFILTERED_HISTORY_BUFFER.append(clean_sensor)

    ESP32_FAN_CONTROLLER_STATE["last_ai_evaluation"] = ai_eval
    ESP32_FAN_CONTROLLER_STATE["updated_at"] = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return ESP32_FAN_CONTROLLER_STATE


@app.on_event("startup")
def startup_initialize():
    try:
        ensure_auth_and_fan_tables()
        clean_s = get_cached_latest_clean_sensor(force_db=True)
        user_p = get_cached_user_profile(ESP32_FAN_CONTROLLER_STATE["active_user_id"])
        ai_eval = ai_engine.evaluate_user_and_environment(user_p, clean_s)
        update_hysteresis_fan_state(ai_eval, clean_s)
        print("[SERVER 2 v4.0] Đã khởi tạo bảng Auth + Fan Activity Logs và nạp sẵn RAM Cache!")
    except Exception as e:
        print(f"[Startup Notice]: {e}")


# =====================================================================
# 1. NHÓM API ĐĂNG KÝ, ĐĂNG NHẬP & HIỂN THỊ HỒ SƠ CÁ NHÂN
# =====================================================================
@app.post("/api/v1/auth/register")
def register_new_user(req: UserRegisterRequest):
    """
    Đăng ký tài khoản người dùng mới với đầy đủ các trường thông tin y tế cá nhân hóa.
    Nếu người dùng không nhập `nguong_pm25_an_toan_ugm3` hoặc `nguong_gas_an_toan_ppm`,
    hệ thống tự động nội suy chuẩn xác dựa trên mức độ bệnh và độ nhạy cảm phế quản.
    """
    engine = get_user_engine()
    bmi_calc = round(req.can_nang_kg / ((req.chieu_cao_cm / 100.0) ** 2), 1)

    auto_thresh = calculate_personalized_safe_thresholds(
        req.muc_do_benh_1_4,
        req.do_nhay_cam_phe_quan_0_100,
        req.chi_so_fev1_phan_tram,
        req.su_dung_binh_xit_gian_phe_quan
    )
    safe_pm25 = req.nguong_pm25_an_toan_ugm3 if req.nguong_pm25_an_toan_ugm3 is not None else auto_thresh["nguong_pm25_an_toan_ugm3"]
    safe_gas = req.nguong_gas_an_toan_ppm if req.nguong_gas_an_toan_ppm is not None else auto_thresh["nguong_gas_an_toan_ppm"]

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT user_id FROM public.user_medical_profiles WHERE username = :u LIMIT 1"),
            {"u": req.username.strip()}
        ).scalar()
        if exists:
            raise HTTPException(status_code=400, detail=f"Tên đăng nhập '{req.username}' đã tồn tại!")

        total_users = int(conn.execute(text("SELECT COUNT(*) FROM public.user_medical_profiles")).scalar() or 5000)
        new_user_id = f"USR_{total_users + 1:05d}"

        conn.execute(
            text("""
                INSERT INTO public.user_medical_profiles (
                    user_id, username, password_hash, ho_ten, gioi_tinh, tuoi,
                    chieu_cao_cm, can_nang_kg, bmi, ma_benh_icd10, ten_benh_ho_hap_phe_quan,
                    nhom_benh, muc_do_benh_1_4, benh_kem_theo, chi_so_fev1_phan_tram,
                    spo2_nen_phan_tram, nhip_tho_phut, do_nhay_cam_phe_quan_0_100,
                    su_dung_binh_xit_gian_phe_quan, so_dot_cap_trong_nam, tinh_trang_hut_thuoc,
                    nguong_pm25_an_toan_ugm3, nguong_gas_an_toan_ppm
                )
                VALUES (
                    :uid, :uname, :pwh, :ht, :gt, :tuoi,
                    :cc, :cn, :bmi, :icd, :ten_benh,
                    :nhom_benh, :muc_do, :kem_theo, :fev1,
                    :spo2, :nhip_tho, :nhay_cam,
                    :binh_xit, :dot_cap, :hut_thuoc,
                    :pm25_safe, :gas_safe
                )
            """),
            {
                "uid": new_user_id,
                "uname": req.username.strip(),
                "pwh": _hash_password(req.password),
                "ht": req.ho_ten.strip(),
                "gt": req.gioi_tinh,
                "tuoi": req.tuoi,
                "cc": req.chieu_cao_cm,
                "cn": req.can_nang_kg,
                "bmi": bmi_calc,
                "icd": req.ma_benh_icd10,
                "ten_benh": req.ten_benh_ho_hap_phe_quan,
                "nhom_benh": req.nhom_benh,
                "muc_do": req.muc_do_benh_1_4,
                "kem_theo": req.benh_kem_theo,
                "fev1": req.chi_so_fev1_phan_tram,
                "spo2": req.spo2_nen_phan_tram,
                "nhip_tho": req.nhip_tho_phut,
                "nhay_cam": req.do_nhay_cam_phe_quan_0_100,
                "binh_xit": req.su_dung_binh_xit_gian_phe_quan,
                "dot_cap": req.so_dot_cap_trong_nam,
                "hut_thuoc": req.tinh_trang_hut_thuoc,
                "pm25_safe": safe_pm25,
                "gas_safe": safe_gas,
            }
        )

    # Tự động chuyển hệ thống AI sang bảo vệ tài khoản vừa đăng ký
    ESP32_FAN_CONTROLLER_STATE["active_user_id"] = new_user_id
    profile = get_cached_user_profile(new_user_id, force_refresh=True)
    clean_sensor = get_cached_latest_clean_sensor()
    ai_eval = ai_engine.evaluate_user_and_environment(profile, clean_sensor)
    update_hysteresis_fan_state(ai_eval, clean_sensor)

    return {
        "status": "registered_and_activated",
        "message": f"Đăng ký thành công! Hệ thống AI đang bảo vệ tài khoản {new_user_id} ({req.ho_ten}).",
        "user_profile": profile,
        "initial_ai_evaluation": ai_eval
    }


@app.post("/api/v1/auth/login")
def login_user(req: UserLoginRequest):
    """
    Đăng nhập bằng `username` (tài khoản mới tạo) hoặc mã `user_id` có sẵn (VD: `USR_00001`..`USR_05000`).
    Khi đăng nhập thành công, hệ thống tự động đặt người dùng này làm `active_user_id` cho mạch ESP32!
    """
    engine = get_user_engine()
    ident = req.username_or_userid.strip()
    df = pd.read_sql(
        text("SELECT * FROM public.user_medical_profiles WHERE username = :ident OR user_id = :ident LIMIT 1"),
        engine,
        params={"ident": ident}
    )
    if df.empty:
        raise HTTPException(status_code=404, detail="Tài khoản không tồn tại!")

    user_row = df.iloc[0].to_dict()
    stored_hash = user_row.get("password_hash")

    # Nếu tài khoản có mật khẩu thì kiểm tra hash; nếu là 5.000 hồ sơ mẫu ban đầu (password_hash=None) thì cho phép đăng nhập nhanh
    if stored_hash:
        if not req.password or _hash_password(req.password) != stored_hash:
            raise HTTPException(status_code=401, detail="Mật khẩu không chính xác!")

    user_row.pop("password_hash", None)
    uid = user_row["user_id"]
    USER_PROFILES_CACHE[uid] = user_row
    ESP32_FAN_CONTROLLER_STATE["active_user_id"] = uid

    clean_sensor = get_cached_latest_clean_sensor()
    ai_eval = ai_engine.evaluate_user_and_environment(user_row, clean_sensor)
    update_hysteresis_fan_state(ai_eval, clean_sensor)

    return {
        "status": "logged_in",
        "active_user_id": uid,
        "user_profile": user_row,
        "realtime_ai_evaluation": ai_eval
    }


@app.get("/api/v1/users/{user_id}/profile")
def get_user_account_details(user_id: str):
    """Hiển thị toàn bộ 21 trường thông tin chi tiết của tài khoản và hồ sơ bệnh án."""
    profile = get_cached_user_profile(user_id)
    clean_sensor = get_cached_latest_clean_sensor()
    ai_eval = ai_engine.evaluate_user_and_environment(profile, clean_sensor)
    return {
        "user_profile": profile,
        "current_health_status": ai_eval
    }


# =====================================================================
# 2. NHÓM API HIỂN THỊ CHI TIẾT CHỈ SỐ ĐÃ CLEAN & CẢNH BÁO ĐỘNG TƯƠNG ỨNG
# =====================================================================
@app.get("/api/v1/sensors/clean-feed")
def get_clean_sensor_details_feed(
    user_id: Optional[str] = Query(None, description="Mã người dùng để đối chiếu cảnh báo"),
    limit: int = Query(30, ge=1, le=200, description="Số bản ghi Clean gần nhất muốn hiển thị")
):
    """
    Hiển thị chi tiết đầy đủ 19 trường thông tin của bảng `sensor_data_clean`
    (so sánh trực diện giữa giá trị Thô và giá trị Đã làm sạch, PM2.5, Gas, AQI, Heat Index, Trạng thái cảm biến)
    kèm thông điệp cảnh báo cá nhân hóa bám sát chính xác với thông số đang hiển thị trên màn hình.
    """
    target_uid = user_id or ESP32_FAN_CONTROLLER_STATE["active_user_id"]
    profile = get_cached_user_profile(target_uid)

    clean_engine = get_clean_engine()
    df_clean = pd.read_sql(
        text("""
            SELECT clean_id, raw_sensor_id, recorded_at, nguon_nhan,
                   nhiet_do_raw, do_am_raw, gas_raw, dust_raw,
                   temp_clean_c, humidity_clean_pct, gas_raw_clean, dust_raw_clean,
                   pm25_ugm3, gas_ppm, aqi_vn, air_quality_status,
                   heat_index_c, sensor_health_status, is_spike_cleaned, processed_at
            FROM public.sensor_data_clean
            ORDER BY raw_sensor_id DESC
            LIMIT :lim
        """),
        clean_engine,
        params={"lim": limit}
    )
    if df_clean.empty:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu trong sensor_data_clean.")

    records = df_clean.to_dict(orient="records")
    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()

    latest_clean = records[0]
    global LATEST_CLEAN_SENSOR_CACHE
    LATEST_CLEAN_SENSOR_CACHE = latest_clean

    # Đánh giá AI khớp 100% với bản ghi `latest_clean` đang hiển thị ở đầu bảng
    ai_eval = ai_engine.evaluate_user_and_environment(profile, latest_clean)
    fan_state = update_hysteresis_fan_state(ai_eval, latest_clean)

    return {
        "active_user": {
            "user_id": profile["user_id"],
            "ho_ten": profile["ho_ten"],
            "ten_benh": profile["ten_benh_ho_hap_phe_quan"],
            "nguong_pm25_an_toan_ugm3": profile["nguong_pm25_an_toan_ugm3"],
            "nguong_gas_an_toan_ppm": profile["nguong_gas_an_toan_ppm"],
        },
        "displayed_snapshot_alert": {
            "matched_raw_sensor_id": latest_clean["raw_sensor_id"],
            "matched_recorded_at": latest_clean["recorded_at"],
            "badge_color": ai_eval["badge_color"],
            "alert_title": ai_eval["alert_title"],
            "medical_warning_message": ai_eval["medical_warning_message"],
            "personalized_risk_score_0_100": ai_eval["personalized_risk_score_0_100"],
            "fan_command": fan_state["fan_command"],
            "estimated_filter_minutes": ai_eval["estimated_filter_minutes"],
        },
        "latest_clean_detail_card": {
            "raw_sensor_id": latest_clean["raw_sensor_id"],
            "recorded_at": latest_clean["recorded_at"],
            "session_source": latest_clean["nguon_nhan"],
            "temperature": {
                "raw_c": latest_clean["nhiet_do_raw"],
                "clean_c": latest_clean["temp_clean_c"],
                "heat_index_feels_like_c": latest_clean["heat_index_c"],
            },
            "humidity": {
                "raw_pct": latest_clean["do_am_raw"],
                "clean_pct": latest_clean["humidity_clean_pct"],
            },
            "particulate_matter_pm25": {
                "dust_adc_raw": latest_clean["dust_raw"],
                "dust_adc_ema_clean": latest_clean["dust_raw_clean"],
                "pm25_ugm3": latest_clean["pm25_ugm3"],
                "user_safe_limit_ugm3": profile["nguong_pm25_an_toan_ugm3"],
            },
            "toxic_gas": {
                "gas_adc_raw": latest_clean["gas_raw"],
                "gas_adc_ema_clean": latest_clean["gas_raw_clean"],
                "gas_ppm": latest_clean["gas_ppm"],
                "user_safe_limit_ppm": profile["nguong_gas_an_toan_ppm"],
            },
            "overall_air_index": {
                "aqi_vn": latest_clean["aqi_vn"],
                "air_quality_status": latest_clean["air_quality_status"],
                "sensor_health_status": latest_clean["sensor_health_status"],
                "is_spike_cleaned": latest_clean["is_spike_cleaned"],
            }
        },
        "clean_history_table": records
    }


# =====================================================================
# 3. WIDGET DỰ BÁO THEO GIỜ KIỂU THỜI TIẾT ("BÂY GIỜ", "12 SA", "1 SA"...)
# =====================================================================
@app.get("/api/v1/dashboard/hourly-forecast")
def get_weather_style_hourly_forecast(
    user_id: Optional[str] = Query(None, description="Mã người dùng"),
    hours: int = Query(5, ge=3, le=12, description="Số giờ tiếp theo cần hiển thị trên thanh ngang")
):
    """
    Trả về dữ liệu cho Widget Dự báo theo giờ giống hệt ảnh mẫu Thời tiết [cite: 12]:
      - Cột 0: Luôn là "Bây giờ" (khớp chính xác 100% với chỉ số Clean hiện tại).
      - Cột 1..N: Các mốc giờ chẵn tiếp theo ("12 SA", "1 SA", "2 SA", "3 SA", "4 SA"...) [cite: 12].
    """
    target_uid = user_id or ESP32_FAN_CONTROLLER_STATE["active_user_id"]
    profile = get_cached_user_profile(target_uid)
    clean_sensor = get_cached_latest_clean_sensor(force_db=True)

    return ai_engine.build_weather_style_hourly_forecast(
        current_clean_sensor=clean_sensor,
        user_profile=profile,
        num_hours_ahead=hours,
        unfiltered_history_buffer=list(UNFILTERED_HISTORY_BUFFER)
    )


# =====================================================================
# 4. DASHBOARD THEO DÕI LỊCH SỬ BẬT/TẮT QUẠT & THỜI LƯỢNG CHẠY QUẠT
# =====================================================================
@app.get("/api/v1/fan/history-dashboard")
def get_fan_activity_dashboard(limit: int = Query(50, ge=1, le=200)):
    """
    Dashboard giúp người dùng theo dõi chi tiết:
      - Hiện tại quạt đang BẬT hay TẮT? Nếu đang BẬT thì đã chạy liên tục được bao nhiêu phút/giây?
      - Tổng số lần quạt đã kích hoạt trong ngày, tổng số phút quạt đã chạy lọc không khí.
      - Danh sách từng lần quạt bật: Bật lúc mấy giờ, tắt lúc mấy giờ, chạy bao lâu, giảm được bao nhiêu µg/m³ bụi.
    """
    now_vn = datetime.now(VN_TZ)
    current_session_seconds = 0
    if ESP32_FAN_CONTROLLER_STATE["fan_command"] == "ON" and ESP32_FAN_CONTROLLER_STATE.get("active_fan_started_at"):
        current_session_seconds = max(1, int((now_vn - ESP32_FAN_CONTROLLER_STATE["active_fan_started_at"]).total_seconds()))

    engine = get_user_engine()
    df_logs = pd.read_sql(
        text("""
            SELECT log_id, user_id, ho_ten, ten_benh, started_at, ended_at,
                   duration_seconds, duration_minutes, trigger_reason,
                   trigger_pm25_ugm3, trigger_gas_ppm, trigger_risk_score,
                   end_pm25_ugm3, end_gas_ppm, status
            FROM public.fan_activity_logs
            ORDER BY started_at DESC
            LIMIT :lim
        """),
        engine,
        params={"lim": limit}
    )

    logs_list = []
    total_completed_seconds = 0
    for _, row in df_logs.iterrows():
        item = row.to_dict()
        st = pd.to_datetime(item["started_at"]).tz_convert(VN_TZ) if item["started_at"] is not None else None
        et = pd.to_datetime(item["ended_at"]).tz_convert(VN_TZ) if item["ended_at"] is not None else None

        if item["status"] == "RUNNING" and st is not None:
            live_secs = max(1, int((now_vn - st).total_seconds()))
            item["duration_seconds"] = live_secs
            item["duration_minutes"] = round(live_secs / 60.0, 2)
            item["duration_human_readable"] = f"Đang chạy ({live_secs // 60} phút {live_secs % 60} giây)"
        else:
            secs = int(item["duration_seconds"] or 0)
            total_completed_seconds += secs
            item["duration_human_readable"] = f"{secs // 60} phút {secs % 60} giây"

        item["started_at_vn"] = st.strftime("%H:%M:%S %d/%m/%Y") if st else None
        item["ended_at_vn"] = et.strftime("%H:%M:%S %d/%m/%Y") if et else "Đang lọc..."
        for k, v in item.items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
        logs_list.append(item)

    total_Runtime_minutes = round((total_completed_seconds + current_session_seconds) / 60.0, 1)

    return {
        "current_fan_status": {
            "fan_command": ESP32_FAN_CONTROLLER_STATE["fan_command"],
            "is_filtering_locked": ESP32_FAN_CONTROLLER_STATE["is_filtering_locked"],
            "active_user_id": ESP32_FAN_CONTROLLER_STATE["active_user_id"],
            "current_run_duration_seconds": current_session_seconds,
            "current_run_duration_label": (
                f"Đang bật lọc ({current_session_seconds // 60} phút {current_session_seconds % 60} giây)"
                if ESP32_FAN_CONTROLLER_STATE["fan_command"] == "ON" else "Đang tắt (Không khí an toàn)"
            ),
        },
        "summary_statistics": {
            "total_activation_events": len(logs_list),
            "total_filtering_minutes": total_Runtime_minutes,
        },
        "fan_activation_timeline": logs_list
    }


# =====================================================================
# 5. ENDPOINT TỔNG HỢP (ALL-IN-ONE DASHBOARD) & GIAO TIẾP ESP32 / SERVER 1
# =====================================================================
@app.get("/api/v1/dashboard/personalized-analysis")
def get_full_personalized_dashboard(user_id: Optional[str] = Query(None, description="Mã người dùng")):
    """API All-in-One trả về trọn bộ Hồ sơ + Chỉ số Clean + Cảnh báo + Dự báo theo giờ + Lịch sử Quạt."""
    target_uid = user_id or ESP32_FAN_CONTROLLER_STATE["active_user_id"]
    ESP32_FAN_CONTROLLER_STATE["active_user_id"] = target_uid

    user_profile = get_cached_user_profile(target_uid)
    clean_sensor = get_cached_latest_clean_sensor(force_db=True)

    ai_eval = ai_engine.evaluate_user_and_environment(user_profile, clean_sensor)
    fan_state = update_hysteresis_fan_state(ai_eval, clean_sensor)
    weather_forecast = ai_engine.build_weather_style_hourly_forecast(
        current_clean_sensor=clean_sensor,
        user_profile=user_profile,
        num_hours_ahead=5,
        unfiltered_history_buffer=list(UNFILTERED_HISTORY_BUFFER)
    )
    fan_dashboard = get_fan_activity_dashboard(limit=15)

    return {
        "user_medical_profile": user_profile,
        "realtime_clean_air": clean_sensor,
        "personalized_ai_decision": ai_eval,
        "weather_style_hourly_forecast": weather_forecast,
        "fan_activity_dashboard": fan_dashboard,
        "esp32_fan_controller": {
            "fan_command": fan_state["fan_command"],
            "is_filtering_locked": fan_state["is_filtering_locked"],
            "safe_streak_count": fan_state["safe_streak_count"]
        }
    }


@app.post("/api/v1/ai/evaluate-realtime")
def receive_realtime_clean_from_server1(payload: Dict[str, Any]):
    global LATEST_CLEAN_SENSOR_CACHE
    clean_sensor = payload.get("clean_sensor")
    if clean_sensor:
        LATEST_CLEAN_SENSOR_CACHE = clean_sensor
    else:
        clean_sensor = get_cached_latest_clean_sensor(force_db=True)

    user_id = ESP32_FAN_CONTROLLER_STATE["active_user_id"]
    user_profile = get_cached_user_profile(user_id)

    ai_eval = ai_engine.evaluate_user_and_environment(user_profile, clean_sensor)
    controller_state = update_hysteresis_fan_state(ai_eval, clean_sensor)
    return {
        "status": "evaluated_in_memory",
        "fan_command_for_esp32": controller_state["fan_command"],
        "ai_evaluation": ai_eval
    }


@app.get("/api/v1/esp32/command")
def get_command_for_esp32(user_id: Optional[str] = Query(None, description="Mã người dùng cần bảo vệ")):
    if user_id and user_id != ESP32_FAN_CONTROLLER_STATE["active_user_id"]:
        ESP32_FAN_CONTROLLER_STATE["active_user_id"] = user_id
        clean_sensor = get_cached_latest_clean_sensor()
        user_profile = get_cached_user_profile(user_id)
        ai_eval = ai_engine.evaluate_user_and_environment(user_profile, clean_sensor)
        update_hysteresis_fan_state(ai_eval, clean_sensor)

    if ESP32_FAN_CONTROLLER_STATE["last_ai_evaluation"] is None:
        clean_sensor = get_cached_latest_clean_sensor()
        user_profile = get_cached_user_profile(ESP32_FAN_CONTROLLER_STATE["active_user_id"])
        ai_eval = ai_engine.evaluate_user_and_environment(user_profile, clean_sensor)
        update_hysteresis_fan_state(ai_eval, clean_sensor)

    ai_eval = ESP32_FAN_CONTROLLER_STATE["last_ai_evaluation"]
    fan_cmd = ESP32_FAN_CONTROLLER_STATE["fan_command"]

    return {
        "fan_command": fan_cmd,
        "relay_gpio_level": 1 if fan_cmd == "ON" else 0,
        "risk_level": ai_eval["risk_level"],
        "personalized_risk_score": ai_eval["personalized_risk_score_0_100"],
        "estimated_filter_minutes": ai_eval["estimated_filter_minutes"],
        "active_user_id": ESP32_FAN_CONTROLLER_STATE["active_user_id"],
        "reason": ai_eval["medical_warning_message"]
    }
