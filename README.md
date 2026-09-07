# AI-Radar 🤖 AI 大厂动态雷达

第一手盯住 AI 公司的发布渠道，**粗筛 → LLM 双维度评估 → 主题聚类 → 攒批发送**，
把信息噪音压缩成每天 1~2 封高密度邮件。

```
海外官方(OpenAI/DeepMind/Google/Anthropic RSS) ─┐
国产官方(DeepSeek GitHub tags)                 ├─> 评分过滤(噪音强扣) ─> 主题聚类(10条报道=1条)
社区雷达(HN热帖/r/LocalLLaMA/LMArena榜单监视) ┤                                ├─> LLM三句话摘要 ─> 攒批发邮件
论文雷达(HF每日论文/arXiv重大论文)             │                                │    (重大新闻立即发)
中文媒体(量子位)                               ─┘                                └─> 抽取式兜底(零配置可用)
```

## 三层降噪（不是每条新闻都发）

1. **双维度评估**：关键词先粗筛（主体词 +2/个、发布动作 +3、版本号 +2；宕机吐槽/教程/硬件折腾强扣，不过线直接丢弃）；
   过线条目由 LLM 通读正文打两个分：**通用影响力**（1-10，标尺见调参表）+ **个人相关性**（高/中/低，高相关标 ★）；
   影响力≤`influence_floor` 不入池，唯一豁免：高个人相关且影响力 5 分。个人画像存 `secrets.local.json` 的 `USER_PROFILE`（或环境变量），不入库
2. **主题聚类**：同一事件的多条报道（官方博客+HN+中文媒体常常三连发）聚合成一条，
   领衔条目带摘要 + "相关报道 N 条"
3. **攒批发送**（默认 1~2 封/天）：攒够 6 条或最老一条等满 5 小时才发；
   普通邮件间隔冷却 10 小时；**重大新闻（高分）跳过所有规则立即发**——
   各家一起放大招的日子一天十几封也正常，平淡的日子一封都没有也正常

所有节奏参数在 `config.json` 里可调。

## 快速开始

### 方式 A：本地常驻（Windows，推荐）

```powershell
git clone https://github.com/<你的用户名>/ai-radar.git
cd ai-radar
copy secrets.example.json secrets.local.json   # 填入你的推送通道（下表五选一）
python radar.py --test                          # 验证通道
python radar.py                                 # 跑一轮（首跑建基线不发送）
powershell -ExecutionPolicy Bypass -File install-task.ps1   # 注册两阶段任务：Eval 每2小时:45 评估 / Send 每2小时整点发送
```

### 方式 B：GitHub Actions 云端 24/7

1. 使用本仓库模板新建仓库
2. Settings → Secrets → Actions 添加推送通道 secret（下表）
3. 编辑 `.github/workflows/radar.yml`，把 `cron` 两行取消注释
4. Actions 页手动 Run 一次（首跑建基线）

> ⚠️ 云端与本机二选一，同时开会双倍发邮件（两边状态独立）。

## 推送通道（五选一，全免费）

| 通道 | secrets / secrets.local.json 键 | 获取 |
|---|---|---|
| **邮件**（推荐） | `SMTP_HOST/PORT/USER/PASS/TO` | QQ邮箱：设置→账户→开SMTP→授权码 |
| 企业微信机器人 | `WECOM_WEBHOOK` | 群机器人 Webhook，不限量 |
| Server酱 | `SERVERCHAN_KEY` | sct.ft07.com，免费 5 条/天 |
| WxPusher | `WXPUSHER_TOKEN`+`WXPUSHER_UID` | wxpusher.zjiecode.com |
| PushPlus | `PUSHPLUS_TOKEN` | pushplus.plus |

## 摘要通道（三级自动降级，语言可配）

| 级别 | 配置 | 说明 |
|---|---|---|
| 1. OpenAI 兼容 API | `SUMMARY_API_KEY`（+可选 `SUMMARY_API_BASE`/`SUMMARY_MODEL`） | DeepSeek/GLM/SiliconFlow/OpenAI 均可，几分钱/天 |
| 2. 本机 Claude Code CLI | 装了 Claude Code 即自动发现（`SUMMARY_MODE=claude` 可强制） | 走你已有的 CLI 订阅 |
| 3. 抽取式摘要 | 零配置 | 正文前 3 句，永远兜底 |

摘要语言由 `config.json` 的 `summary_language` 控制（默认 `中文`，可改 `English`、`日本語` 等）。

## 调参（config.json）

| 键 | 默认 | 含义 |
|---|---|---|
| `min_score` | 4 | 入选门槛，调高更严 |
| `high_score` | 7 | 关键词高分兜底线（LLM 不可用时） |
| `high_influence` | 8 | LLM 影响力线：≥此值立即发+冷却豁免（标尺：10=全球旗舰发布，9=国产旗舰，8=重要衍生/旗舰工具） |
| `influence_floor` | 5 | 影响力≤此值不入池（高个人相关且=5 者豁免） |
| `batch_min_items` | 6 | 攒几条发一封 |
| `batch_max_age_minutes` | 300 | 最老条目等待上限 |
| `send_cooldown_minutes` | 600 | 两封普通邮件最小间隔（10 小时 ≈ 每天 1~2 封） |
| `max_per_email` | 12 | 单封邮件（聚类后）条数上限 |
| `old_cutoff_days` | 7 | 发布时间超过 N 天的条目直接忽略 |
| `summary_mode` | auto | auto / api / claude（+抽取式兜底） |
| `summary_language` | 中文 | 摘要输出语言 |

消息源在 `sources.json`：官方 RSS 直连 + GitHub tags + HN 关键词热帖 + LMArena 榜单
变动监视（新模型上榜即报，含匿名款被识破）+ HF 每日论文 + arXiv 重大论文（HN≥150 赞）
+ 国产官方 HF 模型库监视（GLM/Qwen/Kimi 新权重上架即报）+ 量子位。加源/关源/改关键词改这个文件即可。

## 命令

```
python radar.py              # 常规扫描（定时任务调用的就是这个）
python radar.py --dry-run    # 只看会发什么，不发送
python radar.py --send-now   # 立即把待发池整封发出
python radar.py --eval-only   # 只扫描+评估+入池（定时 :45 调用）
python radar.py --send-only   # 只处理待发池（定时整点发送窗口）
python radar.py --test       # 推送通道测试
python radar.py --init       # 重建基线（换源后防止旧消息涌入）
python radar.py --export-md  # 导出最近7天发送历史为markdown素材（可跟天数：--export-md 14）
python radar.py --profile-suggest  # 用近30天★高相关记录起草新版个人画像
```

## 致谢与许可

数据来自各公司官方渠道、[RSSHub](https://docs.rsshub.app/)、[hnrss](https://hnrss.org/)、
arXiv、HuggingFace、LMArena、量子位。[MIT License](LICENSE)
