#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每周作文素材自动推送（v2 —— 反重复 + 反老套 + 模板化）

核心改造：
1. 反重复：维护 history.json，记录用过的人物，下次作为"禁用名单"注入提示词，
   并在生成后做【程序级校验】——如果撞了就自动重新生成。
2. 反老套：两阶段生成。第一阶段让模型自己列候选并逐条自评，淘汰套路货；
   第二阶段只对幸存者深挖。提示词里内置常见老套人物黑名单。
3. 模板化：输出固定为四段式，板块标签统一，可直接套用/背诵。

推送通道：PushPlus（微信公众号）
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# ---------------- 常量 ----------------
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

PUSHPLUS_URL = "https://www.pushplus.plus/send"

WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message/simple-push"
WXPUSHER_STD_URL = "https://wxpusher.zjiecode.com/api/send/message"
WXPUSHER_USERS_URL = "https://wxpusher.zjiecode.com/api/fun/wxuser/v2"

# Secrets 可能用的名字
PUSHPLUS_NAMES = ["PUSHPLUS_TOKEN", "PUSHPLUS", "PUSH_PLUS_TOKEN"]
SPT_NAMES = ["WXPUSHER_SPT", "WXPUSHER_TOKEN"]
APPTOKEN_NAMES = ["WXPUSHER_APPTOKEN", "APPTOKEN", "WXPUSHER_APP_TOKEN"]
UID_NAMES = ["WXPUSHER_UID", "UID"]

# 历史记录文件名（存在仓库根目录，每次运行后更新）
HISTORY_FILE = "history.json"
HISTORY_KEEP = 60          # 最多保留最近多少条
RECENT_FOR_PROMPT = 25     # 注入提示词时给模型看多少条
GENERATE_ATTEMPTS = 4      # 撞车时最多重新生成几次
MAX_TOKENS_STAGE1 = 1600   # 第一阶段（选人）token 上限
MAX_TOKENS_STAGE2 = 1400   # 第二阶段（成文）token 上限

CN_TZ = timezone(timedelta(hours=8))

# ---------------- 提示词（第一阶段：自己选人）----------------
STAGE1_SYSTEM = """你是一位带过 20 届高三的作文教研员，专门为学生挑选"用了就能得分"的作文人物素材。

你的信条：
· 素材的第一价值是【真】，第二价值是【新】，第三价值是【能用】。
· 编造事实、夸大功绩、张冠李戴，是对学生最大的伤害——你绝不这样做。
· 网上传滥的人物（爱迪生、居里夫人、司马迁、苏轼、袁隆平、钟南山、张桂梅、樊锦诗、苏炳添这类）
  阅卷老师已经看吐了，写上去不加分反而减分。你要主动避开它们。"""

STAGE1_USER_TMPL = """任务：为高考作文挑选【1 位】人物，并给出选择理由。

【本周禁区】以下人物写过或写滥了，绝对不能再选：
{ban_list}

【候选要求】先在脑子里列 4 位候选人物，然后按下面的标准逐个打分淘汰，最后只保留 1 位：

筛选标准（按重要程度排序）：
1. 真实性：事迹必须是可公开查证的客观事实，不能是网络传言、二手夸张说法。
2. 新鲜度（关键）：优先"名字不算家喻户晓，但事迹足够硬"的人；
   避免"人人都写过"的人和"最近被刷屏到烂"的人。
3. 具体性：有明确的年份、地点、动作、数字，而不是空泛的"他一生奉献"。
4. 可论证性：事迹能对应到高考常见命题（梦想、坚持、选择、创新、担当、困境突围、
   传统与创新、个人与时代、科学与人文、蛰伏与爆发等）。
5. 领域多样性：如果上面禁区里最近已经出现过某领域（比如航天/体育），
   这次尽量换个领域（建议在 科学、医学、工程、文学、艺术、教育、农业、环保、
   商业、公益、考古、体育、军事、法律 之间轮换）。

【输出格式】只输出下面这个 JSON，不要任何多余文字、不要 markdown 代码块：
{{
  "candidates": [
    {{"name": "候选1姓名", "field": "所属领域", "fresh": 1到10的整数，越大越新鲜, "why": "一句话理由"}},
    {{"name": "候选2姓名", "field": "所属领域", "fresh": 1到10的整数, "why": "一句话理由"}},
    {{"name": "候选3姓名", "field": "所属领域", "fresh": 1到10的整数, "why": "一句话理由"}},
    {{"name": "候选4姓名", "field": "所属领域", "fresh": 1到10的整数, "why": "一句话理由"}}
  ],
  "picked": "最终选中的姓名（必须是候选之一）",
  "field": "最终人物所属领域",
  "reject_reason": "为什么淘汰了另外几位（一句话，要说清是老套、还是不够具体）"
}}"""

# ---------------- 提示词（第二阶段：深挖成文）----------------
STAGE2_SYSTEM = """你是一位给高三学生供稿的作文素材编辑。你的文字标准是：

· 短句为主，读者一眼能扫完，适合手机阅读和背诵。
· 只写"可查证的动作和结果"，不写形容词堆砌的赞美。
· 论点句必须能直接抄进作文，且要有锋利的判断，不写"我们要学习他"这种废话。
· 拒绝所有空洞词汇：伟大、崇高、无私奉献、令人敬佩、值得我们学习。"""

STAGE2_USER_TMPL = """请为这位人物写一份高考作文素材卡：{name}（领域：{field}）

【事实纪律】
1. 只写你能确认的公开事实。任何不确定的数字、年份、称号，一律不写。
2. 不要编造名言。如果他确实没有广为流传的原话，就不要写引语。
3. 不要堆砌头衔，最多保留一个最关键的。

【写法要求】
1. 事迹控制在 3-5 句，必须包含至少 2 个具体细节（时间/地点/数字/动作）。
2. 论点是"可以直接抄进作文"的完整句子，要有力度和思辨性，不要口号。
3. 全文（含标题）控制在 400 字以内。
4. 不要输出任何开场白、结尾语、署名。

【输出格式】严格遵守，每个板块的标签原样保留，板块之间空一行：

【人物】
（一行：姓名 + 一句话身份定位，不超过 25 字）

【事迹速记】
（3-5 句，每句一行，以 · 开头，句句带具体细节）

【可适配题材】
（一行：3-4 个题材关键词，用 、 分隔）

【论点金句】
（一句话，20-45 字，可直接抄进作文，要有锋利判断）

【一句话记忆】
（一句话，15-30 字，用最精炼的方式概述这个人为什么值得写）

现在开始写，直接以【人物】开头。"""

# 常见老套人物（注入禁区的兜底黑名单，避免模型钻空子）
CLICHE_FIGURES = [
    "爱迪生", "居里夫人", "爱因斯坦", "牛顿", "达尔文", "特斯拉",
    "司马迁", "苏轼", "屈原", "陶渊明", "李白", "杜甫", "王阳明",
    "孔子", "孟子", "曾国藩", "鲁迅", "钱学森", "邓稼先", "袁隆平",
    "钟南山", "张桂梅", "樊锦诗", "苏炳添", "全红婵", "杨利伟",
    "马云", "任正非", "乔布斯", "马斯克", "比尔盖茨",
]


# ---------------- 基础工具 ----------------
def log(msg: str) -> None:
    print(f"[{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_env(name: str, required: bool = True) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value and required:
        log(f"❌ 缺少环境变量 {name}")
        sys.exit(1)
    return value


def first_env(names):
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return n, v
    return None, None


def now_str() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def clean_text(text: str) -> str:
    text = text.replace("**", "").replace("###", "").replace("##", "")
    text = re.sub(r"^\s*[-*]\s+", "· ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_for_wxpusher(text: str, limit: int = 3500) -> list:
    chunks, current = [], ""
    for line in text.split("\n"):
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


# ---------------- 历史记录（反重复的核心）----------------
def load_history() -> list:
    """读取 history.json（优先本地文件，因为工作流会先 checkout 仓库）。"""
    # 1. 本地文件（工作流 checkout 后就有）
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                log(f"✅ 读到历史记录 {len(data)} 条（本地文件）")
                return data
            log("⚠️ history.json 格式不是列表，忽略")
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️ 解析 history.json 失败：{exc}")

    # 2. 退路：从仓库 API 读
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log("ⓘ 没有历史文件，且无 GitHub 凭据，按首次运行处理")
        return []
    url = f"https://api.github.com/repos/{repo}/contents/{HISTORY_FILE}"
    try:
        import base64
        r = requests.get(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }, timeout=30)
        if r.status_code == 404:
            log("ⓘ 远端也没有历史记录，按首次运行处理")
            return []
        r.raise_for_status()
        data = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
        if isinstance(data, list):
            log(f"✅ 从仓库读到历史记录 {len(data)} 条")
            return data
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ 从仓库读历史失败：{exc}")
    return []


def save_history(history: list) -> None:
    """把更新后的历史写到本地 history.json（由工作流负责 git 提交）。"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        log(f"✅ 历史记录已写入 {HISTORY_FILE}（共 {len(history)} 条）")
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ 写历史失败（不影响本次推送）：{exc}")


def build_ban_list(history: list) -> str:
    """构造注入提示词的禁用名单。"""
    lines = []
    recent = history[-RECENT_FOR_PROMPT:]
    if recent:
        names = [f"{h['name']}（{h.get('field', '')}）" for h in recent]
        lines.append("· 最近已写过：" + "、".join(names))
        fields = [h.get("field", "") for h in recent if h.get("field")]
        if fields:
            recent_fields = "、".join(dict.fromkeys(fields[-6:]))
            lines.append(f"· 最近涉及领域（这次要避开或轮换）：{recent_fields}")
    lines.append("· 永久黑名单（一律不许选）：" + "、".join(CLICHE_FIGURES))
    return "\n".join(lines)


# ---------------- DeepSeek 调用 ----------------
def call_deepseek(api_key: str, model: str, messages: list,
                  temperature: float, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=150)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}：{resp.text[:200]}")
            data = resp.json()
            content = (data["choices"][0]["message"]["content"] or "").strip()
            if not content:
                raise RuntimeError("返回空内容")
            usage = data.get("usage") or {}
            log(f"   token 消耗：{usage.get('total_tokens', '?')}")
            return content
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log(f"   ⚠️ 第 {attempt} 次调用失败：{exc}")
            if attempt < 3:
                time.sleep(4 * attempt)
    raise RuntimeError(f"DeepSeek 连续失败：{last_error}")


def parse_json_loose(text: str):
    """从模型输出里尽量抠出 JSON。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ---------------- 两阶段生成 ----------------
def pick_figure(api_key: str, model: str, ban_list: str) -> dict:
    """第一阶段：让模型自己评估候选，选出最合适的人。"""
    log("【第一阶段】让模型自己列候选、自评新鲜度，再决定选谁…")
    content = call_deepseek(
        api_key, model,
        [{"role": "system", "content": STAGE1_SYSTEM},
         {"role": "user", "content": STAGE1_USER_TMPL.format(ban_list=ban_list)}],
        temperature=1.35, max_tokens=MAX_TOKENS_STAGE1,
    )
    data = parse_json_loose(content)
    if not isinstance(data, dict) or not data.get("picked"):
        log("   ⚠️ 第一阶段返回格式异常，改用纯文本模式兜底")
        return {"picked": None, "field": "", "candidates": [], "raw": content}

    cands = data.get("candidates") or []
    log(f"   候选 {len(cands)} 位，选中：{data.get('picked')}（领域：{data.get('field')}）")
    for c in cands:
        if isinstance(c, dict):
            log(f"      - {c.get('name')}  新鲜度={c.get('fresh')}  {c.get('why', '')}")
    if data.get("reject_reason"):
        log(f"   淘汰理由：{data['reject_reason']}")
    return data


def write_card(api_key: str, model: str, name: str, field: str) -> str:
    """第二阶段：只对选中的人深挖成文。"""
    log(f"【第二阶段】深挖 {name}，按模板成文…")
    content = call_deepseek(
        api_key, model,
        [{"role": "system", "content": STAGE2_SYSTEM},
         {"role": "user", "content": STAGE2_USER_TMPL.format(name=name, field=field)}],
        temperature=1.0, max_tokens=MAX_TOKENS_STAGE2,
    )
    return clean_text(content)


def extract_name_from_card(card: str) -> str:
    """从成文里再抽一次人物名，用于最终校验。"""
    m = re.search(r"【人物】\s*\n\s*[^\u4e00-\u9fa5A-Za-z]*([\u4e00-\u9fa5]{2,4})", card)
    if m:
        return m.group(1)
    return ""


def is_duplicate(name: str, history: list) -> bool:
    """判断人物是否不合格：命中历史记录 或 命中永久黑名单，都算不合格。"""
    if not name:
        return False

    # 1) 永久黑名单（老套人物）
    for cliche in CLICHE_FIGURES:
        if cliche in name or name in cliche:
            return True

    # 2) 历史记录（写过的人）
    used = {h.get("name", "") for h in history}
    if name in used:
        return True
    # 姓名部分重合也算疑似重复
    for u in used:
        if u and len(u) >= 2 and (u in name or name in u):
            return True
    return False


# ---------------- 推送 ----------------
def build_payload_text(card: str, meta: dict) -> str:
    """在素材卡前后加上说明，形成最终推送内容。"""
    header = (
        f"📚 本周作文素材 · {meta['date']}\n"
        f"（{meta['field']}）\n"
        "————————————\n"
    )
    footer = (
        "\n————————————\n"
        "📝 用法：把【事迹速记】当论据，【论点金句】直接抄进分论点句，"
        "【可适配题材】用于快速判断能不能套这道题。"
    )
    return header + card + footer


def push_via_pushplus(token: str, content: str) -> None:
    title = "本周高考作文素材 " + datetime.now(CN_TZ).strftime("%m-%d")
    chunks = split_for_wxpusher(content)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        body = chunk if total == 1 else f"（第 {index}/{total} 部分）\n{chunk}"
        payload = {"token": token, "title": title, "content": body,
                   "template": "txt", "channel": "wechat"}
        last_error = None
        for attempt in range(1, 4):
            try:
                log(f"正在推送第 {index}/{total} 条（第 {attempt} 次尝试）…")
                resp = requests.post(PUSHPLUS_URL, json=payload, timeout=60)
                result = resp.json()
                if resp.status_code == 200 and result.get("code") == 200:
                    log(f"✅ 第 {index}/{total} 条已受理，流水号 {result.get('data')}")
                    last_error = None
                    break
                raise RuntimeError(f"PushPlus 异常：{result}")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log(f"⚠️ 第 {attempt} 次失败：{exc}")
                if attempt < 3:
                    time.sleep(5 * attempt)
        if last_error is not None:
            log(f"❌ 第 {index}/{total} 条推送失败")
            sys.exit(1)


def auto_fetch_uid(app_token: str):
    try:
        resp = requests.get(WXPUSHER_USERS_URL,
                            params={"appToken": app_token, "page": 1, "pageSize": 50},
                            timeout=60)
        data = resp.json()
        rows = data.get("data")
        if isinstance(rows, dict):
            rows = rows.get("records") or rows.get("list") or []
        for row in rows or []:
            if isinstance(row, dict) and row.get("uid"):
                return row["uid"]
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ 查询 UID 失败：{exc}")
    return None


def push_to_wxpusher(mode: str, token: str, content: str, uid=None) -> None:
    chunks = split_for_wxpusher(content)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        text = chunk if total == 1 else f"【作文素材 {index}/{total}】\n{chunk}"
        if mode == "spt":
            url = WXPUSHER_URL
            payload = {"spt": token, "content": text,
                       "summary": "本周作文素材", "contentType": 1}
        else:
            url = WXPUSHER_STD_URL
            payload = {"appToken": token, "uids": [uid], "content": text,
                       "summary": "本周作文素材", "contentType": 1}
        last_error = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, json=payload, timeout=60)
                result = resp.json()
                if resp.status_code == 200 and result.get("code") == 1000:
                    log(f"✅ 第 {index}/{total} 条推送成功")
                    last_error = None
                    break
                raise RuntimeError(f"WxPusher 异常：{result}")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < 3:
                    time.sleep(5 * attempt)
        if last_error is not None:
            log(f"❌ 第 {index}/{total} 条推送失败：{last_error}")
            sys.exit(1)


# ---------------- 主流程 ----------------
def main() -> None:
    log("=== 每周作文素材推送（v2 反重复版）开始 ===")

    api_key = get_env("DEEPSEEK_API_KEY")
    model = get_env("DEEPSEEK_MODEL", required=False) or DEFAULT_MODEL

    history = load_history()
    ban_list = build_ban_list(history)

    # 两阶段生成 + 反重复校验
    meta = None
    card = None
    final_name = ""
    final_field = ""

    for attempt in range(1, GENERATE_ATTEMPTS + 1):
        log(f"—— 第 {attempt}/{GENERATE_ATTEMPTS} 轮生成 ——")
        picked = pick_figure(api_key, model, ban_list)
        name = (picked.get("picked") or "").strip() or "（见正文）"
        field = (picked.get("field") or "").strip() or "未标注"

        new_card = write_card(api_key, model, name, field)

        # 程序级校验：是否撞车
        guessed = extract_name_from_card(new_card) or name
        if is_duplicate(guessed, history):
            if any(c in guessed or guessed in c for c in CLICHE_FIGURES):
                reason = "命中老套人物黑名单"
            else:
                reason = "已在历史记录里"
            log(f"   ❌ 不合格！「{guessed}」{reason}，重新生成…")
            ban_list += f"\n· 刚刚你写过的（本次绝对不许再写）：{guessed}"
            continue

        meta = {"name": guessed, "field": field, "date": now_str()}
        card = new_card
        final_name, final_field = guessed, field
        log(f"   ✅ 校验通过：{final_name} 是全新人物")
        break

    if not card:
        log("⚠️ 多轮都撞车，用最后一轮结果兜底推送")
        meta = {"name": final_name or "未知", "field": final_field or "未标注", "date": now_str()}
        card = new_card

    log("生成的素材卡：")
    print("-" * 50, flush=True)
    print(card, flush=True)
    print("-" * 50, flush=True)

    # 更新历史
    history.append({"name": meta["name"], "field": meta["field"], "date": meta["date"]})
    history = history[-HISTORY_KEEP:]
    save_history(history)

    # 推送
    final_text = build_payload_text(card, meta)

    pp_name, pp = first_env(PUSHPLUS_NAMES)
    spt_name, spt = first_env(SPT_NAMES)
    at_name, at = first_env(APPTOKEN_NAMES)
    uid_name, uid = first_env(UID_NAMES)

    if pp and len(pp) >= 20 and not pp.startswith(("AT_", "SPT_")):
        log("推送通道：PushPlus（微信公众号）")
        push_via_pushplus(pp, final_text)
    elif (at and at.startswith("AT_")) or (spt and spt.startswith(("AT_", "SPT_"))):
        tok = at if (at and at.startswith("AT_")) else spt
        if tok.startswith("AT_"):
            if not uid:
                uid = auto_fetch_uid(tok)
            if not uid:
                log("❌ 拿不到 UID，无法推送")
                sys.exit(1)
            log("推送通道：WxPusher 标准推送")
            push_to_wxpusher("standard", tok, final_text, uid)
        else:
            log("推送通道：WxPusher 极简推送")
            push_to_wxpusher("spt", tok, final_text)
    else:
        log("❌ 没有可用的推送令牌")
        sys.exit(1)

    log(f"=== 全部完成：{meta['name']}（{meta['field']}）===")


if __name__ == "__main__":
    main()
