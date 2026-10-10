#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每周作文素材自动推送（v3 —— 高分论据版）

v3 相对 v2 的关键调整：
  · 选人标准从"越冷门越好"改成【说服力 × 熟悉度 × 时效性】三维打分。
    既不选爱迪生这种被写烂的，也不选没人听过的——目标区间是
    "叫得响但没被作文素材书写烂"的当代人物（如大疆汪滔、蔡磊这类量级）。
  · 输出升级为【论据卡】：新增【论据价值】和【论证链示例】，
    学生可以照着"论据 → 论证 → 应用 → 升级写法"直接套用。
  · 保留 v2 的反重复机制（history.json + 程序级校验）和永久黑名单。

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

# ---------------- 人物可感知度参考（给模型当标尺，不是硬性白名单）----------------
# 说明：这些是"认知度够高、又不像爱迪生那样被写烂"的当代人物，供模型类比参考
FRESH_AND_KNOWN = [
    "大疆 汪滔", "渐冻症 蔡磊", "宇树科技 王兴兴", "DeepSeek 梁文锋",
    "《黑神话·悟空》冯骥", "嫦娥团队 青年工程师", "北斗 谢军",
    "张伟丽", "郑钦文", "潘展乐", "江梦南", "刘秀祥",
    "曹原", "颜宁", "王亚平", "徐立平", "南仁东", "黄大年",
    "陈立群", "张定宇", "汪品先", "叶聪", "张荣桥",
]

# ---------------- 提示词（第一阶段：自己选人）----------------
STAGE1_SYSTEM = """你是一位带过 20 届高三的作文教研员，专门为学生挑选"写进作文就能得分"的论据人物。

你的信条（按重要程度排序）：
1. 【说服力第一】这个人的事迹必须能**有力地证明一个观点**。学生抄上去，阅卷老师会觉得"这个论据真贴切"。
2. 【认知度第二】读者要**认识或至少不排斥**这个人。用一个人尽皆知的名字写不出新意，
   但用一个没人听过的名字，阅卷老师会觉得学生在硬凑——这是减分项，不是加分项。
   理想区间是：**在新闻/科技/文化圈叫得响，普通人也有印象，但还没被作文素材书写烂**。
3. 【真实第三】只写可查证的客观事实，不编造、不夸大、不张冠李戴。

你要主动避开的（两类）：
· 被写滥的（爱迪生、居里夫人、司马迁、苏轼、袁隆平、钟南山、张桂梅、樊锦诗、苏炳添…）
  —— 阅卷老师已经看吐了，写上去不加分反而减分。
· 太偏门的（只有专业圈知道、近三十年没有公开报道、没有可查证细节的人）
  —— 学生写了老师不认识，说服力归零。"""

STAGE1_USER_TMPL = """任务：为高考作文挑选【1 位】论据人物，并给出选择理由。

【本周禁区】以下人物写过或写滥了，不能再选：
{ban_list}

【认知度参考】下面这类人物是"叫得响但没被写烂"的示范，你可以照这个感觉找同类型的人
（不要直接选这些，而是找同等量级、同等新鲜度的人）：
{fresh_ref}

【怎么选】先在脑子里列 4 位候选，按下表的三个维度打分淘汰，最后只留 1 位。

评估维度（每项 1-10 分）：

| 维度 | 含义 | 打分标准 |
|---|---|---|
| **说服力** | 事迹能否有力证明某个观点 | 10 分 = 事迹与观点咬合极紧，学生抄上去立刻加分 |
| **熟悉度** | 读者对这个名字的认知程度 | 10 分 = 家喻户晓；7-8 分 = 新闻/科技圈知名、普通人有印象（**最佳区间**）；≤4 分 = 太冷门（**扣分项**） |
| **时效性** | 是否为当代人物、近十年仍有公开动态 | 10 分 = 近年活跃；≤4 分 = 几十年前的历史人物 |

硬性要求：
· 优先**当代、可感知**的人物：企业家、科学家、运动员、医生、技术工作者、创业者、
  公益人物等；观众"身边不远"的真实人物。
· 如果候选人是已故的历史人物，**直接淘汰**（除非他近十年仍被高频讨论且属于上述禁区之外）。
· 领域尽量和禁区里最近出现过的不同（轮换：科技/商业、医学、体育、科学、文化、公益、教育…）。

【输出格式】只输出下面这个 JSON，不要任何多余文字、不要 markdown 代码块：
{{
  "candidates": [
    {{"name": "候选1姓名", "field": "领域", "persuasive": 1到10, "familiar": 1到10, "recent": 1到10, "why": "一句话理由"}},
    {{"name": "候选2姓名", "field": "领域", "persuasive": 1到10, "familiar": 1到10, "recent": 1到10, "why": "一句话理由"}},
    {{"name": "候选3姓名", "field": "领域", "persuasive": 1到10, "familiar": 1到10, "recent": 1到10, "why": "一句话理由"}},
    {{"name": "候选4姓名", "field": "领域", "persuasive": 1到10, "familiar": 1到10, "recent": 1到10, "why": "一句话理由"}}
  ],
  "picked": "最终选中的姓名（必须是候选之一）",
  "field": "最终人物所属领域",
  "total_score": 选中者的三项分数之和,
  "reject_reason": "为什么淘汰了另外几位（要说清是太老套、太冷门、还是说服力不够）"
}}"""

# ---------------- 提示词（第二阶段：深挖成文）----------------
STAGE2_SYSTEM = """你是一位给高三学生供稿的作文素材编辑，你最擅长的是【把一个人物变成一件称手的论证武器】。

你的文字标准：
· 短句为主，手机上能一眼扫完，方便背诵。
· 只写"可查证的动作和结果"，不写形容词堆砌的赞美。
· 论点必须能直接抄进作文，且要有锋利的判断，不写"我们要学习他"这种废话。
· 拒绝空洞词汇：伟大、崇高、无私奉献、令人敬佩、值得我们学习、精神值得我们传承。"""

STAGE2_USER_TMPL = """请为这位人物写一份**高考作文论据卡**：{name}（领域：{field}）

【这篇卡的核心任务】
不是介绍这个人，而是**把这个人的事迹变成学生可以直接用的论据**。
学生读完应该能立刻明白：这个人能证明什么观点、怎么用在作文里。

【事实纪律】
1. 只写能确认的公开事实。不确定的数字、年份、称号，一律不写。
2. 不要编造名言。如果他确实没有广为流传的原话，就不要写引语。
3. 头衔最多保留一个最关键的。

【写法要求】
1. 事迹 3-5 句，每句以 · 开头，必须含具体细节（年份/地点/数字/动作）。
2. 论点要有力度和思辨性，是完整的判断句，不是口号。
3. 必须写出"为什么这个论据比常规例子更有说服力"。
4. 全文 450 字以内。不要开场白、结尾语、署名。

【输出格式】严格遵守，板块标签原样保留，板块之间空一行：

【人物】
（一行：姓名 + 一句话身份定位，不超过 25 字）

【核心事迹】
（4-5 句，每句一行，以 · 开头；要有年份/数字/具体动作；最后一句点出他最难的地方）

【论据价值】
（2-3 句：这个人的事迹最能证明什么？为什么他比"爱迪生式"的常规例子更打动人？
 要点出他身上的"反差"或"稀缺性"）

【可适配题材】
（一行：3-4 个题材关键词，用 、 分隔）

【论点金句】
（一句话，25-50 字，可以直接抄进作文的分论点句。要有锋利判断，不要口号）

【论证链示例】
（给一个学生可以直接套用的三段式，帮我写成这个格式，每段一行、以 · 开头）：
· 论据：（一句话概括这个人的事迹）
· 论证：论据如何支撑观点——常用写法是"这说明……"，你要写得更锋利
· 应用：适合放在讨论"XX 与 XX"关系的段落里，比如"个人选择与时代机遇"
· 升级写法：（对比：常规写法会怎么写 → 用这个论据可以怎么写）

【一句话记忆】
（一句话，15-30 字，最精炼地概括这个人为什么值得写）

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


def build_fresh_ref() -> str:
    """构造"叫得响但没写烂"的参照名单，给模型当标尺。"""
    return "、".join(FRESH_AND_KNOWN)


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
    """第一阶段：让模型按"说服力×熟悉度×时效性"评估候选，选出最合适的人。"""
    log("【第一阶段】让模型自己列候选，按 说服力/熟悉度/时效性 三维打分…")
    content = call_deepseek(
        api_key, model,
        [{"role": "system", "content": STAGE1_SYSTEM},
         {"role": "user", "content": STAGE1_USER_TMPL.format(
             ban_list=ban_list, fresh_ref=build_fresh_ref())}],
        temperature=1.3, max_tokens=MAX_TOKENS_STAGE1,
    )
    data = parse_json_loose(content)
    if not isinstance(data, dict) or not data.get("picked"):
        log("   ⚠️ 第一阶段返回格式异常，改用纯文本模式兜底")
        return {"picked": None, "field": "", "candidates": [], "raw": content}

    cands = data.get("candidates") or []
    log(f"   候选 {len(cands)} 位，选中：{data.get('picked')}（领域：{data.get('field')}）")
    for c in cands:
        if isinstance(c, dict):
            log(f"      - {str(c.get('name')):<6} "
                f"说服力={c.get('persuasive', '?')} 熟悉度={c.get('familiar', '?')} "
                f"时效性={c.get('recent', '?')}  {c.get('why', '')}")
    if data.get("total_score"):
        log(f"   选中者总分：{data['total_score']}")
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
        f"📚 本周作文论据 · {meta['date']}\n"
        f"（{meta['field']}）\n"
        "————————————\n"
    )
    footer = (
        "\n————————————\n"
        "📝 怎么用：\n"
        "①【核心事迹】当论据直接引用（有年份数字，老师爱看）\n"
        "②【论据价值】告诉你这个人比常规例子强在哪，写的时候点一句就显出水平\n"
        "③【论点金句】抄进分论点句\n"
        "④【论证链示例】照着套：论据 → 论证 → 应用 → 升级写法"
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
    log("=== 每周作文论据推送（v3 高分论据版）开始 ===")

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
