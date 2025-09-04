# C:\egrid-demo\docker\ingest_app\main.py
import os
import time
import csv
import io
import re
from decimal import Decimal

import boto3
from botocore.config import Config


# -------------------------
# Environment / Defaults
# -------------------------
TABLE_NAME = os.environ.get("TABLE_NAME", "egrid_plants")

# MinIO (S3-compatible) endpoint and bucket
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://minio:9000")
S3_BUCKET = os.environ.get("S3_BUCKET", "egrid")
S3_INCOMING_PREFIX = os.environ.get("S3_INCOMING_PREFIX", "incoming/")
S3_PROCESSED_PREFIX = os.environ.get("S3_PROCESSED_PREFIX", "processed/")

# IMPORTANT: use AWS_* names (what boto3 expects). Fall back to MINIO_* or defaults.
S3_KEY = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("MINIO_ACCESS_KEY") or "minioadmin"
S3_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("MINIO_SECRET_KEY") or "minioadmin"

# Local DynamoDB endpoint
DDB_ENDPOINT = os.environ.get("DDB_ENDPOINT", "http://dynamodb:8000")

# Poll frequency (seconds)
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))


# -------------------------
# Header aliases / helpers
# -------------------------
HEADER_ALIASES = {
    "plant_id": ["plant id (orispl)", "orispl", "plant code", "plant orispl code"],
    "plant_name": ["plant name"],
    "state": ["state abbreviation", "plant state abbreviation", "state"],
    "net_generation_mwh": [
        "plant annual net generation (mwh)",
        "plant annual net generation",
        "net generation (mwh)",
        "generation (mwh)",
        "annual net generation (mwh)",
    ],
    "balancing_authority_code": [
        "balancing authority code",
        "ba code",
        "egrid subregion code",
    ],
    "generator_net_generation_mwh": [
        "generator annual net generation (mwh)",
        "annual net generation (mwh) - generator",
        "generator net generation (mwh)",
    ],
}


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower()).strip()


def _find_col(header_row, wanted_key):
    norm_headers = [_normalize(h) for h in header_row]
    # exact match
    for alias in HEADER_ALIASES[wanted_key]:
        ali = _normalize(alias)
        for idx, h in enumerate(norm_headers):
            if ali == h:
                return idx
    # fuzzy contains
    for alias in HEADER_ALIASES[wanted_key]:
        ali = _normalize(alias)
        for idx, h in enumerate(norm_headers):
            if ali in h or h in ali:
                return idx
    return None


def _to_decimal(x):
    try:
        if x is None or x == "":
            return None
        x = str(x).replace(",", "")
        return Decimal(str(float(x)))
    except Exception:
        return None


# -------------------------
# DynamoDB helpers
# -------------------------
def ensure_table(dynamodb):
    client = dynamodb.meta.client
    existing = client.list_tables().get("TableNames", [])
    if TABLE_NAME in existing:
        return dynamodb.Table(TABLE_NAME)
    print("Creating DynamoDB table", TABLE_NAME)
    client.create_table(
        TableName=TABLE_NAME,
        AttributeDefinitions=[{"AttributeName": "plant_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "plant_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)
    return dynamodb.Table(TABLE_NAME)


def process_csv_text(tbl, text: str) -> int:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return 0

    header, data_rows = rows[0], rows[1:]
    idx_id = _find_col(header, "plant_id")
    idx_name = _find_col(header, "plant_name")
    idx_state = _find_col(header, "state")
    idx_net = _find_col(header, "net_generation_mwh")
    idx_ba = _find_col(header, "balancing_authority_code")
    idx_gen_net = _find_col(header, "generator_net_generation_mwh")

    if idx_name is None or idx_state is None or (idx_net is None and idx_gen_net is None):
        raise RuntimeError(
            "CSV missing required columns similar to: 'Plant name', 'State abbreviation', "
            "'Plant annual net generation (MWh)' OR generator-level net."
        )

    aggregated = None
    if idx_net is None and idx_gen_net is not None:
        # Sum generator-level rows to plant totals
        aggregated = {}
        for r in data_rows:
            try:
                plant_name = r[idx_name].strip() if idx_name is not None else None
                state = r[idx_state].strip().upper() if idx_state is not None else None
                gnet = _to_decimal(r[idx_gen_net]) if idx_gen_net is not None else None
                if not plant_name or not state or gnet is None:
                    continue
                plant_id = (r[idx_id].strip() if idx_id is not None and r[idx_id] else None) or f"{state}:{plant_name}"
                ba = r[idx_ba].strip() if idx_ba is not None and r[idx_ba] else None
                key = (plant_id, plant_name, state, ba)
                aggregated[key] = (aggregated.get(key, Decimal(0)) + gnet)
            except Exception as e:
                print("Agg row error:", e)
                continue

    written = 0
    with tbl.batch_writer(overwrite_by_pkeys=["plant_id"]) as batch:
        if aggregated is not None:
            for (plant_id, plant_name, state, ba), net in aggregated.items():
                item = {
                    "plant_id": plant_id,
                    "plant_name": plant_name,
                    "plant_name_lc": plant_name.lower(),
                    "state": state,
                    "net_generation_mwh": net,
                }
                if ba:
                    item["balancing_authority_code"] = ba
                batch.put_item(Item=item)
                written += 1
        else:
            for r in data_rows:
                try:
                    plant_name = r[idx_name].strip() if idx_name is not None else None
                    state = r[idx_state].strip().upper() if idx_state is not None else None
                    net = _to_decimal(r[idx_net]) if idx_net is not None else None
                    if not plant_name or not state or net is None:
                        continue
                    plant_id = (r[idx_id].strip() if idx_id is not None and r[idx_id] else None) or f"{state}:{plant_name}"
                    item = {
                        "plant_id": plant_id,
                        "plant_name": plant_name,
                        "plant_name_lc": plant_name.lower(),
                        "state": state,
                        "net_generation_mwh": net,
                    }
                    if idx_ba is not None and r[idx_ba]:
                        item["balancing_authority_code"] = r[idx_ba].strip()
                    batch.put_item(Item=item)
                    written += 1
                except Exception as e:
                    print("Row error:", e)
                    continue
    return written


# -------------------------
# Main loop
# -------------------------
def main():
    # S3 client with EXPLICIT credentials and MinIO path-style addressing
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )

    dynamodb = boto3.resource("dynamodb", endpoint_url=DDB_ENDPOINT, region_name="us-east-1")
    table = ensure_table(dynamodb)

    # Ensure bucket exists
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        s3.create_bucket(Bucket=S3_BUCKET)

    print(f"Ingest poller started. Watching s3://{S3_BUCKET}/{S3_INCOMING_PREFIX}")
    while True:
        try:
            resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_INCOMING_PREFIX)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                if not key.lower().endswith(".csv"):
                    continue
                print("Processing", key)
                o = s3.get_object(Bucket=S3_BUCKET, Key=key)
                body = o["Body"].read()
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError:
                    text = body.decode("latin-1")

                written = process_csv_text(table, text)
                print("Written items:", written)

                # Move processed file to processed/
                dest = key.replace(S3_INCOMING_PREFIX, S3_PROCESSED_PREFIX, 1)
                s3.copy_object(Bucket=S3_BUCKET, CopySource={"Bucket": S3_BUCKET, "Key": key}, Key=dest)
                s3.delete_object(Bucket=S3_BUCKET, Key=key)
        except Exception as e:
            print("Poll error:", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
