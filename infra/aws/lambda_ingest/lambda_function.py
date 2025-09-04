import os, csv, io, re, json, urllib.parse, boto3
from decimal import Decimal

TABLE_NAME  = os.environ.get("TABLE_NAME", "egrid_plants")
S3_BUCKET   = os.environ["S3_BUCKET"]
IN_PREFIX   = os.environ.get("S3_INCOMING_PREFIX", "incoming/")
PROC_PREFIX = os.environ.get("S3_PROCESSED_PREFIX", "processed/")

ddb = boto3.resource("dynamodb")
table = ddb.Table(TABLE_NAME)
s3 = boto3.client("s3")

HEADER_ALIASES = {
  "plant_id": ["plant id (orispl)","orispl","plant orispl code","plant code"],
  "plant_name": ["plant name"],
  "state": ["state abbreviation","plant state abbreviation","state"],
  "net_generation_mwh": [
    "plant annual net generation (mwh)",
    "plant annual net generation","net generation (mwh)","generation (mwh)","annual net generation (mwh)"
  ],
  "balancing_authority_code": ["balancing authority code","ba code","egrid subregion code"],
  "generator_net_generation_mwh": [
    "generator annual net generation (mwh)","generator net generation (mwh)","annual net generation (mwh) - generator"
  ],
}
def _norm(s): return re.sub(r"[^a-z0-9]+"," ",(s or "").strip().lower()).strip()

def _find(header, key):
  H=[_norm(h) for h in header]
  for alias in HEADER_ALIASES[key]:
    a=_norm(alias)
    for i,h in enumerate(H):
      if a==h: return i
  for alias in HEADER_ALIASES[key]:
    a=_norm(alias)
    for i,h in enumerate(H):
      if a in h or h in a: return i
  return None

def _to_decimal(x):
  try:
    if x is None or x=="": return None
    x=str(x).replace(",","")
    return Decimal(str(float(x)))
  except Exception:
    return None

def _read_rows(text: str):
  text = text.lstrip("\ufeff")
  sample = text[:4096]
  try:
    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    reader = csv.reader(io.StringIO(text), dialect)
  except Exception:
    first = sample.splitlines()[0] if sample.splitlines() else sample
    delim = max([",","\t",";","|"], key=lambda d: len(first.split(d)))
    reader = csv.reader(io.StringIO(text), delimiter=delim)
  return [row for row in reader if any(cell.strip() for cell in row)]

def process_csv_text(text: str) -> int:
  rows = _read_rows(text)
  if not rows: return 0
  header, data = rows[0], rows[1:]

  i_id=_find(header,"plant_id"); i_nm=_find(header,"plant_name"); i_st=_find(header,"state")
  i_net=_find(header,"net_generation_mwh"); i_ba=_find(header,"balancing_authority_code")
  i_gnet=_find(header,"generator_net_generation_mwh")

  if i_nm is None or i_st is None or (i_net is None and i_gnet is None):
    print("Header detected:", header)
    return 0

  written=0
  if i_net is None and i_gnet is not None:
    agg={}
    for r in data:
      try:
        name=(r[i_nm] or "").strip(); st=(r[i_st] or "").strip().upper()
        gnet=_to_decimal(r[i_gnet]) if i_gnet is not None and len(r)>i_gnet else None
        if not name or not st or gnet is None: continue
        pid=(r[i_id].strip() if i_id is not None and len(r)>i_id and r[i_id] else None) or f"{st}:{name}"
        ba=(r[i_ba].strip() if i_ba is not None and len(r)>i_ba and r[i_ba] else None)
        key=(pid,name,st,ba); agg[key]=agg.get(key,Decimal(0))+gnet
      except Exception: continue
    for (pid,name,st,ba),net in agg.items():
      item={"plant_id":pid,"plant_name":name,"plant_name_lc":name.lower(),"state":st,"net_generation_mwh":net}
      if ba: item["balancing_authority_code"]=ba
      table.put_item(Item=item); written+=1
  else:
    for r in data:
      try:
        name=(r[i_nm] or "").strip(); st=(r[i_st] or "").strip().upper()
        net=_to_decimal(r[i_net]) if i_net is not None and len(r)>i_net else None
        if not name or not st or net is None: continue
        pid=(r[i_id].strip() if i_id is not None and len(r)>i_id and r[i_id] else None) or f"{st}:{name}"
        item={"plant_id":pid,"plant_name":name,"plant_name_lc":name.lower(),"state":st,"net_generation_mwh":net}
        if i_ba is not None and len(r)>i_ba and r[i_ba]: item["balancing_authority_code"]=r[i_ba].strip()
        table.put_item(Item=item); written+=1
      except Exception: continue
  return written

def lambda_handler(event, context):
  for rec in event.get("Records", []):
    b = rec["s3"]["bucket"]["name"]
    key = urllib.parse.unquote(rec["s3"]["object"]["key"])
    if not key.lower().endswith(".csv") or not key.startswith(IN_PREFIX): continue
    obj = s3.get_object(Bucket=b, Key=key)
    body = obj["Body"].read()
    try: text = body.decode("utf-8")
    except UnicodeDecodeError: text = body.decode("latin-1")
    n = process_csv_text(text)
    dest = key.replace(IN_PREFIX, PROC_PREFIX, 1)
    s3.copy_object(Bucket=b, CopySource={"Bucket": b, "Key": key}, Key=dest)
    s3.delete_object(Bucket=b, Key=key)
    print(f"Processed {key} → wrote {n} items → moved to {dest}")
  return {"statusCode":200,"body":json.dumps({"ok":True})}
