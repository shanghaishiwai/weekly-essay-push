# weekly-essay-push（每周作文素材自动推送）

每周五 20:20（北京时间）自动调用 DeepSeek 生成 1 个人物素材，并推送到**微信**。

## 推送通道说明（重要）

原先用的 **WxPusher 微信公众号渠道已经关停**，它现在只能推送到它自己的客户端 App。
所以现在主力通道改成了 **PushPlus（推送加）**，它走微信公众号，能真正进微信。

代码支持三种通道，**自动识别**，哪个配了就用哪个（优先级从上到下）：

| 优先级 | 通道 | 需要的 Secret | 能进微信 |
| --- | --- | --- | --- |
| 1 | **PushPlus** | `PUSHPLUS_TOKEN` | ✅ 能 |
| 2 | WxPusher 标准推送 | `WXPUSHER_SPT`（`AT_` 开头） | ❌ 只能进 App |
| 3 | WxPusher 极简推送 | `WXPUSHER_SPT`（`SPT_` 开头） | ❌ 只能进 App |

## 文件结构

```
weekly-essay-push/
├── .github/
│   └── workflows/
│       └── weekly_push.yml    # GitHub Actions 定时任务（cron: 20 12 * * 5）
├── main.py                    # 主程序：调 DeepSeek → 推送到微信
├── diagnose.py                # 体检工具：排查 Secrets 配置问题
├── requirements.txt           # 依赖（只有 requests）
└── README.md
```

## 用到的密钥（不写进代码，只放 GitHub Secrets）

| 名称 | 说明 | 在哪里拿 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 的 API Key，`sk-` 开头 | platform.deepseek.com → API keys |
| `PUSHPLUS_TOKEN` | PushPlus 的 token，32 位字母数字 | pushplus.plus → 一对一推送 |

> ⚠️ PushPlus 从 2024 年 8 月起**必须实名认证**才能调用发送接口（免费），
> 未认证会返回 `905` 错误码。认证地址：https://verify.pushplus.plus

可选：在仓库 `Settings → Secrets and variables → Actions → Variables` 里加一个变量
`DEEPSEEK_MODEL`（例如 `deepseek-chat`），不加就默认用 `deepseek-chat`。

## PushPlus 免费额度（够用很久）

| 用户类型 | 微信渠道每日可推送 |
| --- | --- |
| 未实名 | 0 次 |
| **实名（免费）** | **200 次** ← 我们每周只用 1 次 |
| 会员（付费） | 2,000 次 |

## 本地手动测试（可选，会真的发一条微信）

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY="sk-你的key"
$env:PUSHPLUS_TOKEN="你的32位token"
python main.py
```

## 时间说明

- GitHub Actions 的 cron 用的是 **UTC 时间**，所以 `20 12 * * 5` 才是北京时间周五 20:20。
- GitHub 定时任务在高峰期可能延迟几分钟到十几分钟，属于正常现象。
- GitHub 免费账号：仓库连续 60 天没有活动，定时任务会被自动暂停。
  发现没收到时，去 Actions 页面手动点一次 **Run workflow** 即可恢复。

## 出问题时怎么排查

1. 打开仓库的 **Actions** 页面，点最近一次运行，展开 `调用 DeepSeek 生成素材` 那一步看日志
2. 日志里会明确打印：
   - 用了哪个推送通道
   - DeepSeek 是否生成成功
   - PushPlus 是否受理（`code: 200` 表示受理成功）
3. 也可以手动跑 `diagnose.py`（工作流里有"令牌体检"这一步）来检查 Secrets 配置
