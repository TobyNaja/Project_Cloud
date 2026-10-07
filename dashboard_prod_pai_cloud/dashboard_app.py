from flask import Flask, Response, request, jsonify, render_template
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime

app = Flask(__name__)
app.json.sort_keys = False  # คงลำดับ key ตามไฟล์ต้นฉบับ

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_path(path):
    """path แบบ relative อิงจากโฟลเดอร์ของ dashboard เสมอ ไม่ขึ้นกับว่าสั่งรันจากที่ไหน"""
    path = os.path.expanduser(os.path.expandvars(path.strip().strip('"')))
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return os.path.abspath(path)


# โฟลเดอร์ Terraform ถูก "อ่านอย่างเดียว" เท่านั้น dashboard ไม่เคยเขียนอะไรลงไปในนั้น
TERRAFORM_DIR = resolve_path(os.environ.get("TERRAFORM_DIR", os.path.join("..", "prodpai_cloud")))
# ไฟล์ที่ /api/update เขียน (ข้อมูลที่ pipeline POST เข้ามา) อยู่ฝั่ง dashboard เสมอ
PUSH_FILE = resolve_path(os.environ.get("INFRA_FILE", "current_infra.json"))
SAMPLE_FILE = os.path.join(BASE_DIR, "current_infra_sample.json")
# ชื่อไฟล์ที่ตรวจหาอัตโนมัติในโฟลเดอร์ Terraform เมื่อไม่ได้ตั้ง INFRA_JSON_PATH
AUTO_NAMES = ("current_infra.json", "currentinfra.json", "terraform_output.json", "outputs.json")
# ชื่อ output ที่ใช้เมื่อไฟล์เป็น `terraform output -json` แบบรวมทุก output
OUTPUT_KEY = os.environ.get("INFRA_OUTPUT_KEY", "infrastructure_summary")
SAMPLE_FALLBACK = os.environ.get("INFRA_SAMPLE_FALLBACK", "1") != "0"
TF_LOCK_NAME = ".terraform.tfstate.lock.info"

READ_RETRIES = 3
READ_RETRY_SECONDS = 0.15
# ไฟล์ที่ยังอ่านไม่ได้ภายในช่วงนี้หลังถูกแก้ไข ถือว่า "กำลังเขียนอยู่" ไม่ใช่ไฟล์เสีย
WRITE_GRACE_SECONDS = float(os.environ.get("INFRA_WRITE_GRACE_SECONDS", "10"))
EVENT_CHECK_SECONDS = 1
EVENT_HEARTBEAT_SECONDS = 15


def candidate_paths():
    """ไฟล์ทั้งหมดที่ dashboard เฝ้าดู: INFRA_JSON_PATH (ถ้าตั้ง) หรือชื่อมาตรฐานใน TERRAFORM_DIR
    ตามด้วยไฟล์ที่ /api/update เขียน"""
    explicit = os.environ.get("INFRA_JSON_PATH", "").strip()
    paths = []
    if explicit:
        for part in explicit.split(os.pathsep):
            if not part.strip():
                continue
            path = resolve_path(part)
            if os.path.isdir(path):
                paths.extend(os.path.join(path, name) for name in AUTO_NAMES)
            else:
                paths.append(path)
    else:
        paths.extend(os.path.join(TERRAFORM_DIR, name) for name in AUTO_NAMES)
    paths.append(PUSH_FILE)
    unique = {}
    for path in paths:
        unique.setdefault(os.path.normcase(path), path)
    return list(unique.values())


def display_path(path):
    try:
        return os.path.relpath(path, BASE_DIR).replace("\\", "/")
    except ValueError:  # คนละไดรฟ์บน Windows
        return path

# ใช้เมื่อไม่มีทั้ง current_infra.json และ current_infra_sample.json
FALLBACK_MOCK = {
    "project": "prodpai-cloud",
    "region": "ap-southeast-1",
    "vpc": {"id": "vpc-0a4c7e19b52d83f06", "cidr_block": "10.20.0.0/16"},
    "load_balancer": {"name": "prodpai-prod-alb", "state": "active"},
    "security_groups": {"alb": "sg-0c41e7b29a5d38f16", "ec2": "sg-06a9d3f1c7e52b804"},
    "auto_scaling": {"name": "prodpai-prod-asg", "desired_capacity": 1},
    "ec2_instances": [
        {"id": "i-04d7a1e93c6b2f580", "status": "running", "instance_type": "t3.micro",
         "private_ip": "10.20.11.84"}
    ],
    "s3_buckets": [{"name": "prodpai-prod-tfstate"}],
}

# key ใน JSON -> (terraform resource type, สถานะเริ่มต้นถ้าไม่ได้ส่งมา)
RESOURCE_KEYS = {
    "vpc": ("aws_vpc", "available"),
    "subnets": ("aws_subnet", "available"),
    "internet_gateway": ("aws_internet_gateway", "available"),
    "nat_gateways": ("aws_nat_gateway", "available"),
    "load_balancer": ("aws_lb", "active"),
    "load_balancers": ("aws_lb", "active"),
    "target_group": ("aws_lb_target_group", "active"),
    "target_groups": ("aws_lb_target_group", "active"),
    "waf": ("aws_wafv2_web_acl", "active"),
    "security_groups": ("aws_security_group", "active"),
    "auto_scaling": ("aws_autoscaling_group", "active"),
    "launch_template": ("aws_launch_template", "active"),
    "ec2_instances": ("aws_instance", "running"),
    "image_builder": ("aws_instance", "terminated"),
    "lambda_functions": ("aws_lambda_function", "active"),
    "s3_buckets": ("aws_s3_bucket", "available"),
    "rds_instances": ("aws_db_instance", "available"),
}

TYPE_CATEGORY = {
    "aws_instance": "Compute",
    "aws_autoscaling_group": "Compute",
    "aws_launch_template": "Compute",
    "aws_lambda_function": "Compute",
    "aws_vpc": "Network",
    "aws_subnet": "Network",
    "aws_internet_gateway": "Network",
    "aws_nat_gateway": "Network",
    "aws_lb": "Network",
    "aws_lb_target_group": "Network",
    "aws_security_group": "Security",
    "aws_wafv2_web_acl": "Security",
    "aws_s3_bucket": "Storage",
    "aws_ebs_volume": "Storage",
    "aws_db_instance": "Database",
}

HEALTHY = {"running", "active", "available", "in-service", "inservice", "healthy", "ok",
           "enabled", "attached", "issued"}
INACTIVE = {"terminated", "deleted", "disabled", "inactive"}
WARNING = {"stopped", "stopping", "pending", "shutting-down", "unhealthy", "degraded",
           "impaired", "failed", "error", "provisioning", "draining"}

# ราคา on-demand โดยประมาณของ ap-southeast-1 (USD) ใช้ประเมินคร่าวๆ เท่านั้น
HOURS_PER_MONTH = 730
EC2_HOURLY = {
    "t2.micro": 0.0146, "t2.small": 0.0292, "t2.medium": 0.0584,
    "t3.micro": 0.0132, "t3.small": 0.0264, "t3.medium": 0.0528, "t3.large": 0.1056,
    "m5.large": 0.12, "c5.large": 0.098,
}
ALB_HOURLY = 0.0252
NAT_HOURLY = 0.059
S3_PER_GB = 0.025
WAF_ACL_MONTHLY = 5.0
WAF_RULE_MONTHLY = 1.0

SPEC_FIELDS = [
    ("instance_type", "{}"), ("load_balancer_type", "{}"), ("scheme", "{}"),
    ("runtime", "{}"), ("memory_size", "{} MB"), ("engine", "{}"), ("cidr_block", "{}"),
    ("desired_capacity", "desired {}"), ("min_size", "min {}"), ("max_size", "max {}"),
    ("ami_id", "{}"), ("port", "port {}"), ("size_gb", "{} GB"),
    ("rule_count", "{} rules"), ("versioning", "versioning {}"),
]


def first(item, *keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def unwrap(payload):
    """รองรับทั้ง JSON ธรรมดา และรูปแบบ `terraform output -json` ที่ห่อด้วย {"value": ...}"""
    if isinstance(payload.get("value"), dict):
        payload = payload["value"]
    unwrapped = {}
    for key, value in payload.items():
        if isinstance(value, dict) and "value" in value and ("type" in value or "sensitive" in value):
            value = value["value"]
        unwrapped[key] = value
    # `terraform output -json` (ทุก output) จะได้ {"infrastructure_summary": {...}, ...}
    # ส่วน `terraform output -json infrastructure_summary` จะได้ตัว object ตรงๆ รองรับทั้งสองแบบ
    inner = unwrapped.get(OUTPUT_KEY)
    if isinstance(inner, dict):
        return inner
    return unwrapped


def health_of(status):
    if status in HEALTHY:
        return "healthy"
    if status in INACTIVE:
        return "inactive"
    if status in WARNING:
        return "warning"
    return "unknown"


def estimate_cost(rtype, item, status):
    override = item.get("monthly_cost")
    if isinstance(override, (int, float)):
        return round(float(override), 2)
    if status in INACTIVE or status == "stopped":
        return 0.0
    cost = 0.0
    if rtype == "aws_instance":
        cost = EC2_HOURLY.get(str(item.get("instance_type", "")), 0.0) * HOURS_PER_MONTH
    elif rtype == "aws_lb":
        cost = ALB_HOURLY * HOURS_PER_MONTH
    elif rtype == "aws_nat_gateway":
        cost = NAT_HOURLY * HOURS_PER_MONTH
    elif rtype == "aws_wafv2_web_acl":
        rules = item.get("rule_count")
        cost = WAF_ACL_MONTHLY + WAF_RULE_MONTHLY * (rules if isinstance(rules, (int, float)) else 0)
    elif rtype == "aws_s3_bucket":
        size = item.get("size_gb")
        cost = S3_PER_GB * (size if isinstance(size, (int, float)) else 0)
    return round(cost, 2)


def build_resource(item, rtype, default_status, ctx, name_hint=None):
    own_tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    tags = {**own_tags, **{k: v for k, v in ctx["tags"].items() if k not in own_tags}}
    tags = {str(k): str(v) for k, v in tags.items()}

    rid = first(item, "id", "instance_id", "arn", "name", "function_name", "bucket") or name_hint or rtype
    name = own_tags.get("Name") or first(item, "name", "function_name", "bucket") or name_hint or rid
    status = str(first(item, "status", "state", "instance_state") or default_status).lower()

    ips = []
    for key in ("private_ip", "private_ip_address", "public_ip", "public_ip_address"):
        if item.get(key):
            ips.append(str(item[key]))
    if isinstance(item.get("ip_addresses"), list):
        ips.extend(str(ip) for ip in item["ip_addresses"])

    az = first(item, "az", "availability_zone", "availability_zones")
    if isinstance(az, list):
        az = ", ".join(str(a) for a in az)

    spec = [fmt.format(item[key]) for key, fmt in SPEC_FIELDS if item.get(key) not in (None, "")]

    return {
        "id": str(rid),
        "name": str(name),
        "type": str(item.get("type") or rtype),
        "category": str(item.get("category") or TYPE_CATEGORY.get(rtype, "Other")),
        "provider": str(item.get("provider") or ctx["provider"]),
        "status": status,
        "health": health_of(status),
        "region": str(first(item, "region") or ctx["region"]),
        "az": str(az) if az else "",
        "ips": list(dict.fromkeys(ips)),
        "endpoint": str(first(item, "dns_name", "endpoint", "domain_name") or ""),
        "tags": tags,
        "spec": ", ".join(spec[:4]),
        "monthly_cost": estimate_cost(rtype, item, status),
    }


def expand(key, value):
    """แปลงค่าของ key หนึ่งให้เป็น list ของ (item dict, name_hint)"""
    hint = key.replace("_", "-")
    if isinstance(value, dict):
        is_name_to_id = value and all(isinstance(v, str) for v in value.values()) and not (
            {"id", "name", "arn", "instance_id"} & set(value))
        if is_name_to_id:
            # เช่น security_groups: {"alb": "sg-...", "ec2": "sg-..."}
            return [({"id": v, "name": k}, k) for k, v in value.items()]
        is_group_to_ids = value and all(
            isinstance(v, list) and all(isinstance(i, str) for i in v) for v in value.values())
        if is_group_to_ids:
            # เช่น subnets: {"public": ["subnet-..."], "private": ["subnet-..."]}
            return [({"id": rid, "name": f"{group}-{n}", "tags": {"Tier": group}}, None)
                    for group, ids in value.items() for n, rid in enumerate(ids, 1)]
        return [(value, hint)]
    if isinstance(value, list):
        items = []
        for entry in value:
            if isinstance(entry, dict):
                items.append((entry, None))
            elif isinstance(entry, str):
                items.append(({"id": entry, "name": entry}, None))
        return items
    return []


def normalize(payload):
    ctx = {
        "region": str(payload.get("region") or "unknown"),
        "provider": str(payload.get("provider") or "aws"),
        "tags": payload.get("tags") if isinstance(payload.get("tags"), dict) else {},
    }
    resources, issues, ignored = [], [], []

    def add(key, item, rtype, default_status, hint=None):
        # resource ที่รูปแบบผิดหนึ่งตัวต้องไม่ทำให้ตัวอื่นหายไปด้วย
        try:
            resources.append(build_resource(item, rtype, default_status, ctx, hint))
        except Exception as exc:
            issues.append(f"{key}: skipped one entry ({type(exc).__name__}: {exc})")

    # รูปแบบ list สำเร็จรูป: {"resources": [{"type": "aws_instance", ...}]}
    if isinstance(payload.get("resources"), list):
        for item in payload["resources"]:
            if isinstance(item, dict):
                add("resources", item, str(item.get("type") or "unknown"), "unknown")

    for key, value in payload.items():
        if key in ("resources", "tags"):
            continue
        if key in RESOURCE_KEYS:
            rtype, default_status = RESOURCE_KEYS[key]
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            rtype, default_status = key, "unknown"
        elif isinstance(value, dict) and {"id", "arn"} & set(value):
            rtype, default_status = key, "unknown"
        else:
            if isinstance(value, (dict, list)) and value:
                ignored.append(str(key))
            continue
        try:
            items = expand(key, value)
        except Exception as exc:
            issues.append(f"{key}: unexpected shape ({type(exc).__name__}: {exc})")
            continue
        if not items and value not in (None, "", [], {}):
            issues.append(f"{key}: expected an object or a list, got {type(value).__name__}")
        for item, hint in items:
            add(key, item, rtype, default_status, hint)
    return resources, issues, ignored


def summarize(resources):
    by_category, by_type = {}, {}
    health = {"healthy": 0, "warning": 0, "inactive": 0, "unknown": 0}
    for r in resources:
        by_category[r["category"]] = by_category.get(r["category"], 0) + 1
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        health[r["health"]] += 1
    if not resources:
        status = "No data"
    elif health["warning"]:
        status = "Warning"
    else:
        status = "Healthy"
    return {
        "total": len(resources),
        "by_category": by_category,
        "by_type": by_type,
        "health": health,
        "status": status,
        "monthly_cost": round(sum(r["monthly_cost"] for r in resources), 2),
    }


def stat_signature(path):
    try:
        stat = os.stat(path)
        return f"{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        return "none"


def decode_json(raw):
    # PowerShell 5.1 เขียนผลของ `terraform output -json > file` เป็น UTF-16 จึงต้องดู BOM ก่อน
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    return json.loads(text)


def read_stable(path):
    """อ่านไฟล์ JSON แบบกันการอ่านชนกับการเขียน คืนค่า (status, data, detail)

    status: ok | missing | writing | invalid
    ไฟล์จะถูกยอมรับก็ต่อเมื่อ stat ก่อนและหลังอ่านตรงกัน (ไม่มีใครเขียนระหว่างอ่าน) ไม่ว่าง
    และ parse เป็น JSON object ได้ครบ ถ้าไม่ผ่านจะลองใหม่สั้นๆ ก่อนสรุปผล
    """
    detail = ""
    for attempt in range(READ_RETRIES):
        if attempt:
            time.sleep(READ_RETRY_SECONDS)
        try:
            before = stat_signature(path)
            if before == "none":
                return "missing", None, ""
            with open(path, "rb") as f:
                raw = f.read()
            after = stat_signature(path)
        except FileNotFoundError:
            return "missing", None, ""
        except OSError as exc:  # เช่น ถูก process อื่น lock ไว้บน Windows
            detail = f"the file is locked by another process ({exc.strerror or exc})"
            continue
        if before != after:
            detail = "the file changed while it was being read"
            continue
        if not raw.strip():
            detail = "the file is empty"  # shell redirect ตัดไฟล์เป็น 0 byte ก่อนเริ่มเขียน
            continue
        try:
            data = decode_json(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            detail = f"the JSON is incomplete or malformed ({exc})"
            continue
        if not isinstance(data, dict):
            return "invalid", None, "the top-level JSON value must be an object"
        return "ok", data, ""

    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return "missing", None, ""
    return ("writing" if age < WRITE_GRACE_SECONDS else "invalid"), None, detail


def terraform_lock_present(paths):
    """มีไฟล์ lock ของ Terraform (local backend) อยู่ไหม ใช้แจ้งสถานะเท่านั้น ไม่ได้ใช้กั้นการอ่าน"""
    folders = {TERRAFORM_DIR} | {os.path.dirname(p) for p in paths}
    return any(os.path.exists(os.path.join(folder, TF_LOCK_NAME)) for folder in folders)


_snapshot_lock = threading.Lock()
_snapshot_cache = {"signature": None, "snapshot": None}
_last_good = {"data": None, "path": None}


def get_snapshot():
    """สถานะล่าสุดของไฟล์ข้อมูล ใช้ร่วมกันทั้ง /api/data และ /api/events

    เลือกไฟล์ที่ใหม่ที่สุดในบรรดาไฟล์ที่เฝ้าดู ถ้าไฟล์นั้นยังเขียนไม่เสร็จหรือเสีย จะคงข้อมูลชุดล่าสุด
    ที่อ่านได้สมบูรณ์ไว้ (last known good) แทนที่จะส่งข้อมูลแหว่งให้หน้าเว็บ
    """
    with _snapshot_lock:
        paths = candidate_paths()
        applying = terraform_lock_present(paths)
        signature = "|".join([stat_signature(p) for p in paths]
                             + [stat_signature(SAMPLE_FILE), str(applying)])
        cached = _snapshot_cache["snapshot"]
        # สถานะ writing ต้องประเมินใหม่ทุกครั้ง เพราะจะเปลี่ยนเป็น invalid เองเมื่อพ้นช่วง grace
        if cached and _snapshot_cache["signature"] == signature and cached["state"] != "writing":
            return cached

        existing = []
        for path in paths:
            try:
                existing.append((os.stat(path).st_mtime_ns, path))
            except OSError:
                pass
        existing.sort(reverse=True)

        # head = ไฟล์ใหม่สุดที่ยังมีอยู่, older = ไฟล์ที่เก่ากว่าแต่สมบูรณ์ (ใช้เมื่อ head ยังอ่านไม่ได้)
        head, older = None, None
        for _, path in existing:
            status, payload, reason = read_stable(path)
            if status == "missing":
                continue
            if head is None:
                head = (path, status, payload, reason)
                if status == "ok":
                    break
            elif status == "ok" and payload:
                older = (path, payload)
                break

        state, detail, data, source, used, data_path = "waiting", "", None, "none", None, None
        if head:
            used, status, payload, reason = head
            if status == "ok" and payload:
                state, data, source, data_path = "live", payload, "live", used
            elif status != "ok":
                state, detail = status, reason
                if _last_good["data"] is not None:
                    data, source, data_path = _last_good["data"], "stale", _last_good["path"]
                elif older:
                    (data_path, data), source = older, "stale"

        if state == "live":
            _last_good.update(data=data, path=used)
        elif head is None or head[1] == "ok":
            # ไฟล์หายไปหรือว่างเปล่าจริงๆ (เช่นหลัง destroy) ไม่ควรค้างข้อมูลเก่าไว้
            _last_good.update(data=None, path=None)

        snapshot = {
            "state": state,
            "detail": detail,
            "data": data,
            "source": source,
            "path": used,
            "data_path": data_path,
            "applying": applying,
            "watching": [display_path(p) for p in paths],
            "version": hashlib.sha1(f"{state}|{source}|{signature}".encode()).hexdigest()[:16],
        }
        _snapshot_cache.update(signature=signature, snapshot=snapshot)
        return snapshot


def load_sample():
    status, data, _ = read_stable(SAMPLE_FILE)
    return data if status == "ok" and data else dict(FALLBACK_MOCK)


def describe(snapshot, resources, payload, issues):
    """ข้อความสถานะที่หน้าเว็บนำไปแสดง คืนค่า (state, title, detail)"""
    state = snapshot["state"]
    shown = display_path(snapshot["path"]) if snapshot["path"] else ""
    kept = " Showing the last complete data until the file is ready." if snapshot["source"] == "stale" else ""

    if state == "waiting":
        if shown:
            return state, "Waiting for Terraform state", f"{shown} exists but has no outputs yet."
        return state, "Waiting for Terraform state", (
            "No output file was found yet. Watching " + ", ".join(snapshot["watching"])
            + ". This page updates by itself after the next terraform apply.")
    if state == "writing":
        return state, "Terraform output is being written", (
            f"{shown} is not complete yet ({snapshot['detail']}).{kept}")
    if state == "invalid":
        return state, "Terraform output could not be read", (
            f"{shown} is not usable ({snapshot['detail']}).{kept}")
    if not resources:
        if str(payload.get("status", "")).upper() == "DESTROYED":
            return "waiting", "Infrastructure was destroyed", (
                str(payload.get("message") or "") + " Waiting for the next terraform apply.").strip()
        keys = ", ".join(list(payload)[:8]) or "none"
        return "schema", "Unrecognised Terraform output format", (
            f"{shown} is valid JSON, but none of its keys describe a known resource. Keys found: {keys}.")
    if snapshot["applying"]:
        return state, "terraform apply is running", (
            "Showing the last applied state. This page updates by itself when the apply finishes.")
    if issues:
        return state, "Some entries were skipped", "; ".join(issues[:3]) + "."
    return state, "", ""


@app.route('/api/update', methods=['POST'])
def update_infra():
    req_data = request.get_json(silent=True)
    if not isinstance(req_data, dict):
        return jsonify({"status": "error", "message": "Request body must be a JSON object."}), 400

    if isinstance(req_data.get("value"), dict):
        payload = req_data["value"]
    else:
        payload = req_data

    payload["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # กันพลาด: ถ้า INFRA_FILE ถูกตั้งให้ชี้เข้าไปในโฟลเดอร์ Terraform จะไม่ยอมเขียนเด็ดขาด
    target_dir = os.path.dirname(PUSH_FILE)
    try:
        inside_terraform = os.path.commonpath([target_dir, TERRAFORM_DIR]) == TERRAFORM_DIR
    except ValueError:  # คนละไดรฟ์บน Windows
        inside_terraform = False
    if inside_terraform:
        return jsonify({"status": "error",
                        "message": "INFRA_FILE points inside the Terraform folder. Refusing to write there."}), 409

    # เขียนไฟล์ชั่วคราวแล้วค่อยสลับ เพื่อไม่ให้ฝั่งอ่านเจอไฟล์ที่เขียนไม่เสร็จ
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, PUSH_FILE)
    except OSError as exc:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return jsonify({"status": "error", "message": f"Could not write data file: {exc}"}), 500

    return jsonify({"status": "success", "message": "Dashboard updated!"})


@app.route('/api/data', methods=['GET'])
def get_data():
    snapshot = get_snapshot()
    version = snapshot["version"]

    if request.headers.get("If-None-Match", "").strip('"') == version:
        return "", 304

    raw, source = snapshot["data"], snapshot["source"]
    payload, resources, issues, ignored = {}, [], [], []
    if raw is not None:
        try:
            payload = unwrap(raw)
            resources, issues, ignored = normalize(payload)
        except Exception as exc:  # โครงสร้าง JSON ผิดจากที่คาดไว้ ไม่ควรทำให้หน้าเว็บพัง
            app.logger.exception("Failed to parse infrastructure data")
            payload, resources = {}, []
            issues = [f"unexpected structure ({type(exc).__name__}: {exc})"]

    state, title, detail = describe(snapshot, resources, payload, issues)

    # ยังไม่มีข้อมูลจริงให้แสดง: ใช้ข้อมูลตัวอย่างเป็น preview (ปิดได้ด้วย INFRA_SAMPLE_FALLBACK=0)
    if not resources and SAMPLE_FALLBACK:
        try:
            sample = load_sample()
            payload = unwrap(sample)
            resources = normalize(payload)[0]
            source = "mock"
            raw = sample if raw is None else raw  # ถ้ามีไฟล์จริงอยู่ ให้ช่อง raw แสดงของจริงไว้ตรวจ
            detail = (detail + " Sample data is shown below as a preview.").strip()
        except Exception:
            app.logger.exception("Failed to load sample data")
            payload, resources, source = {}, [], "none"
    elif not resources:
        source = "none"

    update_time = payload.get("update_time")
    if not update_time and source in ("live", "stale") and snapshot["data_path"]:
        try:
            update_time = datetime.fromtimestamp(
                os.path.getmtime(snapshot["data_path"])).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            update_time = None

    response = jsonify({
        "state": state,          # live | waiting | writing | invalid | schema
        "source": source,        # live | stale | mock | none
        "version": version,
        "message": title,
        "detail": detail,
        "file": display_path(snapshot["path"]) if snapshot["path"] else None,
        "watching": snapshot["watching"],
        "applying": snapshot["applying"],
        "schema": {"issues": issues, "ignored_keys": ignored},
        "update_time": str(update_time) if update_time else None,
        "meta": {
            "project": payload.get("project"),
            "environment": payload.get("environment"),
            "region": payload.get("region"),
        },
        "summary": summarize(resources),
        "resources": resources,
        "raw": raw,
    })
    response.headers["ETag"] = version
    response.headers["Cache-Control"] = "no-store"
    return response


def current_version():
    try:
        return get_snapshot()["version"]
    except Exception:  # stream ต้องไม่ตายเพราะอ่านไฟล์พลาดรอบเดียว
        app.logger.exception("Failed to check infrastructure files")
        return "error"


@app.route('/api/events', methods=['GET'])
def events():
    # Server-Sent Events: เบราว์เซอร์เปิดการเชื่อมต่อค้างไว้ แล้ว server แจ้งเมื่อข้อมูลเปลี่ยน
    # ใช้ version ของ snapshot (ผ่าน read guard แล้ว) จึงแจ้ง "ข้อมูลใหม่" เฉพาะเมื่อไฟล์เขียนเสร็จสมบูรณ์
    def stream():
        last = current_version()
        idle = 0
        yield ": connected\n\n"
        while True:
            time.sleep(EVENT_CHECK_SECONDS)
            current = current_version()
            if current != last:
                last = current
                idle = 0
                yield f"event: change\ndata: {current}\n\n"
            else:
                idle += EVENT_CHECK_SECONDS
                if idle >= EVENT_HEARTBEAT_SECONDS:
                    idle = 0
                    yield ": ping\n\n"  # กัน proxy ตัดการเชื่อมต่อที่เงียบนานเกินไป

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route('/')
def dashboard():
    # โหลดไฟล์ index.html จากโฟลเดอร์ templates
    return render_template('index.html')


if __name__ == '__main__':
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=debug, threaded=True)
