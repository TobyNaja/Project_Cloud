import json
import base64
import gzip
import boto3
import os

# Connect AWS EC2 API
ec2 = boto3.client('ec2')

# Take Security Group' ID ( quarantine_security)
ISOLATED_SG_ID = os.environ.get('ISOLATED_SG_ID')

def lambda_handler(event, context):
    # 1. ถอดรหัสและขยายไฟล์ Log ที่ CloudWatch บีบอัดส่งมาให้
    cw_data = event['awslogs']['data']
    compressed_payload = base64.b64decode(cw_data)
    uncompressed_payload = gzip.decompress(compressed_payload)
    log_payload = json.loads(uncompressed_payload)
    
    # 2. วนลูปอ่านทุกเหตุการณ์ (Log Events) ที่ส่งมาในรอบนี้
    for log_event in log_payload.get('logEvents', []):
        try:
            # แปลงข้อความ Log (ของ Falco) ให้เป็น JSON Dictionary
            falco_alert = json.loads(log_event['message'])
            
            # ดึงข้อมูลสำคัญจาก Falco
            priority = falco_alert.get('priority', 'Unknown')
            rule     = falco_alert.get('rule', 'Unknown')
            output   = falco_alert.get('output', 'No details provided')
            hostname = falco_alert.get('hostname', '')
            
            # ==========================================
            # 3. ตั้งเงื่อนไข "การถูกยึดเครื่อง (Compromised)"
            # ==========================================
            # เช็คจากระดับความรุนแรง (Severity)
            critical_priorities = ["Critical", "Emergency", "Alert"]
            
            # หรือเช็คจาก "ชื่อพฤติกรรม" ที่แฮกเกอร์ชอบทำ
            compromised_rules = [
                "Terminal shell in container", 
                "Run shell untrusted",
                "Launch Privileged Container",
                "Write below etc"
            ]
            
            is_compromised = (priority in critical_priorities) or (rule in compromised_rules)
            
            if is_compromised:
                # สมมติว่าตั้งชื่อ Hostname เป็น Instance ID ไว้ (เช่น i-0abcd1234...)
                instance_id = hostname
                
                if instance_id.startswith('i-'):
                    # ==========================================
                    # 4. พิมพ์ Output รายละเอียด "ก่อน" ลงมือ (บันทึกลง Log ของ Lambda)
                    # ==========================================
                    print("="*60)
                    print(f"🚨 [THREAT DETECTED] EC2 Instance Compromised!")
                    print(f"   📌 Instance ID : {instance_id}")
                    print(f"   🔥 Severity    : {priority}")
                    print(f"   📜 Rule Broken : {rule}")
                    print(f"   📝 Details     : {output}")
                    print(f"   🛡️ Action      : Isolating instance to Air-Gap SG ({ISOLATED_SG_ID}) for Forensics...")
                    print("="*60)
                    
                    # ==========================================
                    # 5. สั่งตัดเน็ต (ย้ายเข้า Air-Gap SG)
                    # ==========================================
                    # คำสั่งนี้จะถอด SG เดิมออกทั้งหมด และใส่ SG ห้องกักกันเข้าไปแทน
                    ec2.modify_instance_attribute(
                        InstanceId=instance_id,
                        Groups=[ISOLATED_SG_ID]
                    )
                    
                    print(f"✅ [SUCCESS] Instance {instance_id} has been successfully air-gapped.")
                else:
                    print(f"⚠️ [WARNING] Threat detected, but hostname '{hostname}' is not a valid AWS Instance ID. Cannot isolate.")
            else:
                print(f"ℹ️ [INFO] Alert received (Priority: {priority}, Rule: {rule}). Not critical enough for isolation. Skipping.")
                
        except Exception as e:
            print(f"❌ [ERROR] Failed to process log event: {e}")
            print(f"Raw event: {log_event['message']}")
            
    return {
        "statusCode": 200,
        "body": "Log processing complete"
    }