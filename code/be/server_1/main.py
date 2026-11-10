import asyncio
import json
from datetime import datetime
from typing import Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from etl_cleaner import run_incremental_etl_sync

app = FastAPI(title="AuraTrack - Server 1 (Real-time Auto-ETL & UI Stream)", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVER_2_AI_WEBHOOK_URL = "http://localhost:8002/api/v1/ai/evaluate-realtime"

REALTIME_STATE_CACHE: Dict[str, Any] = {
    "last_raw_id": 0,
    "last_updated": None,
    "latest_clean_record": None,
}

async def auto_realtime_etl_loop():
    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            try:
                sync_res = await asyncio.to_thread(run_incremental_etl_sync, 500)
                if sync_res.get("status") == "synced" and sync_res.get("latest_snapshot"):
                    latest_clean = sync_res["latest_snapshot"]
                    # Chuyển đổi kiểu datetime sang chuỗi ISO để truyền JSON
                    for k, v in latest_clean.items():
                        if hasattr(v, "isoformat"):
                            latest_clean[k] = v.isoformat()

                    REALTIME_STATE_CACHE["last_raw_id"] = sync_res["last_raw_id"]
                    REALTIME_STATE_CACHE["last_updated"] = datetime.utcnow().isoformat()
                    REALTIME_STATE_CACHE["latest_clean_record"] = latest_clean

                    print(f"[AUTO-ETL] Đã clean tức thì dòng raw_id={sync_res['last_raw_id']} -> PM2.5={latest_clean['pm25_ugm3']} ug/m3, AQI={latest_clean['aqi_vn']}")

                    # Chủ động đẩy (Push) chỉ số vừa clean sang Server 2 để AI đánh giá & điều khiển quạt ngay
                    try:
                        await client.post(SERVER_2_AI_WEBHOOK_URL, json={"clean_sensor": latest_clean})
                    except Exception:
                        pass  # Server 2 có thể chưa bật hoặc đang khởi động
            except Exception as e:
                print(f"[Auto-ETL Warning]: {e}")

            await asyncio.sleep(5.0)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_realtime_etl_loop())

@app.get("/api/v1/realtime/latest")
def get_latest_clean_for_ui():
    if not REALTIME_STATE_CACHE["latest_clean_record"]:
        raise HTTPException(status_code=404, detail="Đang đồng bộ dữ liệu lần đầu...")
    return REALTIME_STATE_CACHE

@app.get("/api/v1/realtime/stream")
async def stream_clean_data_to_ui():
    async def event_generator():
        last_sent_id = 0
        while True:
            current_id = REALTIME_STATE_CACHE.get("last_raw_id", 0)
            if current_id > last_sent_id and REALTIME_STATE_CACHE["latest_clean_record"]:
                last_sent_id = current_id
                payload = json.dumps(REALTIME_STATE_CACHE, default=str)
                yield f"data: {payload}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")