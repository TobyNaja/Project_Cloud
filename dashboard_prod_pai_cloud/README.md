# Prodpai Cloud Dashboard

หน้าเว็บแสดงสถานะ infrastructure ที่สร้างด้วย Terraform ในโฟลเดอร์ `../prodpai_cloud`
dashboard อ่านไฟล์ JSON ที่ได้จาก `terraform output` แล้วอัปเดตหน้าจอเองเมื่อไฟล์เปลี่ยน

> dashboard **อ่านอย่างเดียว** ไม่มีคำสั่งใดในคู่มือนี้ที่แก้ไฟล์ `.tf` หรือเปลี่ยน resource บน AWS

## สารบัญ

1. [เริ่มรัน dashboard](#1-เริ่มรัน-dashboard)
2. [ข้อมูลมาจากไหน](#2-ข้อมูลมาจากไหน)
3. [เปลี่ยนจากข้อมูลตัวอย่างเป็นข้อมูลจริง](#3-เปลี่ยนจากข้อมูลตัวอย่างเป็นข้อมูลจริง)
4. [ข้อความสถานะบนหน้าเว็บ](#4-ข้อความสถานะบนหน้าเว็บ)
5. [Environment variables](#5-environment-variables)
6. [แก้ปัญหาที่พบบ่อย](#6-แก้ปัญหาที่พบบ่อย)

---

## 1. เริ่มรัน dashboard

ต้องมี Python 3.11 ขึ้นไป คำสั่งทั้งหมดในคู่มือนี้ใช้กับ Git Bash

```bash
cd "/d/IT/Year 3/Term 1/Cloud/dashboard_prod_pai_cloud"
pip install -r requirements.txt
python dashboard_app.py
```

เปิด <http://localhost:5000> ในเบราว์เซอร์ กด `Ctrl+C` ใน terminal เพื่อหยุด

ถ้ายังไม่มีข้อมูลจริง หน้าเว็บจะแสดงแถบ **Waiting for Terraform state** พร้อมข้อมูลตัวอย่างและป้าย **Sample data**
ซึ่งเป็นเรื่องปกติ ทำตามหัวข้อ 3 เพื่อเปลี่ยนเป็นข้อมูลจริง

## 2. ข้อมูลมาจากไหน

dashboard เฝ้าดูไฟล์ตามรายการด้านล่าง แล้วใช้ไฟล์ที่ **ถูกแก้ไขล่าสุด**

| ไฟล์ | ใครเป็นคนเขียน |
|---|---|
| `current_infra.json` (ในโฟลเดอร์นี้) | `export_infra.py` หรือ `POST /api/update` |
| `../prodpai_cloud/outputs.json` | คุณเอง ถ้ารัน `terraform output -json > outputs.json` |
| `../prodpai_cloud/terraform_output.json` | เช่นเดียวกัน |
| `../prodpai_cloud/current_infra.json`, `currentinfra.json` | เช่นเดียวกัน |

ถ้าไม่พบไฟล์ใดเลย จะใช้ `current_infra_sample.json` เป็นข้อมูลตัวอย่าง

รองรับรูปแบบ JSON ทั้งสองแบบของ Terraform:

```bash
terraform output -json                          # ทุก output (dashboard จะหยิบ infrastructure_summary เอง)
terraform output -json infrastructure_summary   # เฉพาะ output เดียว
```

## 3. เปลี่ยนจากข้อมูลตัวอย่างเป็นข้อมูลจริง

`terraform apply` ของโปรเจกต์นี้รันบน GitHub Actions ไม่ใช่ในเครื่องของคุณ
เครื่องของคุณจึงยังไม่มีไฟล์ output จนกว่าจะทำตามขั้นตอนนี้

ขั้นที่ 1 ถึง 3 ทำครั้งเดียว หลังจากนั้นใช้ขั้นที่ 4 อย่างเดียว

### ขั้นที่ 1: ตรวจ AWS credentials

```bash
aws sts get-caller-identity
```

ถ้าได้ผลลัพธ์เป็น `UserId`, `Account`, `Arn` แปลว่าใช้ได้ ไปขั้นที่ 2

ถ้าขึ้น `ExpiredToken` หรือ `InvalidClientTokenId` ให้คัดลอก credentials ชุดใหม่มาใส่ในไฟล์ `~/.aws/credentials`:

```ini
[default]
aws_access_key_id = ...
aws_secret_access_key = ...
aws_session_token = ...
```

โปรเจกต์นี้ใช้ session token ซึ่งหมดอายุได้ ถ้าวันหลังขั้นที่ 4 ล้ม ให้กลับมาเช็กขั้นนี้ก่อน

### ขั้นที่ 2: เชื่อม Terraform กับ state บน S3

```bash
cd "/d/IT/Year 3/Term 1/Cloud/prodpai_cloud"
terraform init -lockfile=readonly
```

สิ่งที่คำสั่งนี้ทำ:

- ดาวน์โหลด provider และเชื่อมกับ state ใน S3 bucket `prodpai-tfstate-storage`
- สร้างโฟลเดอร์ `.terraform/` ซึ่งถูก gitignore ไว้แล้ว
- `-lockfile=readonly` กันไม่ให้ไฟล์ `.terraform.lock.hcl` ถูกแก้

ถ้า init ฟ้องว่า lock file ไม่มี checksum สำหรับ Windows ให้ใช้วิธีนี้แทน:

```bash
terraform init
cd ..
git checkout -- prodpai_cloud/.terraform.lock.hcl   # คืน lock file เป็นของเดิม
```

### ขั้นที่ 3: ทดสอบว่าอ่าน state ได้

```bash
cd "/d/IT/Year 3/Term 1/Cloud/prodpai_cloud"
terraform output infrastructure_summary
```

ควรเห็นค่า `vpc`, `subnets`, `load_balancer` และอื่นๆ
ถ้าขึ้นว่าไม่มี output แปลว่า infrastructure ถูก destroy ไปแล้ว dashboard จะแสดงสถานะรอ

### ขั้นที่ 4: ส่งข้อมูลให้ dashboard

```bash
python "/d/IT/Year 3/Term 1/Cloud/dashboard_prod_pai_cloud/export_infra.py"
```

เมื่อสำเร็จจะขึ้น `Wrote 2 Terraform output(s) to ...current_infra.json`
หน้าเว็บจะเปลี่ยนป้ายเป็น **Live file** เองภายในประมาณ 1 วินาที ไม่ต้องกด refresh

รันขั้นนี้ซ้ำทุกครั้งที่ pipeline apply รอบใหม่ หรือเปิด terminal อีกหน้าต่างให้ดึงเองทุก 60 วินาที:

```bash
while true; do python "/d/IT/Year 3/Term 1/Cloud/dashboard_prod_pai_cloud/export_infra.py"; sleep 60; done
```

### สิ่งที่ห้ามทำ

อย่ารัน `terraform apply` หรือ `terraform destroy` จากเครื่องของคุณ
ทั้งสองคำสั่งเปลี่ยน production จริง ขั้นตอนข้างบนใช้เฉพาะ `init` และ `output` ซึ่งอ่านอย่างเดียว

### ทางเลือก: ไม่ใช้ export_infra.py

ถ้าอยากสั่งเองตรงๆ ก็ได้ dashboard จะตรวจเจอไฟล์เอง:

```bash
cd "/d/IT/Year 3/Term 1/Cloud/prodpai_cloud"
terraform output -json > outputs.json
```

ข้อแตกต่างคือวิธีนี้สร้างไฟล์ไว้ในโฟลเดอร์ `prodpai_cloud` (ต้องระวังไม่ commit ไฟล์นี้)
และระหว่างที่ไฟล์กำลังถูกเขียน หน้าเว็บจะขึ้น **Terraform output is being written** ชั่วครู่
ส่วน `export_infra.py` เขียนไฟล์ชั่วคราวแล้วค่อยสลับชื่อ หน้าเว็บจึงไม่เห็นไฟล์ที่เขียนค้าง

## 4. ข้อความสถานะบนหน้าเว็บ

| ข้อความ | ความหมาย | ต้องทำอะไร |
|---|---|---|
| (ไม่มีแถบ) ป้าย **Live file** | อ่านข้อมูลจริงได้ครบ | ไม่ต้องทำอะไร |
| **Waiting for Terraform state** | ยังไม่พบไฟล์ หรือไฟล์ไม่มี output | ทำหัวข้อ 3 |
| **Terraform output is being written** | ไฟล์กำลังถูกเขียน ยังไม่ครบ | รอ หน้าเว็บจะอัปเดตเองเมื่อเขียนเสร็จ |
| **Terraform output could not be read** | ไฟล์ค้างเสียเกิน 10 วินาที | รันขั้นที่ 4 ใหม่ |
| **Unrecognised Terraform output format** | เป็น JSON ถูกต้อง แต่ไม่มี key ที่รู้จัก | ดูช่อง "Incoming JSON data" ว่าได้อะไรมา |
| **Infrastructure was destroyed** | pipeline แจ้งว่า destroy แล้ว | รอ apply รอบถัดไป |
| ป้าย **Previous data** | ไฟล์ใหม่ยังใช้ไม่ได้ จึงแสดงข้อมูลชุดก่อนหน้า | ดูข้อความในแถบสถานะ |
| ป้าย **Sample data** | กำลังแสดงข้อมูลตัวอย่าง ไม่ใช่ของจริง | ทำหัวข้อ 3 |

หน้าเว็บไม่ล่มในทุกกรณีข้างต้น ถ้าไฟล์ใหม่ใช้ไม่ได้ จะคงข้อมูลชุดล่าสุดที่อ่านได้ครบไว้ให้ดูก่อน

## 5. Environment variables

ทุกตัวไม่บังคับ ตั้งใน shell ก่อนรัน เพราะแอปไม่ได้โหลดไฟล์ `.env` เอง (ดูรายการเต็มใน `.env.example`)

| ตัวแปร | ค่าเริ่มต้น | ใช้ทำอะไร |
|---|---|---|
| `INFRA_JSON_PATH` | (ว่าง) | ไฟล์หรือโฟลเดอร์ที่ให้อ่าน แทนการหาอัตโนมัติ |
| `TERRAFORM_DIR` | `../prodpai_cloud` | โฟลเดอร์ Terraform ที่ใช้หาไฟล์อัตโนมัติ |
| `INFRA_FILE` | `current_infra.json` | ไฟล์ที่ `export_infra.py` และ `/api/update` เขียน |
| `INFRA_OUTPUT_KEY` | `infrastructure_summary` | ชื่อ output ที่จะหยิบมาใช้ |
| `INFRA_WRITE_GRACE_SECONDS` | `10` | ไฟล์ที่อ่านไม่ได้ภายในกี่วินาทีให้ถือว่ากำลังเขียน |
| `INFRA_SAMPLE_FALLBACK` | `1` | `0` เพื่อไม่แสดงข้อมูลตัวอย่างระหว่างรอ |
| `PORT` | `5000` | พอร์ตของเว็บ |

path แบบ relative อิงจากโฟลเดอร์ `dashboard_prod_pai_cloud` เสมอ ไม่ขึ้นกับว่ารันคำสั่งจากที่ไหน

ตัวอย่าง:

```bash
# ชี้ไปที่ไฟล์เดียว
INFRA_JSON_PATH=../prodpai_cloud/outputs.json python dashboard_app.py

# ไม่เอาข้อมูลตัวอย่าง และเปลี่ยนพอร์ต
INFRA_SAMPLE_FALLBACK=0 PORT=8080 python dashboard_app.py
```

```powershell
# PowerShell
$env:INFRA_JSON_PATH = "..\prodpai_cloud\outputs.json"
python dashboard_app.py
```

## 6. แก้ปัญหาที่พบบ่อย

**หน้าเว็บยังเป็น Sample data หลังรันขั้นที่ 4**
ดูว่ามีไฟล์ `current_infra.json` ในโฟลเดอร์นี้หรือไม่ และดูข้อความที่ `export_infra.py` พิมพ์ออกมา
ถ้าสคริปต์ล้ม ไฟล์เดิมจะไม่ถูกแตะ

**`Could not run 'terraform'`**
Terraform ไม่อยู่ใน PATH ให้ระบุตำแหน่งเอง:
`TERRAFORM_BIN="/d/Terraform/terraform" python export_infra.py`

**`terraform output failed` พร้อมข้อความ `Backend initialization required`**
ยังไม่ได้ทำขั้นที่ 2

**`terraform output failed` พร้อมข้อความเรื่อง credentials หรือ `AccessDenied`**
credentials หมดอายุ กลับไปทำขั้นที่ 1

**ข้อมูลบนหน้าเว็บเก่ากว่าของจริง**
เครื่องของคุณไม่รู้ว่า pipeline apply รอบใหม่แล้ว จนกว่าจะรันขั้นที่ 4 อีกครั้ง
เวลา "Last Terraform apply" บนหัวเว็บคือเวลาที่ apply ไม่ใช่เวลาที่ดึงข้อมูล

**อยากกลับไปดูหน้าสถานะรอ**
ลบไฟล์ `current_infra.json` ในโฟลเดอร์นี้ หน้าเว็บจะเปลี่ยนเองทันที

**พอร์ต 5000 ถูกใช้อยู่**
รันด้วย `PORT=5001 python dashboard_app.py`
