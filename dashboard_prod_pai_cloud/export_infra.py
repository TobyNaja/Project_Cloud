"""ดึง Terraform output มาเป็นไฟล์ JSON ให้ dashboard โดยไม่แตะโฟลเดอร์ Terraform

รันหลัง `terraform apply` สำเร็จ:

    python export_infra.py

สคริปต์นี้เรียก `terraform output -json` ซึ่งเป็นคำสั่งอ่านอย่างเดียว (ไม่ lock และไม่แก้ state)
แล้วเขียนผลลัพธ์ลงไฟล์ในโฟลเดอร์ของ dashboard แบบ atomic (เขียนไฟล์ชั่วคราว ตรวจว่าเป็น JSON ครบ
แล้วค่อยสลับชื่อ) dashboard จึงไม่มีโอกาสเห็นไฟล์ที่เขียนค้างอยู่ และไม่มีไฟล์ใดถูกสร้างใน prodpai_cloud

ตัวแปรที่ปรับได้:
    TERRAFORM_DIR   โฟลเดอร์ Terraform (ค่าเริ่มต้น ../prodpai_cloud)
    INFRA_FILE      ไฟล์ปลายทาง (ค่าเริ่มต้น current_infra.json ในโฟลเดอร์นี้)
    TERRAFORM_BIN   ชื่อหรือ path ของ terraform (ค่าเริ่มต้น terraform)
"""
import json
import os
import subprocess
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve(path):
    path = os.path.expanduser(os.path.expandvars(path.strip().strip('"')))
    return os.path.abspath(path if os.path.isabs(path) else os.path.join(BASE_DIR, path))


def main():
    terraform_dir = resolve(os.environ.get("TERRAFORM_DIR", os.path.join("..", "prodpai_cloud")))
    target = resolve(os.environ.get("INFRA_FILE", "current_infra.json"))
    terraform = os.environ.get("TERRAFORM_BIN", "terraform")

    if not os.path.isdir(terraform_dir):
        sys.exit(f"Terraform folder not found: {terraform_dir}")
    try:
        if os.path.commonpath([os.path.dirname(target), terraform_dir]) == terraform_dir:
            sys.exit("INFRA_FILE points inside the Terraform folder. Refusing to write there.")
    except ValueError:  # คนละไดรฟ์บน Windows
        pass

    try:
        result = subprocess.run(
            [terraform, f"-chdir={terraform_dir}", "output", "-json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
    except FileNotFoundError:
        sys.exit(f"Could not run '{terraform}'. Install Terraform or set TERRAFORM_BIN.")
    except subprocess.TimeoutExpired:
        sys.exit("terraform output timed out. The existing dashboard file was left untouched.")
    if result.returncode != 0:
        sys.exit(f"terraform output failed. The existing dashboard file was left untouched.\n{result.stderr.strip()}")

    try:
        outputs = json.loads(result.stdout)
    except ValueError as exc:
        sys.exit(f"terraform output did not return valid JSON ({exc}). Nothing was written.")
    if not isinstance(outputs, dict):
        sys.exit("terraform output did not return a JSON object. Nothing was written.")

    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(outputs, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target)
    except OSError as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        sys.exit(f"Could not write {target}: {exc}")

    print(f"Wrote {len(outputs)} Terraform output(s) to {target}")


if __name__ == "__main__":
    main()
