import json, os, ssl, urllib.error, urllib.request
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
AT = (os.environ.get("WXPUSHER_SPT") or "").strip()
UID = "UID_2398Q5ljvRfmSTjn03mEvWqYaIWB"
print("appToken 前缀:", AT[:6], "长度:", len(AT))
print("目标 UID:", UID)

def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"}),
                                    timeout=40, context=CTX) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

print()
print("### A. 直接按 UID 发送（标准接口）")
st, body = post("https://wxpusher.zjiecode.com/api/send/message", {
    "appToken": AT, "content": "【探测1】按 UID 直发测试",
    "summary": "探测", "contentType": 1, "uids": [UID]})
print("HTTP", st)
print("返回:", body[:500])

print()
print("### B. 查关注用户列表")
st, body = get("https://wxpusher.zjiecode.com/api/fun/wxuser/v2?appToken=" + AT + "&page=1&pageSize=50")
print("HTTP", st)
print("返回:", body[:500])

print()
print("### C. 不带 uids，看会不会报错提示（试探接口行为）")
st, body = post("https://wxpusher.zjiecode.com/api/send/message", {
    "appToken": AT, "content": "【探测3】不带收件人", "summary": "探测", "contentType": 1})
print("HTTP", st)
print("返回:", body[:500])

print()
print("### D. 按 topic 群发试一下（不需要 uids）")
st, body = post("https://wxpusher.zjiecode.com/api/send/message", {
    "appToken": AT, "content": "【探测4】按主题群发", "summary": "探测",
    "contentType": 1, "topicIds": [1]})
print("HTTP", st)
print("返回:", body[:500])
