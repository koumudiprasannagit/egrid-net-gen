from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict, Any
import os, boto3
from boto3.dynamodb.conditions import Attr
from decimal import Decimal

TABLE_NAME = os.environ.get("TABLE_NAME", "egrid_plants")
DDB_ENDPOINT = os.environ.get("DDB_ENDPOINT", "http://dynamodb:8000")

dynamodb = boto3.resource("dynamodb", endpoint_url=DDB_ENDPOINT, region_name="us-east-1")
table = dynamodb.Table(TABLE_NAME)

app = FastAPI(title="eGRID Net Generation API (Local)")

# --- CORS so the browser can call the API from http://localhost:8080 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080","http://127.0.0.1:8080","*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _decimal_to_float(obj):
    if isinstance(obj, list):
        return [_decimal_to_float(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

@app.get("/top")
def top(limit: int = 10, state: Optional[str] = None):
    scan_kwargs: Dict[str, Any] = {}
    if state:
        scan_kwargs["FilterExpression"] = Attr("state").eq(state.upper())
    items: List[Dict[str, Any]] = []
    resp = table.scan(**scan_kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
        items.extend(resp.get("Items", []))
    items.sort(key=lambda x: x.get("net_generation_mwh", Decimal(0)), reverse=True)
    return _decimal_to_float(items[:limit])

@app.get("/search")
def search(state: Optional[str] = None, q: Optional[str] = None):
    scan_kwargs: Dict[str, Any] = {}
    if state:
        scan_kwargs["FilterExpression"] = Attr("state").eq(state.upper())
    items: List[Dict[str, Any]] = []
    resp = table.scan(**scan_kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
        items.extend(resp.get("Items", []))
    if q:
        q_lc = q.lower()
        items = [it for it in items if q_lc in (it.get("plant_name_lc") or "")]
    items.sort(key=lambda x: x.get("net_generation_mwh", Decimal(0)), reverse=True)
    return _decimal_to_float(items)
