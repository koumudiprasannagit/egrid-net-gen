import os, json, boto3
from boto3.dynamodb.conditions import Attr
from decimal import Decimal

TABLE_NAME = os.environ.get("TABLE_NAME","egrid_plants")
ddb = boto3.resource("dynamodb")
table = ddb.Table(TABLE_NAME)

def _d2f(o):
  if isinstance(o, list):  return [_d2f(x) for x in o]
  if isinstance(o, dict):  return {k:_d2f(v) for k,v in o.items()}
  if isinstance(o, Decimal): return float(o)
  return o

def _resp(body, status=200):
  return {
    "statusCode": status,
    "headers": {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,OPTIONS",
      "Access-Control-Allow-Headers": "*"
    },
    "body": json.dumps(body)
  }

def lambda_handler(event, context):
  path = (event.get("rawPath") or event.get("path") or "/").lower()
  qs   = event.get("queryStringParameters") or {}
  state = (qs.get("state") or "").upper() if qs else None
  limit = int(qs.get("limit") or 10) if qs else 10
  q     = qs.get("q") if qs else None

  scan_kwargs = {}
  if state:
    scan_kwargs["FilterExpression"] = Attr("state").eq(state)

  items = []
  resp  = table.scan(**scan_kwargs); items += resp.get("Items", [])
  while "LastEvaluatedKey" in resp:
    resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
    items += resp.get("Items", [])

  if path.endswith("/search"):
    if q:
      ql = q.lower()
      items = [it for it in items if ql in (it.get("plant_name_lc") or "")]
    items.sort(key=lambda x: x.get("net_generation_mwh", 0), reverse=True)
    return _resp(_d2f(items))

  items.sort(key=lambda x: x.get("net_generation_mwh", 0), reverse=True)
  return _resp(_d2f(items[:limit]))
