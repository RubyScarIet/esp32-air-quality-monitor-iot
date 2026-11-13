import csv
import io
import math
import time
from datetime import datetime
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd
from sqlalchemy import text
from database import get_clean_engine, get_raw_engine

CREATE_CLEAN_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.sensor_data_clean (
    clean_id BIGSERIAL PRIMARY KEY,
    raw_sensor_id BIGINT UNIQUE NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    nguon_nhan VARCHAR(100),
    nhiet_do_raw FLOAT,
    do_am_raw FLOAT,
    gas_raw BIGINT,
    dust_raw BIGINT,
    temp_clean_c FLOAT NOT NULL,
    humidity_clean_pct FLOAT NOT NULL,
    gas_raw_clean FLOAT NOT NULL,
    dust_raw_clean FLOAT NOT NULL,
    pm25_ugm3 FLOAT NOT NULL,
    gas_ppm FLOAT NOT NULL,
    aqi_vn INT NOT NULL,
    air_quality_status VARCHAR(50) NOT NULL,
    heat_index_c FLOAT NOT NULL,
    sensor_health_status VARCHAR(50) DEFAULT 'OK',
    is_spike_cleaned BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sensor_clean_recorded_at ON public.sensor_data_clean(recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_clean_raw_id ON public.sensor_data_clean(raw_sensor_id DESC);
"""


def calculate_vn_aqi_from_pm25(pm25: float) -> int:
  breakpoints = [
      (0.0, 25.0, 0, 50),
      (25.0, 50.0, 51, 100),
      (50.0, 80.0, 101, 150),
      (80.0, 150.0, 151, 200),
      (150.0, 250.0, 201, 300),
      (250.0, 500.0, 301, 500),
  ]
  for c_low, c_high, i_low, i_high in breakpoints:
    if pm25 <= c_high:
      aqi = ((i_high - i_low) / (c_high - c_low)) * (pm25 - c_low) + i_low
      return int(round(np.clip(aqi, 0, 500)))
  return 500


def classify_aqi_status(aqi: int) -> str:
  if aqi <= 50:
    return "Tốt"
  elif aqi <= 100:
    return "Trung bình"
  elif aqi <= 150:
    return "Kém (Nhạy cảm chú ý)"
  elif aqi <= 200:
    return "Xấu (Hại sức khỏe)"
  elif aqi <= 300:
    return "Rất xấu"
  return "Nguy hại"


def calculate_heat_index_c(temp_c: float, rh_pct: float) -> float:
  if temp_c < 26.0:
    return round(temp_c, 1)
  t_f = temp_c * 9.0 / 5.0 + 32.0
  r = rh_pct
  hi = (
      -42.379
      + 2.04901523 * t_f
      + 10.14333127 * r
      - 0.22475541 * t_f * r
      - 0.00683783 * t_f * t_f
      - 0.05481717 * r * r
      + 0.00122874 * t_f * t_f * r
      + 0.00085282 * t_f * r * r
      - 0.00000199 * t_f * t_f * r * r
  )
  return round((hi - 32.0) * 5.0 / 9.0, 1)


def psql_insert_copy(table, conn, keys, data_iter):
  dbapi_conn = conn.connection
  with dbapi_conn.cursor() as cur:
    s_buf = io.StringIO()
    writer = csv.writer(s_buf)
    writer.writerows(data_iter)
    s_buf.seek(0)
    columns = ", ".join([f'"{k}"' for k in keys])
    table_name = f"{table.schema}.{table.name}" if table.schema else table.name
    sql = f"COPY {table_name} ({columns}) FROM STDIN WITH CSV"
    cur.copy_expert(sql=sql, file=s_buf)


def transform_and_clean_dataframe(df_raw: pd.DataFrame) -> pd.DataFrame:
  df = df_raw.copy()
  df["recorded_at"] = pd.to_datetime(df["thoi_gian_thuc"], errors="coerce")
  df = df.dropna(
      subset=["recorded_at", "nhiet_do", "do_am", "gas_raw", "dust_raw"]
  )
  df = df.sort_values(by=["nguon_nhan", "recorded_at", "id"]).reset_index(
      drop=True
  )

  # 1. Khử nhiễu Spike độc lập bằng Rolling Median (k=3)
  df["gas_med3"] = df.groupby("nguon_nhan")["gas_raw"].transform(
      lambda s: s.rolling(window=3, center=True, min_periods=1).median()
  )
  df["dust_med3"] = df.groupby("nguon_nhan")["dust_raw"].transform(
      lambda s: s.rolling(window=3, center=True, min_periods=1).median()
  )

  # Đánh giá độc lập cho từng loại cảm biến (lệch > 35% so với trung vị cục bộ)
  gas_is_spike = (
      (df["gas_raw"] - df["gas_med3"]).abs() / (df["gas_med3"] + 1e-5)
  ) > 0.35
  dust_is_spike = (
      (df["dust_raw"] - df["dust_med3"]).abs() / (df["dust_med3"] + 1e-5)
  ) > 0.35

  df["gas_despiked"] = np.where(gas_is_spike, df["gas_med3"], df["gas_raw"])
  df["dust_despiked"] = np.where(dust_is_spike, df["dust_med3"], df["dust_raw"])
  df["is_spike_cleaned"] = gas_is_spike | dust_is_spike

  # 2. Làm mượt tín hiệu bằng EMA theo từng phiên
  df["temp_clean_c"] = (
      df.groupby("nguon_nhan")["nhiet_do"]
      .transform(lambda s: s.ewm(span=3, adjust=False).mean())
      .round(2)
  )
  df["humidity_clean_pct"] = (
      df.groupby("nguon_nhan")["do_am"]
      .transform(lambda s: s.ewm(span=3, adjust=False).mean())
      .round(2)
  )
  df["gas_raw_clean"] = (
      df.groupby("nguon_nhan")["gas_despiked"]
      .transform(lambda s: s.ewm(span=5, adjust=False).mean())
      .round(1)
  )
  df["dust_raw_clean"] = (
      df.groupby("nguon_nhan")["dust_despiked"]
      .transform(lambda s: s.ewm(span=5, adjust=False).mean())
      .round(1)
  )

  # 3. Quy đổi nồng độ PM2.5 (ug/m3) và Gas (ppm)
  voltage_dust = (df["dust_raw_clean"] / 4095.0) * 3.3 * 1.5
  humidity_corr = np.where(
      df["humidity_clean_pct"] > 60.0,
      1.0 - 0.004 * (df["humidity_clean_pct"] - 60.0),
      1.0,
  )
  df["pm25_ugm3"] = (
      ((voltage_dust - 0.18) * 145.0 * humidity_corr)
      .clip(lower=3.0, upper=500.0)
      .round(2)
  )

  temp_hum_comp = (
      1.0
      + 0.005 * (df["temp_clean_c"] - 25.0)
      + 0.0025 * (df["humidity_clean_pct"] - 50.0)
  )
  df["gas_ppm"] = (
      (((df["gas_raw_clean"] - 220.0).clip(lower=10.0) * 0.095) / temp_hum_comp)
      .clip(lower=10.0, upper=1000.0)
      .round(2)
  )

  # 4. Tính toán AQI, Trạng thái không khí và Heat Index
  df["aqi_vn"] = df["pm25_ugm3"].apply(calculate_vn_aqi_from_pm25)
  df["air_quality_status"] = df["aqi_vn"].apply(classify_aqi_status)
  df["heat_index_c"] = [
      calculate_heat_index_c(t, h)
      for t, h in zip(df["temp_clean_c"], df["humidity_clean_pct"])
  ]

  # 5. Chẩn đoán sức khỏe phần cứng cảm biến mở rộng
  def _check_sensor_health(row):
    if (
        row["dust_raw"] <= 5
        or row["dust_raw"] >= 4090
        or row["gas_raw"] <= 5
        or row["gas_raw"] >= 4090
    ):
      return "SENSOR_RAIL_FAULT"
    if (
        row["nhiet_do"] < 0
        or row["nhiet_do"] > 55
        or row["do_am"] < 10
        or row["do_am"] > 95
    ):
      return "DHT11_OUT_OF_BOUNDS"
    if row["is_spike_cleaned"]:
      return "SPIKE_FILTERED"
    return "OK"

  df["sensor_health_status"] = df.apply(_check_sensor_health, axis=1)

  # 6. Chuẩn hóa đúng 19 cột Schema ban đầu của sensor_data_clean
  out_df = df.rename(
      columns={
          "id": "raw_sensor_id",
          "nhiet_do": "nhiet_do_raw",
          "do_am": "do_am_raw",
      }
  )[[
      "raw_sensor_id",
      "recorded_at",
      "nguon_nhan",
      "nhiet_do_raw",
      "do_am_raw",
      "gas_raw",
      "dust_raw",
      "temp_clean_c",
      "humidity_clean_pct",
      "gas_raw_clean",
      "dust_raw_clean",
      "pm25_ugm3",
      "gas_ppm",
      "aqi_vn",
      "air_quality_status",
      "heat_index_c",
      "sensor_health_status",
      "is_spike_cleaned",
  ]]
  out_df["processed_at"] = datetime.utcnow()
  return out_df


def run_incremental_etl_sync(batch_size: int = 50000) -> Dict[str, Any]:
  raw_engine = get_raw_engine()
  clean_engine = get_clean_engine()

  # Khởi tạo bảng nếu chưa có và lấy raw_sensor_id lớn nhất đã xử lý
  with clean_engine.begin() as conn:
    for stmt in CREATE_CLEAN_TABLE_SQL.strip().split(";"):
      if stmt.strip():
        conn.execute(text(stmt))
    last_id = int(
        conn.execute(
            text(
                "SELECT COALESCE(MAX(raw_sensor_id), 0) FROM"
                " public.sensor_data_clean"
            )
        ).scalar()
        or 0
    )

  # Lấy 5 bản ghi ngữ cảnh liền trước + các bản ghi mới
  if last_id > 0:
    query_sql = text("""
            (SELECT id, created_at, thoi_gian_thuc, nhiet_do, do_am, gas_raw, dust_raw, nguon_nhan
             FROM public.sensor_data
             WHERE id <= :last_id
             ORDER BY id DESC LIMIT 5)
            UNION ALL
            (SELECT id, created_at, thoi_gian_thuc, nhiet_do, do_am, gas_raw, dust_raw, nguon_nhan
             FROM public.sensor_data
             WHERE id > :last_id
             ORDER BY id ASC LIMIT :batch_size)
            ORDER BY id ASC;
        """)
    df_raw = pd.read_sql(
        query_sql,
        raw_engine,
        params={"last_id": last_id, "batch_size": batch_size},
    )
  else:
    query_sql = text("""
            SELECT id, created_at, thoi_gian_thuc, nhiet_do, do_am, gas_raw, dust_raw, nguon_nhan
            FROM public.sensor_data
            ORDER BY id ASC
            LIMIT :batch_size;
        """)
    df_raw = pd.read_sql(query_sql, raw_engine, params={"batch_size": batch_size})

  df_new_check = df_raw[df_raw["id"] > last_id]
  if df_new_check.empty:
    return {"status": "up_to_date", "synced_rows": 0, "last_raw_id": last_id}

  # Clean và lọc các bản ghi cần chèn mới
  df_cleaned_all = transform_and_clean_dataframe(df_raw)
  df_to_insert = df_cleaned_all[
      df_cleaned_all["raw_sensor_id"] > last_id
  ].copy()

  # Chèn siêu tốc qua psql_insert_copy
  df_to_insert.to_sql(
      "sensor_data_clean",
      clean_engine,
      schema="public",
      if_exists="append",
      index=False,
      method=psql_insert_copy,
  )

  return {
      "status": "synced",
      "synced_rows": int(len(df_to_insert)),
      "spikes_cleaned": int(df_to_insert["is_spike_cleaned"].sum()),
      "last_raw_id": int(df_to_insert["raw_sensor_id"].max()),
      "latest_snapshot": df_to_insert.iloc[-1].to_dict(),
  }


if __name__ == "__main__":
  print(
      "Đang chạy ETL Clean đồng bộ từ RAW_DATABASE_URL sang"
      " CLEAN_DATABASE_URL..."
  )
  start_t = time.time()
  result = run_incremental_etl_sync(batch_size=50000)
  elapsed = round(time.time() - start_t, 2)
  print(f"Hoàn thành trong {elapsed}s! Kết quả:")
  print(
      f" - Số dòng đã clean và nạp:"
      f" {result.get('synced_rows', 0):,} / 10.461 dòng"
  )
  print(
      f" - Số điểm nhiễu đã được khử (Despiked):"
      f" {result.get('spikes_cleaned', 0)}"
  )
  print(f" - Raw Sensor ID cuối cùng: {result.get('last_raw_id')}")