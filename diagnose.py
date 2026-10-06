#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
体检脚本：判断 GitHub Secrets 里存的令牌是什么类型、能不能用。
只打印"类型和长度"，绝不打印令牌内容。
同时把结论写进 GitHub 的「作业摘要」，方便直接查看。
"""

import json
import os
import sys
import urllib.error
import urllib.request

STD_SEND = "https://wxpusher.zjiecode.com/api/send/message"
STD_USERS = "https://wxpusher.zjiecode.com/api/fun/wxuser/v2"
SPT_SEND = "https://wxpusher.zjiecode.com/api/send/message/simple-push"

SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY")
HEALTH_ONLY = os.environ.get("ONLY_HEALTH", "") == "1"

summary_lines = []


def say(msg=""):
    print(msg, flush=True)


def note(msg=""):
    """同时输出到日志和 GitHub 摘要"""
    print(msg, flush=True)
    summary_lines.append(msg)


def flush_summary():
    if not SUMMARY_FILE:
        return
    try:
        with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(summary_lines) + "\n")
    except Exception as e:
        print("写摘要失败:", e, flush=True)


def line(ch="-", n=64):
    print(ch * n, flush=True)


def post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def code_of(raw):
    try:
        return json.loads(raw).get("code")
    except Exception:
        return None


def main():
    note("## 🔍 Secrets 体检结果")
    note()

    # ---------- 1. 找出所有相关 Secret 的存在情况 ----------
    names = ["WXPUSHER_SPT", "WXPUSHER_APPTOKEN", "WXPUSHER_UID",
             "WXPUSHER_TOKEN", "WXPUSHER_APP_TOKEN", "APPTOKEN", "UID"]
    present = {n: (os.environ.get(n) or "").strip() for n in names}
    has_deepseek = bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip())

    note("### 一、Secrets 存在情况")
    note()
    note("| Secret 名字 | 是否存在 | 值的长度 | 开头 4 位 |")
    note("| --- | --- | --- | --- |")
    for n in names:
        v = present[n]
        if v:
            note(f"| `{n}` | ✅ 有 | {len(v)} | `{v[:4]}` |")
        else:
            note(f"| `{n}` | ⬜ 无 | - | - |")
    note(f"| `DEEPSEEK_API_KEY` | {'✅ 有' if has_deepseek else '❌ **没有！**'} | "
         f"{len(os.environ.get('DEEPSEEK_API_KEY') or '') if has_deepseek else '-'} | - |")
    note()

    # 找出第一个可用的推送令牌
    token_name, token = None, None
    for n in names:
        v = present[n]
        if v.startswith(("SPT_", "AT_")):
            token_name, token = n, v
            break

    if not token:
        note("### ⚠️ 结论：没有找到任何可用的推送令牌")
        note()
        note("所有 Secret 里都没有 `SPT_` 或 `AT_` 开头的内容。")
        flush_summary()
        return

    note(f"### 二、使用的令牌：`{token_name}`（`{token[:4]}` 开头，{len(token)} 位）")
    note()

    # ---------- 2. 按类型处理 ----------
    if token.startswith("SPT_"):
        note("类型：**极简推送 SPT** → 应使用 `simple-push` 接口")
        if HEALTH_ONLY:
            note()
            note("（体检模式：不发送消息）")
            flush_summary()
            return
        note()
        note("正在用 SPT 发送测试消息……")
        status, raw = post(SPT_SEND, {
            "spt": token,
            "content": ("【体检消息】GitHub Actions 已成功连接 WxPusher，微信推送通道正常。\n"
                        "以后每周五 20:20 会收到一条高考作文素材。"),
            "summary": "WxPusher 体检消息", "contentType": 1,
        })
        code = code_of(raw)
        note(f"- HTTP 状态码：`{status}`")
        note(f"- 业务返回码：`{code}`")
        note()
        if status == 200 and code == 1000:
            note("### 🎉 发送成功！")
            note()
            note("请去微信查看是否收到【体检消息】。")
        else:
            note("### ❌ 发送失败")
            note()
            note(f"返回内容：`{str(raw)[:300]}`")
        flush_summary()
        return

    # appToken 分支
    note("类型：**标准推送 appToken** → 应使用标准接口（当前 main.py 用的是 simple-push，需要改）")
    note()
    note("正在用 appToken 查询关注用户……")
    status, raw = get(f"{STD_USERS}?appToken={token}&page=1&pageSize=50")
    print("原始返回:", str(raw)[:400], flush=True)

    uids = []
    try:
        result = json.loads(raw)
        d = result.get("data")
        if isinstance(d, dict):
            d = d.get("records") or d.get("list") or []
        for u in (d or []):
            if isinstance(u, dict) and u.get("uid"):
                uids.append(u["uid"])
    except Exception:
        pass

    note(f"- 查询 HTTP 状态码：`{status}`")
    note(f"- 查到关注用户数：`{len(uids)}`")
    if uids:
        note(f"- 用户 UID：`{uids[0]}`")
    note()

    if not uids:
        note("### ⚠️ 没查到关注用户")
        note()
        note("可能原因：appToken 无效，或者还没有人扫码关注这个应用。")
        note(f"接口原始返回：`{str(raw)[:300]}`")
        flush_summary()
        return

    if HEALTH_ONLY:
        note("（体检模式：不发送消息）")
        note()
        note("### ✅ appToken 有效，且已有关注用户")
        flush_summary()
        return

    uid = uids[0]
    note("正在发送测试消息……")
    status, raw = post(STD_SEND, {
        "appToken": token,
        "content": ("【体检消息】GitHub Actions 已成功连接 WxPusher（标准推送），"
                    "微信推送通道正常。\n以后每周五 20:20 会收到一条高考作文素材。"),
        "summary": "WxPusher 体检消息", "contentType": 1, "uids": [uid],
    })
    code = code_of(raw)
    note(f"- 发送 HTTP 状态码：`{status}`")
    note(f"- 发送业务返回码：`{code}`")
    note()
    if status == 200 and code == 1000:
        note("### 🎉 发送成功！")
        note()
        note("请去微信查看是否收到【体检消息】。")
    else:
        note("### ❌ 发送失败")
        note()
        note(f"返回内容：`{str(raw)[:300]}`")
    flush_summary()


if __name__ == "__main__":
    try:
        main()
    finally:
        flush_summary()
