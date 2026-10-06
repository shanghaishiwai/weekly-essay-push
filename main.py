#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每周五 20:20（北京时间）自动执行：
1. 调用 DeepSeek API 生成一段高考作文素材
2. 通过 WxPusher 推送到微信

自动识别两种推送方式，你不用关心自己配的是哪种：
  · 极简推送：WXPUSHER_SPT = SPT_ 开头的令牌
  · 标准推送：WXPUSHER_APPTOKEN = AT_ 开头的令牌（UID 会自动查询）

必需的环境变量（在 GitHub Secrets 里配置）：
  DEEPSEEK_API_KEY  DeepSeek 的 API Key（sk- 开头）
  以及上面两种推送令牌中的任意一种
可选：
  DEEPSEEK_MODEL    默认 deepseek-chat
  WXPUSHER_UID      标准推送的收件人；不填会自动查询
"""

import os
import re
import sys
import time

import requests

# ---------------- 可调参数 ----------------
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message/simple-push"

# 标准推送（appToken 令牌）
WXPUSHER_STD_URL = "https://wxpusher.zjiecode.com/api/send/message"
# 标准推送：查询"谁关注了应用"（用来自动获取 UID）
WXPUSHER_USERS_URL = "https://wxpusher.zjiecode.com/api/fun/wxuser/v2"

# WxPusher 单条消息有长度上限（约 4000 字符），超过就自动拆成多条发送
WXPUSHER_MAX_LEN = 3500

# Secrets 可能用的名字（哪个有值就用哪个，兼容你随手起的名字）
SPT_NAMES = ["WXPUSHER_SPT", "WXPUSHER_TOKEN"]
APPTOKEN_NAMES = ["WXPUSHER_APPTOKEN", "APPTOKEN", "WXPUSHER_APP_TOKEN"]
UID_NAMES = ["WXPUSHER_UID", "UID"]

SYSTEM_PROMPT = (
    "你是一位带过多年高三语文的作文老师，专门为学生整理“能直接背下来用”的作文素材。"
    "你的素材必须是真实存在的公开人物，事迹准确，不编造数据、不虚构名言。"
    "你的语言简短、时髦、好记，拒绝空话套话。"
)

USER_PROMPT = """请为我精选 1 个真实准确、简短时髦的人物事例，用于高考作文，要求容易得分。

硬性要求：
1. 人物必须是真实存在的公开人物（可来自科技、体育、文化、科学、公益、创业等领域），事迹必须真实准确。
2. 优先选择近年热度高、阅卷老师熟悉、显得新鲜不落俗套的人物；避免被写烂的老掉牙例子（如爱迪生、居里夫人式套路）。
3. 全部内容控制在 400 字以内，句子短，好背，好默写。
4. 严格按下面三个板块输出，板块标题必须原样保留（含方括号），每个板块前面加一个对应的 emoji：

【人物】
（一行，写出人物姓名，可加一句身份/成就标签）

【几句话概括主要事迹】
（3-5 句话，只写关键动作和关键结果，要有具体细节，不要空泛评价）

【可适配题材，可论证的论点】
（先列出 2-4 个可适配的作文题材关键词，逗号分隔；再用一句话给出可以直接写进作文的、有力的论点句）

其他要求：
- 不要输出任何多余的开场白、结束语、署名或“希望对你有帮助”之类的话。
- 不要使用 Markdown 的 ** 加粗、### 标题或表格，直接用纯文本。
- 直接以【人物】开头。"""


# ---------------- 工具函数 ----------------
def log(msg: str) -> None:
    """打印日志，顺便把时间一起打出来，方便看 GitHub Actions 记录。"""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_env(name: str, required: bool = True) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value and required:
        log(f"❌ 缺少环境变量 {name}，请检查 GitHub Secrets 是否配置正确")
        sys.exit(1)
    return value


def clean_text(text: str) -> str:
    """去掉模型偶尔带出来的 Markdown 符号，保证微信里看着干净。"""
    text = text.replace("**", "").replace("##", "").replace("###", "")
    text = re.sub(r"^\s*[-*]\s+", "· ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_for_wxpusher(text: str, limit: int = WXPUSHER_MAX_LEN) -> list:
    """按行切分，保证每条不超过 limit 个字符。"""
    chunks, current = [], ""
    for line in text.split("\n"):
        # 单行就超长（几乎不会发生）时按字符硬切
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks or [""]


# ---------------- 第一步：生成素材 ----------------
def generate_material(api_key: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "temperature": 1.2,   # 稍高一点，保证每周内容不重样
        "max_tokens": 1200,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(1, 4):  # 网络抖动时最多重试 3 次
        try:
            log(f"正在调用 DeepSeek 生成素材（第 {attempt} 次尝试）…")
            resp = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=120)
            if resp.status_code != 200:
                raise RuntimeError(f"DeepSeek 返回 HTTP {resp.status_code}：{resp.text[:300]}")
            data = resp.json()
            content = (data["choices"][0]["message"]["content"] or "").strip()
            if not content:
                raise RuntimeError("DeepSeek 返回了空内容")
            usage = data.get("usage") or {}
            log(f"✅ 生成成功，本次消耗 token：{usage.get('total_tokens', '未知')}")
            return clean_text(content)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log(f"⚠️ 第 {attempt} 次失败：{exc}")
            if attempt < 3:
                time.sleep(5 * attempt)

    log(f"❌ DeepSeek 连续 3 次调用失败：{last_error}")
    sys.exit(1)


# ---------------- 第二步：推送到微信 ----------------
def first_env(names):
    """按顺序找第一个有值的环境变量，返回 (名字, 值)。"""
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return n, v
    return None, None


def describe_all():
    """把当前配置情况完整打印出来，方便排查（不打印完整令牌）。"""
    log("下载到本机的 Secrets 情况：")
    for n in SPT_NAMES + APPTOKEN_NAMES + UID_NAMES:
        v = (os.environ.get(n) or "").strip()
        if v:
            log(f"   ✅ {n}：长度 {len(v)}，开头 {v[:4]!r}")
        else:
            log(f"   ⬜ {n}：没有")


def resolve_push_channel():
    """判断该用哪种推送方式，返回 (方式, 令牌名, 令牌, uid)。"""
    spt_name, spt = first_env(SPT_NAMES)
    at_name, at = first_env(APPTOKEN_NAMES)
    uid_name, uid = first_env(UID_NAMES)

    # 优先看令牌本身长什么样，比名字更可靠
    if at and at.startswith("AT_"):
        if not uid:
            log("检测到 appToken，但没找到 UID，正在自动查询关注用户…")
            uid = auto_fetch_uid(at)
        if uid:
            return "standard", at_name, at, uid
        log("❌ 有 appToken 但拿不到 UID，无法指定接收人")
        return None, None, None, None

    if spt and spt.startswith("SPT_"):
        return "spt", spt_name, spt, None

    if spt and spt.startswith("AT_"):
        if not uid:
            uid = auto_fetch_uid(spt)
        if uid:
            return "standard", spt_name, spt, uid
        log("❌ 有 appToken 但拿不到 UID，无法指定接收人")
        return None, None, None, None

    if at:
        log(f"⚠️ {at_name} 的内容不以 AT_ 开头（开头是 {at[:4]!r}），仍按标准推送尝试")
        if not uid:
            uid = auto_fetch_uid(at)
        return "standard", at_name, at, uid

    return None, None, None, None


def auto_fetch_uid(app_token: str):
    """用 appToken 查询关注用户，自动取第一个 UID。"""
    try:
        resp = requests.get(
            WXPUSHER_USERS_URL,
            params={"appToken": app_token, "page": 1, "pageSize": 50},
            timeout=60,
        )
        data = resp.json()
        log(f"查询关注用户返回：code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data")
        if isinstance(rows, dict):
            rows = rows.get("records") or rows.get("list") or []
        for row in rows or []:
            if isinstance(row, dict) and row.get("uid"):
                log(f"✅ 自动获取到 UID：{row['uid']}")
                return row["uid"]
        log("⚠️ 没查到任何关注用户")
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ 查询 UID 失败：{exc}")
    return None


def push_to_wechat(mode: str, token: str, content: str, uid=None) -> None:
    chunks = split_for_wxpusher(content)
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        text = chunk if total == 1 else f"【作文素材 {index}/{total}】\n{chunk}"

        if mode == "spt":
            url = WXPUSHER_URL
            payload = {
                "spt": token,
                "content": text,
                "summary": "本周作文素材已送达",
                "contentType": 1,
            }
        else:
            url = WXPUSHER_STD_URL
            payload = {
                "appToken": token,
                "uids": [uid],
                "content": text,
                "summary": "本周作文素材已送达",
                "contentType": 1,
            }

        last_error = None
        for attempt in range(1, 4):
            try:
                log(f"正在推送第 {index}/{total} 条到微信（第 {attempt} 次尝试）…")
                resp = requests.post(url, json=payload, timeout=60)
                result = resp.json()
                if resp.status_code == 200 and result.get("code") == 1000:
                    log(f"✅ 第 {index}/{total} 条推送成功")
                    last_error = None
                    break
                raise RuntimeError(f"WxPusher 返回异常：{result}")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log(f"⚠️ 第 {attempt} 次失败：{exc}")
                if attempt < 3:
                    time.sleep(5 * attempt)

        if last_error is not None:
            log(f"❌ 第 {index}/{total} 条推送失败：{last_error}")
            sys.exit(1)


# ---------------- 主流程 ----------------
def main() -> None:
    log("=== 每周作文素材推送任务开始 ===")

    describe_all()

    api_key = get_env("DEEPSEEK_API_KEY")
    model = get_env("DEEPSEEK_MODEL", required=False) or DEFAULT_MODEL

    mode, token_name, token, uid = resolve_push_channel()
    if not mode:
        log("❌ 没能确定推送方式：请在 GitHub Secrets 里配置以下任意一组")
        log("   方案A（极简推送）：WXPUSHER_SPT = SPT_ 开头的令牌")
        log("   方案B（标准推送）：WXPUSHER_APPTOKEN = AT_ 开头的令牌，"
            "并在同一应用的关注用户里能查到 UID")
        sys.exit(1)
    log(f"✅ 推送方式：{'极简推送(SPT)' if mode == 'spt' else '标准推送(appToken)'}"
        f"，令牌来源：{token_name}" + (f"，收件 UID：{uid}" if uid else ""))

    material = generate_material(api_key, model)
    log("生成的素材内容如下：")
    print("-" * 40, flush=True)
    print(material, flush=True)
    print("-" * 40, flush=True)

    push_to_wechat(mode, token, material, uid)
    log("=== 全部完成，微信应该已经收到了 ===")


if __name__ == "__main__":
    main()
