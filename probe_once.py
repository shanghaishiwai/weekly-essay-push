import json, os, ssl, urllib.error, urllib.request
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
AT = (os.environ.get("WXPUSHER_SPT") or "").strip()
print("appToken:", AT[:6], "长度", len(AT))

def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"}),
                                    timeout=40, context=CTX) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")

def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")

print()
print("### A. 关注用户列表")
body = get("https://wxpusher.zjiecode.com/api/fun/wxuser/v2?appToken=" + AT + "&page=1&pageSize=50")
print(body[:600])
uids = []
try:
    d = json.loads(body).get("data") or {}
    for rec in (d.get("records") or []):
        if rec.get("uid"):
            uids.append(rec["uid"])
            print("   发现 UID:", rec["uid"])
except Exception as e:
    print("   解析失败:", e)

print()
print("### B. 尝试把 UID 写进环境（用文件传给下一步）")
if uids:
    with open("found_uid.txt", "w") as f:
        f.write(uids[0])
    print("   已写入 found_uid.txt:", uids[0])
    print()
    print("### C. 用标准接口发一条测试消息")
    body = post("https://wxpusher.zjiecode.com/api/send/message", {
        "appToken": AT,
        "content": "【体检消息】微信推送通道已打通！\n以后每周五 20:20 会自动收到一条高考作文素材。",
        "summary": "WxPusher 体检消息", "contentType": 1, "uids": [uids[0]]})
    print(body[:600])
else:
    print("   还没有关注用户")
