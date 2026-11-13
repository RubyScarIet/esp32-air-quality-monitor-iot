import os
import json
import time
from pathlib import Path
from typing import Dict, Any
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

from sklearn.model_selection import train_test_split
from sklearn.ensemble import (
    RandomForestClassifier,
    RandomForestRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    VotingClassifier,
    VotingRegressor,
)
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
import joblib

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()


def _norm_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


USER_DB_URL = _norm_url(os.getenv("USER_DATABASE_URL", os.getenv("CLEAN_DATABASE_URL", "")).strip())

BASE_FEATURES = [
    "tuoi", "bmi", "muc_do_benh_1_4", "chi_so_fev1_phan_tram", "spo2_nen_phan_tram",
    "nhip_tho_phut", "do_nhay_cam_phe_quan_0_100", "su_dung_binh_xit_gian_phe_quan",
    "so_dot_cap_trong_nam", "nguong_pm25_an_toan_ugm3", "nguong_gas_an_toan_ppm",
    "nhiet_do_moi_truong_c", "do_am_moi_truong_pct", "pm25_quy_doi_ugm3", "gas_quy_doi_ppm",
    "pm25_exceed_ratio", "gas_exceed_ratio"
]

META_FEATURES = BASE_FEATURES + ["pred_risk_score"]


def add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pm25_exceed_ratio"] = df["pm25_quy_doi_ugm3"] / (df["nguong_pm25_an_toan_ugm3"] + 1e-3)
    df["gas_exceed_ratio"] = df["gas_quy_doi_ppm"] / (df["nguong_gas_an_toan_ppm"] + 1e-3)
    return df


def load_training_dataset() -> pd.DataFrame:
    df = None
    if USER_DB_URL:
        try:
            print("[1/6] Đang kết nối tới Supabase USER_DATABASE_URL để tải bảng `ai_training_dataset`...")
            engine = create_engine(USER_DB_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)
            df = pd.read_sql("SELECT * FROM public.ai_training_dataset", engine)
            print(f"      -> Tải từ Cloud DB thành công: {len(df):,} bản ghi.")
        except Exception as e:
            print(f"      [Cảnh báo kết nối DB]: {e}")
            print("      -> Đang chuyển sang tìm file CSV dự phòng...")

    if df is None or df.empty:
        csv_candidates = [
            Path(__file__).resolve().parent / "dataset_y_te_ho_hap_ca_nhan_hoa_5000.csv",
            BASE_DIR / "server_1" / "dataset_y_te_ho_hap_ca_nhan_hoa_5000.csv",
            BASE_DIR / "dataset_y_te_ho_hap_ca_nhan_hoa_5000.csv",
            Path.cwd() / "dataset_y_te_ho_hap_ca_nhan_hoa_5000.csv",
        ]
        for p in csv_candidates:
            if p.exists():
                print(f"[1/6] Đang đọc dữ liệu từ file CSV cục bộ: {p} ...")
                df = pd.read_csv(p, keep_default_na=False)
                break

    if df is None or df.empty:
        raise FileNotFoundError("Không tìm thấy dữ liệu trong bảng `public.ai_training_dataset` hoặc file CSV!")

    return add_engineered_features(df)


def run_training_pipeline(verbose: bool = True) -> Dict[str, Any]:
    start_total_time = time.time()
    if verbose:
        print("=" * 86)
        print("   HUẤN LUYỆN KIẾN TRÚC PHÂN CẤP 4 TẦNG (HIERARCHICAL & HURDLE AI PIPELINE - 80/20)")
        print("=" * 86)

    df = load_training_dataset()

    X = df[BASE_FEATURES]
    y_score = df["diem_nguy_hai_ca_nhan_0_100"].astype(float)
    y_risk = df["muc_canh_bao_ai"].astype(int)
    y_fan = df["lenh_bat_quat_ai"].astype(int)
    y_dur = df["thoi_gian_loc_du_kien_phut"].astype(float)

    if verbose:
        print("\n[2/6] Chia tập dữ liệu 80% Train (4.000 mẫu) - 20% Test (1.000 mẫu) có phân tầng...")

    X_train, X_test, ys_train, ys_test, yr_train, yr_test, yf_train, yf_test, yd_train, yd_test = train_test_split(
        X, y_score, y_risk, y_fan, y_dur, test_size=0.20, random_state=42, stratify=y_risk
    )

    # STAGE 1: Huấn luyện Mô hình Hồi quy Điểm nguy hại liên tục (0 - 100)
    if verbose:
        print("[3/6] Stage 1: Huấn luyện Mô hình Điểm Nguy Hại Liên Tục (0-100) cho Hysteresis...")
    rf_score = RandomForestRegressor(n_estimators=180, max_depth=14, random_state=42, n_jobs=-1)
    gb_score = GradientBoostingRegressor(n_estimators=180, learning_rate=0.07, max_depth=5, random_state=42)
    score_model = VotingRegressor(estimators=[("rf", rf_score), ("gb", gb_score)])
    score_model.fit(X_train, ys_train)

    ys_pred_train = np.clip(score_model.predict(X_train), 0.0, 100.0)
    ys_pred_test = np.clip(score_model.predict(X_test), 0.0, 100.0)

    # Tạo tập đặc trưng tầng 2 (Meta-Features) chứa Điểm nguy hại vừa dự báo
    X_train_meta = X_train.copy()
    X_train_meta["pred_risk_score"] = ys_pred_train
    X_test_meta = X_test.copy()
    X_test_meta["pred_risk_score"] = ys_pred_test

    # STAGE 2: Huấn luyện Mô hình Phân loại Mức Cảnh báo (0, 1, 2) dựa trên Meta-Features
    if verbose:
        print("[4/6] Stage 2: Huấn luyện Mô hình Phân loại Cảnh báo Y tế Phân cấp (0, 1, 2)...")
    rf_risk = RandomForestClassifier(n_estimators=180, max_depth=12, random_state=42, n_jobs=-1)
    gb_risk = GradientBoostingClassifier(n_estimators=150, learning_rate=0.08, max_depth=5, random_state=42)
    risk_model = VotingClassifier(estimators=[("rf", rf_risk), ("gb", gb_risk)], voting="soft")
    risk_model.fit(X_train_meta, yr_train)
    yr_pred = risk_model.predict(X_test_meta)

    # STAGE 3: Huấn luyện Mô hình Quyết định Bật/Tắt Quạt + Khóa An toàn Cấu trúc (Structural Guardrail)
    if verbose:
        print("[5/6] Stage 3: Huấn luyện Bộ Điều khiển Quạt ESP32 Phân cấp (Chống xung đột 100%)...")
    rf_fan = RandomForestClassifier(n_estimators=150, max_depth=10, random_state=42, n_jobs=-1)
    gb_fan = GradientBoostingClassifier(n_estimators=120, learning_rate=0.08, max_depth=4, random_state=42)
    fan_model = VotingClassifier(estimators=[("rf", rf_fan), ("gb", gb_fan)], voting="soft")
    fan_model.fit(X_train_meta, yf_train)
    yf_raw_pred = fan_model.predict(X_test_meta)

    # Ràng buộc cấu trúc: Mức 2 bắt buộc BẬT (1), Mức 0 bắt buộc TẮT (0), Mức 1 do fan_model quyết định
    yf_pred = np.where(yr_pred == 2, 1, np.where(yr_pred == 0, 0, yf_raw_pred))

    # STAGE 4: Huấn luyện Mô hình Hurdle Hồi quy Thời gian lọc (Chỉ học trên các mẫu Quạt BẬT > 0)
    if verbose:
        print("[6/6] Stage 4: Huấn luyện Mô hình Hurdle Thời gian lọc (Khử triệt để Zero-Inflation)...")
    active_mask_train = yf_train == 1
    dur_model = GradientBoostingRegressor(n_estimators=160, learning_rate=0.06, max_depth=5, random_state=42)
    dur_model.fit(X_train_meta[active_mask_train], yd_train[active_mask_train])

    yd_raw_test = dur_model.predict(X_test_meta)
    yd_pred = np.where(yf_pred == 1, np.clip(np.round(yd_raw_test), 10.0, 60.0), 0.0)

    # Tính toán các chỉ số kiểm thử trên tập Test (20%)
    mae_score = round(float(mean_absolute_error(ys_test, ys_pred_test)), 2)
    r2_score_val = round(float(r2_score(ys_test, ys_pred_test)), 4)

    acc_risk = round(float(accuracy_score(yr_test, yr_pred)) * 100, 2)
    f1_macro_risk = round(float(f1_score(yr_test, yr_pred, average="macro")) * 100, 2)
    cm_risk = confusion_matrix(yr_test, yr_pred).tolist()

    acc_fan = round(float(accuracy_score(yf_test, yf_pred)) * 100, 2)
    prec_fan = round(float(precision_score(yf_test, yf_pred)) * 100, 2)
    rec_fan = round(float(recall_score(yf_test, yf_pred)) * 100, 2)
    f1_fan = round(float(f1_score(yf_test, yf_pred)) * 100, 2)
    cm_fan = confusion_matrix(yf_test, yf_pred).tolist()

    mae_dur_overall = round(float(mean_absolute_error(yd_test, yd_pred)), 2)
    rmse_dur_overall = round(float(np.sqrt(mean_squared_error(yd_test, yd_pred))), 2)
    r2_dur_overall = round(float(r2_score(yd_test, yd_pred)), 4)

    pos_test_mask = (yf_test == 1) & (yf_pred == 1)
    mae_dur_active = round(float(mean_absolute_error(yd_test[pos_test_mask], yd_pred[pos_test_mask])), 2)
    elapsed = round(time.time() - start_total_time, 2)

    metrics_summary = {
        "architecture": "4-Stage Hierarchical & Two-Stage Hurdle Ensemble",
        "split_ratio": "80% Train (4000) / 20% Test (1000)",
        "stage_1_risk_score_regressor": {
            "mae_points_0_100": mae_score,
            "r2_score": r2_score_val,
        },
        "stage_2_risk_level_classifier": {
            "accuracy_pct": acc_risk,
            "f1_macro_pct": f1_macro_risk,
            "confusion_matrix": cm_risk,
        },
        "stage_3_hierarchical_fan_controller": {
            "accuracy_pct": acc_fan,
            "precision_pct": prec_fan,
            "recall_pct": rec_fan,
            "f1_score_pct": f1_fan,
            "structural_conflicts": 0,
            "confusion_matrix": cm_fan,
        },
        "stage_4_hurdle_duration_regressor": {
            "overall_mae_minutes": mae_dur_overall,
            "active_fan_mae_minutes": mae_dur_active,
            "rmse_minutes": rmse_dur_overall,
            "r2_score": r2_dur_overall,
        },
        "training_time_seconds": elapsed,
    }

    # Lưu toàn bộ 4 mô hình phân cấp
    joblib.dump(score_model, MODEL_DIR / "score_voting_reg.joblib")
    joblib.dump(risk_model, MODEL_DIR / "risk_voting_clf.joblib")
    joblib.dump(fan_model, MODEL_DIR / "fan_voting_clf.joblib")
    joblib.dump(dur_model, MODEL_DIR / "duration_hurdle_reg.joblib")
    with open(MODEL_DIR / "metrics_report.json", "w", encoding="utf-8") as f:
        json.dump(metrics_summary, f, ensure_ascii=False, indent=2)

    if verbose:
        print("\n" + "=" * 86)
        print("         KẾT QUẢ KIỂM THỬ SAU KHI NÂNG CẤP KIẾN TRÚC PHÂN CẤP (TEST SET: 1.000 MẪU)")
        print("=" * 86)
        print(f">>> [STAGE 1] HỒI QUY ĐIỂM NGUY HẠI CÁ NHÂN (0-100): MAE = {mae_score:.2f} điểm | R² = {r2_score_val:.4f}")
        print(f">>> [STAGE 2] PHÂN LOẠI MỨC CẢNH BÁO (0, 1, 2)      : Accuracy = {acc_risk:.2f}% | F1 Macro = {f1_macro_risk:.2f}%")
        print(classification_report(yr_test, yr_pred, target_names=["0: An toàn", "1: Cảnh báo", "2: Nguy hại"], digits=4))
        print(f">>> [STAGE 3] QUYẾT ĐỊNH BẬT/TẮT QUẠT PHÂN CẤP      : Accuracy = {acc_fan:.2f}% | Precision = {prec_fan:.2f}% | Recall = {rec_fan:.2f}%")
        print(f"    * Ma trận nhầm lẫn: Đúng Tắt={cm_fan[0][0]}, Nhầm Bật={cm_fan[0][1]} | Bỏ sót={cm_fan[1][0]}, Đúng Bật={cm_fan[1][1]}")
        print(f">>> [STAGE 4] DỰ TÍNH THỜI GIAN LỌC (HURDLE MODEL)   : Overall MAE = {mae_dur_overall:.2f} phút | Active MAE = {mae_dur_active:.2f} phút | R² = {r2_dur_overall:.4f}")
        print("=" * 86)
        print(f"HOÀN TẤT TRONG {elapsed} GIÂY! Đã lưu 4 file trọng số tại: {MODEL_DIR}")
        print("=" * 86)

    return metrics_summary


if __name__ == "__main__":
    run_training_pipeline(verbose=True)
