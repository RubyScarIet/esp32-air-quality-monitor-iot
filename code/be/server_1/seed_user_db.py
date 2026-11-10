import csv
from io import StringIO
from pathlib import Path
import pandas as pd
from sqlalchemy import text
from database import get_user_engine

RESET_AND_CREATE_TABLES_SQL = """
DROP TABLE IF EXISTS public.ai_training_dataset CASCADE;
DROP TABLE IF EXISTS public.user_medical_profiles CASCADE;

CREATE TABLE public.user_medical_profiles (
    user_id VARCHAR(20) PRIMARY KEY,
    ho_ten VARCHAR(120) NOT NULL,
    gioi_tinh VARCHAR(10),
    tuoi SMALLINT,
    chieu_cao_cm FLOAT,
    can_nang_kg FLOAT,
    bmi FLOAT,
    ma_benh_icd10 VARCHAR(15) NOT NULL,
    ten_benh_ho_hap_phe_quan VARCHAR(255) NOT NULL,
    nhom_benh VARCHAR(80) NOT NULL,
    muc_do_benh_1_4 SMALLINT,
    benh_kem_theo VARCHAR(255),
    chi_so_fev1_phan_tram FLOAT,
    spo2_nen_phan_tram FLOAT,
    nhip_tho_phut SMALLINT,
    do_nhay_cam_phe_quan_0_100 SMALLINT,
    su_dung_binh_xit_gian_phe_quan SMALLINT,
    so_dot_cap_trong_nam SMALLINT,
    tinh_trang_hut_thuoc VARCHAR(50),
    nguong_pm25_an_toan_ugm3 FLOAT NOT NULL,
    nguong_gas_an_toan_ppm FLOAT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE public.ai_training_dataset (
    record_id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES public.user_medical_profiles(user_id) ON DELETE CASCADE,
    ho_ten VARCHAR(120),
    gioi_tinh VARCHAR(10),
    tuoi SMALLINT,
    chieu_cao_cm FLOAT,
    can_nang_kg FLOAT,
    bmi FLOAT,
    ma_benh_icd10 VARCHAR(15),
    ten_benh_ho_hap_phe_quan VARCHAR(255),
    nhom_benh VARCHAR(80),
    muc_do_benh_1_4 SMALLINT,
    benh_kem_theo VARCHAR(255),
    chi_so_fev1_phan_tram FLOAT,
    spo2_nen_phan_tram FLOAT,
    nhip_tho_phut SMALLINT,
    do_nhay_cam_phe_quan_0_100 SMALLINT,
    su_dung_binh_xit_gian_phe_quan SMALLINT,
    so_dot_cap_trong_nam SMALLINT,
    tinh_trang_hut_thuoc VARCHAR(50),
    nguong_pm25_an_toan_ugm3 FLOAT,
    nguong_gas_an_toan_ppm FLOAT,
    nhiet_do_moi_truong_c FLOAT,
    do_am_moi_truong_pct FLOAT,
    dust_raw_clean BIGINT,
    gas_raw_clean BIGINT,
    pm25_quy_doi_ugm3 FLOAT,
    gas_quy_doi_ppm FLOAT,
    diem_nguy_hai_ca_nhan_0_100 FLOAT,
    muc_canh_bao_ai SMALLINT,
    lenh_bat_quat_ai SMALLINT,
    thoi_gian_loc_du_kien_phut INT
);
"""

USER_PROFILE_COLS = [
    "user_id", "ho_ten", "gioi_tinh", "tuoi", "chieu_cao_cm", "can_nang_kg", "bmi",
    "ma_benh_icd10", "ten_benh_ho_hap_phe_quan", "nhom_benh", "muc_do_benh_1_4",
    "benh_kem_theo", "chi_so_fev1_phan_tram", "spo2_nen_phan_tram", "nhip_tho_phut",
    "do_nhay_cam_phe_quan_0_100", "su_dung_binh_xit_gian_phe_quan", "so_dot_cap_trong_nam",
    "tinh_trang_hut_thuoc", "nguong_pm25_an_toan_ugm3", "nguong_gas_an_toan_ppm"
]

def psql_insert_copy(table, conn, keys, data_iter):
    """Phương thức COPY siêu tốc vào PostgreSQL, tránh hoàn toàn lỗi Prepared Statement trên Supabase Pooler."""
    dbapi_conn = conn.connection
    with dbapi_conn.cursor() as cur:
        s_buf = StringIO()
        writer = csv.writer(s_buf)
        writer.writerows(data_iter)
        s_buf.seek(0)
        columns = ', '.join([f'"{k}"' for k in keys])
        table_name = f"{table.schema}.{table.name}" if table.schema else table.name
        sql = f"COPY {table_name} ({columns}) FROM STDIN WITH CSV"
        cur.copy_expert(sql=sql, file=s_buf)

def seed_user_database(csv_path: str = "dataset_y_te_ho_hap_ca_nhan_hoa_5000.csv"):
    try:
        engine = get_user_engine()
        file_path = Path(csv_path)
        if not file_path.exists():
            alt_path = Path(__file__).resolve().parent.parent / csv_path
            if alt_path.exists():
                file_path = alt_path
            else:
                raise FileNotFoundError(f"Khong tim thay file {csv_path}. Hay dat file CSV vao thu muc be/server_1/ hoac be/")

        print(f"[1/4] Dang doc file: {file_path.name} ...")
        # Them keep_default_na=False de Pandas KHONG bien chu "None" o cot nhom_benh thanh gia tri NULL (NaN)
        df = pd.read_csv(file_path, keep_default_na=False)
        # Doi luon chu "None" thanh "Không có bệnh" cho truc quan tren Database
        df["nhom_benh"] = df["nhom_benh"].replace({"None": "Không có bệnh", "": "Không có bệnh"})
        print(f"      -> Da doc {len(df)} dong.")

        print("[2/4] Reset va tao lai 2 bang chuan tren Supabase...")
        with engine.begin() as conn:
            for stmt in RESET_AND_CREATE_TABLES_SQL.strip().split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))

        print("[3/4] Nap 5.000 ho so nguoi dung vao `user_medical_profiles` (COPY Stream)...")
        df_profiles = df[USER_PROFILE_COLS].drop_duplicates(subset=["user_id"])
        df_profiles.to_sql(
            "user_medical_profiles",
            engine,
            schema="public",
            if_exists="append",
            index=False,
            method=psql_insert_copy
        )

        print("[4/4] Nap 5.000 ban ghi huan luyen vao `ai_training_dataset` (COPY Stream)...")
        df.to_sql(
            "ai_training_dataset",
            engine,
            schema="public",
            if_exists="append",
            index=False,
            method=psql_insert_copy
        )

        print("HOAN TAT! Da nap thanh cong 5.000 ho so nguoi dung & benh an len USER_DATABASE_URL.")

    except Exception as e:
        # Chi in ra nguyen nhan loi goc cua PostgreSQL thay vi in tran 500 dong du lieu
        orig_err = getattr(e, "orig", e)
        print("\n[LOI GOC TU POSTGRESQL / SUPABASE]:")
        print(f" -> {type(orig_err).__name__}: {orig_err}")

if __name__ == "__main__":
    seed_user_database()