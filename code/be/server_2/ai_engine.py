"""
File: code/be/server_2/ai_engine.py (Phiên bản v4.0 - Hourly Weather-Style Forecast & Safe Threshold Auto-Calculator)
Chức năng:
  1. Suy luận phân cấp 4 tầng (Risk Score 0-100 -> Risk Level 0/1/2 -> Fan Command ON/OFF -> Hurdle Duration).
  2. Tự động tính toán Ngưỡng an toàn PM2.5 & Gas cá nhân hóa cho người dùng đăng ký mới (nếu người dùng không tự nhập).
  3. Dự báo chất lượng không khí theo từng mốc giờ thực tế kiểu ứng dụng Thời tiết (Apple Weather style):
     - Luôn có cột đầu tiên là "Bây giờ" (khớp chính xác 100% với chỉ số Clean hiện tại).
     - Các cột tiếp theo tự động nhảy theo giờ thực tế Việt Nam (UTC+7): VD đang là 23:14 -> "12 SA", "1 SA", "2 SA", "3 SA", "4 SA", "5 SA" [cite: 12].
     - Sinh câu tóm tắt xu hướng tự nhiên (Summary Banner) ở phía trên bảng dự báo [cite: 12].
"""

import os
import json
import joblib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from train_models import (
    BASE_FEATURES,
    META_FEATURES,
    MODEL_DIR,
    run_training_pipeline,
    _norm_url,
)

# Hỗ trợ tìm file .env linh hoạt dù đặt trong `be/.env` hay ở thư mục gốc của repo
CURRENT_DIR = Path(__file__).resolve().parent
for env_candidate in [CURRENT_DIR.parent / ".env", CURRENT_DIR.parent.parent / ".env", CURRENT_DIR.parent.parent.parent / ".env"]:
    if env_candidate.exists():
        load_dotenv(dotenv_path=env_candidate)
        break
else:
    load_dotenv()

CLEAN_DB_URL = _norm_url(os.getenv("CLEAN_DATABASE_URL", os.getenv("RAW_DATABASE_URL", "")).strip())
USER_DB_URL = _norm_url(os.getenv("USER_DATABASE_URL", CLEAN_DB_URL).strip())

VN_TZ = timezone(timedelta(hours=7))


def get_clean_engine():
    return create_engine(CLEAN_DB_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)


def get_user_engine():
    return create_engine(USER_DB_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)


def format_vn_hour_label(dt_vn: datetime) -> str:
    """Chuyển đổi giờ sang định dạng '12 SA', '1 SA', ..., '12 CH', '1 CH' giống hệt ảnh mẫu Thời tiết [cite: 12]."""
    h = dt_vn.hour
    if h == 0:
        return "12 SA"
    elif h < 12:
        return f"{h} SA"
    elif h == 12:
        return "12 CH"
    else:
        return f"{h - 12} CH"


def calculate_personalized_safe_thresholds(
    muc_do_benh_1_4: int,
    do_nhay_cam_phe_quan_0_100: int,
    chi_so_fev1_phan_tram: float,
    su_dung_binh_xit: int
) -> Dict[str, float]:
    """
    Tự động nội suy ngưỡng chịu đựng an toàn PM2.5 (µg/m³) và Gas (ppm)
    dựa trên mức độ bệnh hô hấp và độ nhạy cảm phế quản khi người dùng đăng ký tài khoản mới.
    """
    base_pm25 = 50.0 - (muc_do_benh_1_4 * 6.5) - (do_nhay_cam_phe_quan_0_100 * 0.18)
    if chi_so_fev1_phan_tram < 70.0:
        base_pm25 -= (70.0 - chi_so_fev1_phan_tram) * 0.15
    if su_dung_binh_xit == 1:
        base_pm25 -= 2.5

    safe_pm25 = round(float(np.clip(base_pm25, 12.0, 50.0)), 1)

    base_gas = 140.0 - (muc_do_benh_1_4 * 18.0) - (do_nhay_cam_phe_quan_0_100 * 0.45)
    if su_dung_binh_xit == 1:
        base_gas -= 8.0
    safe_gas = round(float(np.clip(base_gas, 35.0, 140.0)), 1)

    return {
        "nguong_pm25_an_toan_ugm3": safe_pm25,
        "nguong_gas_an_toan_ppm": safe_gas
    }


class PersonalizedAirAIEngine:
    def __init__(self):
        self.score_regressor = None
        self.risk_classifier = None
        self.fan_classifier = None
        self.duration_regressor = None
        self.model_metrics: Dict[str, Any] = {}
        self._ensure_models_ready()

    def _ensure_models_ready(self):
        score_path = MODEL_DIR / "score_voting_reg.joblib"
        clf_path = MODEL_DIR / "risk_voting_clf.joblib"
        fan_path = MODEL_DIR / "fan_voting_clf.joblib"
        reg_path = MODEL_DIR / "duration_hurdle_reg.joblib"
        metrics_path = MODEL_DIR / "metrics_report.json"

        if score_path.exists() and clf_path.exists() and fan_path.exists() and reg_path.exists():
            try:
                self.score_regressor = joblib.load(score_path)
                self.risk_classifier = joblib.load(clf_path)
                self.fan_classifier = joblib.load(fan_path)
                self.duration_regressor = joblib.load(reg_path)
                if metrics_path.exists():
                    with open(metrics_path, "r", encoding="utf-8") as f:
                        self.model_metrics = json.load(f)
                print("[AI ENGINE v4.0] Đã nạp sẵn 4 mô hình phân cấp vào RAM!")
                return
            except Exception as e:
                print(f"[AI ENGINE] Khác biệt phiên bản model ({e}). Tự động huấn luyện lại...")

        self.train_personalized_models()

    def train_personalized_models(self) -> Dict[str, Any]:
        self.model_metrics = run_training_pipeline(verbose=True)
        self.score_regressor = joblib.load(MODEL_DIR / "score_voting_reg.joblib")
        self.risk_classifier = joblib.load(MODEL_DIR / "risk_voting_clf.joblib")
        self.fan_classifier = joblib.load(MODEL_DIR / "fan_voting_clf.joblib")
        self.duration_regressor = joblib.load(MODEL_DIR / "duration_hurdle_reg.joblib")
        return self.model_metrics

    def evaluate_user_and_environment(self, user_profile: Dict[str, Any], clean_sensor: Dict[str, Any]) -> Dict[str, Any]:
        pm25 = float(clean_sensor["pm25_ugm3"])
        gas = float(clean_sensor["gas_ppm"])
        temp = float(clean_sensor["temp_clean_c"])
        hum = float(clean_sensor["humidity_clean_pct"])

        safe_pm25 = float(user_profile["nguong_pm25_an_toan_ugm3"])
        safe_gas = float(user_profile["nguong_gas_an_toan_ppm"])

        base_row = pd.DataFrame([{
            "tuoi": int(user_profile["tuoi"]),
            "bmi": float(user_profile["bmi"]),
            "muc_do_benh_1_4": int(user_profile["muc_do_benh_1_4"]),
            "chi_so_fev1_phan_tram": float(user_profile["chi_so_fev1_phan_tram"]),
            "spo2_nen_phan_tram": float(user_profile["spo2_nen_phan_tram"]),
            "nhip_tho_phut": int(user_profile["nhip_tho_phut"]),
            "do_nhay_cam_phe_quan_0_100": int(user_profile["do_nhay_cam_phe_quan_0_100"]),
            "su_dung_binh_xit_gian_phe_quan": int(user_profile["su_dung_binh_xit_gian_phe_quan"]),
            "so_dot_cap_trong_nam": int(user_profile["so_dot_cap_trong_nam"]),
            "nguong_pm25_an_toan_ugm3": safe_pm25,
            "nguong_gas_an_toan_ppm": safe_gas,
            "nhiet_do_moi_truong_c": temp,
            "do_am_moi_truong_pct": hum,
            "pm25_quy_doi_ugm3": pm25,
            "gas_quy_doi_ppm": gas,
            "pm25_exceed_ratio": pm25 / (safe_pm25 + 1e-3),
            "gas_exceed_ratio": gas / (safe_gas + 1e-3),
        }])[BASE_FEATURES]

        pred_score = float(np.clip(self.score_regressor.predict(base_row)[0], 0.0, 100.0))
        personalized_risk_score = round(pred_score, 1)

        meta_row = base_row.copy()
        meta_row["pred_risk_score"] = pred_score
        meta_row = meta_row[META_FEATURES]

        risk_level = int(self.risk_classifier.predict(meta_row)[0])

        if risk_level == 2:
            fan_ai_decision = 1
        elif risk_level == 0:
            fan_ai_decision = 0
        else:
            fan_ai_decision = int(self.fan_classifier.predict(meta_row)[0])

        if fan_ai_decision == 1:
            raw_dur = float(self.duration_regressor.predict(meta_row)[0])
            est_duration = int(np.clip(round(raw_dur), 10, 60))
        else:
            est_duration = 0

        disease_name = user_profile["ten_benh_ho_hap_phe_quan"]
        if risk_level == 2:
            badge_color = "RED"
            short_title = "NGUY HẠI CAO CHO ĐƯỜNG HÔ HẤP"
            advice = (
                f"CẢNH BÁO ĐỎ [{disease_name}] (Điểm rủi ro: {personalized_risk_score}/100): "
                f"Nồng độ bụi mịn PM2.5 ({pm25} µg/m³) hoặc Khí gas ({gas} ppm) đang vượt ngưỡng chịu đựng an toàn "
                f"của phế quản bạn ({safe_pm25} µg/m³). Hệ thống đã tự động BẬT QUẠT LỌC dự kiến trong {est_duration} phút."
            )
        elif risk_level == 1:
            badge_color = "YELLOW"
            short_title = "CHÚ Ý THEO DÕI ĐƯỜNG THỞ"
            fan_note = f" Hệ thống đã bật quạt lọc sớm ({est_duration} phút)." if fan_ai_decision == 1 else " Hãy hạn chế vận động mạnh và chuẩn bị sẵn thuốc dự phòng."
            advice = (
                f"CẢNH BÁO VÀNG [{disease_name}] (Điểm rủi ro: {personalized_risk_score}/100): "
                f"Chỉ số không khí hiện tại (PM2.5: {pm25}/{safe_pm25} µg/m³, Nhiệt độ: {temp}°C, Độ ẩm: {hum}%) đang tiệm cận ngưỡng kích ứng phế quản.{fan_note}"
            )
        else:
            badge_color = "GREEN"
            short_title = "MÔI TRƯỜNG AN TOÀN"
            advice = (
                f"AN TOÀN XANH [{disease_name}] (Điểm rủi ro: {personalized_risk_score}/100): "
                f"Chỉ số PM2.5 ({pm25} µg/m³) và Khí gas ({gas} ppm) hoàn toàn thấp hơn ngưỡng kích ứng ({safe_pm25} µg/m³) của bạn."
            )

        return {
            "user_id": user_profile["user_id"],
            "ho_ten": user_profile["ho_ten"],
            "ten_benh": disease_name,
            "muc_do_benh": int(user_profile["muc_do_benh_1_4"]),
            "personalized_risk_score_0_100": personalized_risk_score,
            "risk_level": risk_level,
            "badge_color": badge_color,
            "alert_title": short_title,
            "ai_fan_recommend": "ON" if fan_ai_decision == 1 else "OFF",
            "estimated_filter_minutes": est_duration,
            "medical_warning_message": advice,
            "safe_thresholds": {
                "pm25_ugm3": safe_pm25,
                "gas_ppm": safe_gas
            }
        }

    def build_weather_style_hourly_forecast(
        self,
        current_clean_sensor: Dict[str, Any],
        user_profile: Optional[Dict[str, Any]] = None,
        num_hours_ahead: int = 5,
        unfiltered_history_buffer: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Tạo bảng dự báo theo từng giờ giống hệt giao diện Thời tiết trong ảnh mẫu [cite: 12]:
          - Mốc đầu tiên ("Bây giờ"): Luôn lấy trực tiếp 100% từ `current_clean_sensor` vừa nhận được để đảm bảo độ chính xác tuyệt đối!
          - Các mốc tiếp theo (1..num_hours_ahead giờ tới): Nhãn giờ tự động tính theo đồng hồ thực tế Việt Nam (VD: "12 SA", "1 SA", "2 SA", "3 SA", "4 SA") [cite: 12].
        """
        now_vn = datetime.now(VN_TZ)

        if unfiltered_history_buffer and len(unfiltered_history_buffer) >= 10:
            df_session = pd.DataFrame(unfiltered_history_buffer)
        else:
            clean_engine = get_clean_engine()
            df_hist = pd.read_sql(
                text("""
                    SELECT raw_sensor_id, recorded_at, nguon_nhan, temp_clean_c, humidity_clean_pct, pm25_ugm3, gas_ppm, aqi_vn
                    FROM public.sensor_data_clean
                    ORDER BY raw_sensor_id DESC
                    LIMIT 240
                """),
                clean_engine
            )
            if not df_hist.empty:
                df_hist = df_hist.sort_values("raw_sensor_id").reset_index(drop=True)
                latest_session = df_hist.iloc[-1]["nguon_nhan"]
                df_session = df_hist[df_hist["nguon_nhan"] == latest_session].reset_index(drop=True)
                if len(df_session) < 6:
                    df_session = df_hist.tail(30).reset_index(drop=True)
            else:
                df_session = pd.DataFrame([current_clean_sensor])

        dt_seconds = pd.to_datetime(df_session["recorded_at"]).diff().dt.total_seconds().median() if len(df_session) > 1 else 15.0
        if pd.isna(dt_seconds) or dt_seconds <= 0:
            dt_seconds = 15.0
        samples_per_min = 60.0 / dt_seconds

        def _get_natural_slope_per_min(series: pd.Series, is_pollutant: bool = True) -> float:
            vals = series.values.astype(float)
            if len(vals) < 3:
                return 0.0
            diffs = np.diff(vals)
            if is_pollutant and len(diffs) > 3:
                threshold_nat = np.percentile(diffs, 35)
                nat_diffs = diffs[diffs >= threshold_nat]
                if len(nat_diffs) == 0:
                    nat_diffs = diffs
            else:
                nat_diffs = diffs
            short_s = np.mean(nat_diffs[-min(len(nat_diffs), 12):])
            long_s = np.mean(nat_diffs[-min(len(nat_diffs), 40):])
            return float((0.65 * short_s + 0.35 * long_s) * samples_per_min)

        slope_pm25 = _get_natural_slope_per_min(df_session["pm25_ugm3"], True)
        slope_gas = _get_natural_slope_per_min(df_session["gas_ppm"], True)
        slope_temp = _get_natural_slope_per_min(df_session["temp_clean_c"], False)
        slope_hum = _get_natural_slope_per_min(df_session["humidity_clean_pct"], False)

        # Mốc 0: "Bây giờ" - Khớp chính xác tuyệt đối 100% với bản ghi Clean hiện tại!
        cur_pm25 = round(float(current_clean_sensor["pm25_ugm3"]), 1)
        cur_gas = round(float(current_clean_sensor["gas_ppm"]), 1)
        cur_temp = round(float(current_clean_sensor["temp_clean_c"]), 1)
        cur_hum = round(float(current_clean_sensor["humidity_clean_pct"]), 1)
        cur_aqi = int(current_clean_sensor["aqi_vn"])

        safe_pm25_user = float(user_profile["nguong_pm25_an_toan_ugm3"]) if user_profile else 25.0

        def _get_icon_and_status(pm25_val: float, aqi_val: int) -> Dict[str, str]:
            if pm25_val >= safe_pm25_user * 1.25 or aqi_val > 150:
                return {"icon": "smoke-hazard", "status_short": "Nguy hại", "color": "#EF4444"}
            elif pm25_val >= safe_pm25_user or aqi_val > 100:
                return {"icon": "cloud-warning", "status_short": "Kém", "color": "#F59E0B"}
            elif aqi_val > 50:
                return {"icon": "cloud-normal", "status_short": "Trung bình", "color": "#3B82F6"}
            return {"icon": "sun-clean", "status_short": "Tốt", "color": "#10B981"}

        now_visual = _get_icon_and_status(cur_pm25, cur_aqi)

        hourly_items = [{
            "step_index": 0,
            "time_label": "Bây giờ",
            "exact_timestamp": now_vn.strftime("%H:%M:%S %d/%m/%Y"),
            "is_now": True,
            "temp_c": cur_temp,
            "temp_display": f"{int(round(cur_temp))}°",
            "humidity_pct": cur_hum,
            "pm25_ugm3": cur_pm25,
            "gas_ppm": cur_gas,
            "aqi_vn": cur_aqi,
            "icon_type": now_visual["icon"],
            "status_label": now_visual["status_short"],
            "badge_hex": now_visual["color"],
        }]

        phi = 0.965
        max_future_pm25 = cur_pm25
        for h in range(1, num_hours_ahead + 1):
            target_hour_dt = (now_vn + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
            minutes_ahead = max(15, int((target_hour_dt - now_vn).total_seconds() // 60))
            if h > 1:
                minutes_ahead = h * 60

            damped_steps = sum([phi ** m for m in range(1, min(minutes_ahead, 360) + 1)])
            proj_pm25 = round(float(np.clip(cur_pm25 + slope_pm25 * damped_steps * 0.25, 3.0, 500.0)), 1)
            proj_gas = round(float(np.clip(cur_gas + slope_gas * damped_steps * 0.25, 10.0, 1000.0)), 1)
            proj_temp = round(float(np.clip(cur_temp + slope_temp * damped_steps * 0.15, 14.0, 45.0)), 1)
            proj_hum = round(float(np.clip(cur_hum + slope_hum * damped_steps * 0.15, 20.0, 98.0)), 1)
            proj_aqi = int(round(np.clip((proj_pm25 / 25.0) * 50.0 if proj_pm25 <= 25 else 50 + (proj_pm25 - 25.0) * 2.0, 0, 500)))

            max_future_pm25 = max(max_future_pm25, proj_pm25)
            vis = _get_icon_and_status(proj_pm25, proj_aqi)

            hourly_items.append({
                "step_index": h,
                "time_label": format_vn_hour_label(target_hour_dt),
                "exact_timestamp": target_hour_dt.strftime("%H:00 %d/%m/%Y"),
                "is_now": False,
                "temp_c": proj_temp,
                "temp_display": f"{int(round(proj_temp))}°",
                "humidity_pct": proj_hum,
                "pm25_ugm3": proj_pm25,
                "gas_ppm": proj_gas,
                "aqi_vn": proj_aqi,
                "icon_type": vis["icon"],
                "status_label": vis["status_short"],
                "badge_hex": vis["color"],
            })

        # Tạo dòng tiêu đề tóm tắt phía trên giống ảnh mẫu ("Có mây đêm nay, tiếp tục đến buổi sáng...") [cite: 12]
        if max_future_pm25 >= safe_pm25_user:
            summary_headline = (
                f"Dự báo bụi mịn có xu hướng tích tụ lên mức {max_future_pm25} µg/m³ trong vài giờ tới "
                f"(vượt ngưỡng an toàn {safe_pm25_user} µg/m³) nếu không bật quạt lọc."
            )
        elif hourly_items[-1]["pm25_ugm3"] > cur_pm25 + 1.5:
            summary_headline = (
                f"Chất lượng không khí duy trì mức ổn định đêm nay, bụi mịn tăng nhẹ lên {hourly_items[-1]['pm25_ugm3']} µg/m³."
            )
        else:
            summary_headline = (
                f"Không khí trong lành duy trì ổn định trong {num_hours_ahead} giờ tới. PM2.5 dao động quanh mức {cur_pm25} µg/m³."
            )

        return {
            "summary_headline": summary_headline,
            "current_vn_time": now_vn.strftime("%Y-%m-%d %H:%M:%S"),
            "hourly_strip": hourly_items
        }
