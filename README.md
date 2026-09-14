# AI-Radar 🤖 AI 动态雷达

**39 个信息源 → 6 层筛选漏斗 → LLM 智能评估 → 结构化中文编译 → 定时推送邮件**

不是 RSS 阅读器，是一个**理解信息价值的自动化情报系统**：它读完全文后判断重要性、
翻译成中文、写结构化分析报告，然后按你定的节奏推送到邮箱 —— **打开邮件就是阅读终点，
不需要开代理、不需要点外链**。

```
海外官方(OpenAI/DeepMind/Google/Microsoft/NVIDIA/HF) ─┐
海外媒体(TechCrunch/MIT TR/Ars/Wired)               ├─→ 关键词预筛 ─→ 跨源去重(同主题留1条)
海外快讯(smol.ai/AlphaSignal/Latent Space)          │                    ↓
社区雷达(HN双档/LMArena/GitHub Trending)            ├─→ LLM影响力评估(1-10分)
论文雷达(HF每日论文/arXiv重大)                       │                    ↓
国产官方(DeepSeek/智谱/Qwen/Kimi HF监视)             ┤              门槛拦截(≤5分丢弃)
中文媒体(量子位/降权)                               ─┘                    ↓
                                                                      攒批/冷却(日均1-2封)
                                                                           ↓
                                                              📧 HTML邮件(卡片+中文编译全文)
```

## 快速复用（3 步）

### 1. 克隆 + 配置

```bash
git clone https://github.com/<你的用户名>/ai-radar.git
cd ai-radar
pip install certifi                    # 唯一依赖
cp secrets.example.json secrets.local.json
# 编辑 secrets.local.json：
#   - 填一个推送通道（推荐 SMTP 邮件，免费不限量）
#   - 填 user_profile（你的职业背景，驱动"个人相关性"判断）
python radar.py --test                 # 验证通道
```

### 2. 跑一轮看看效果

```bash
python radar.py                        # 首跑建基线（不推送）
python radar.py --send-now             # 立即扫描+评估+推送（体验完整流程）
python radar.py --dry-run              # 只看会发什么，不发送
```

### 3. 装定时任务（自动运行）

```powershell
# Windows：每 2 小时 :45 评估 → 整点 :00 发送
powershell -ExecutionPolicy Bypass -File install-task.ps1
```

Linux/macOS 用 cron：
```
45 */2 * * * cd /path/to/ai-radar && python3 radar.py --eval-only
0 1-23/2 * * * cd /path/to/ai-radar && python3 radar.py --send-only
```

## 核心机制

### 六层筛选漏斗（80 条候选 → 4 条入池 → 1 封邮件）

| 层 | 干什么 | 效果 |
|---|---|---|
| 1. 关键词预筛 | 每个源自定义关键词 | 砍掉不相关源内容 |
| 2. 跨源去重 | 同一主题多家报道只留最高分代表 | 省 30~50% LLM 调用 |
| 3. LLM 影响力评估 | 通读正文，输出 1-10 分 | 10=全球旗舰发布，9=国产旗舰，8=重要工具… |
| 4. 门槛拦截 | ≤5 分丢弃；中文媒体单独 ≤6 | 营销/体验帖/边缘内容沉底 |
| 5. 个人相关性 | 高相关可救 5 分入池 | 与你技术栈匹配的项目不漏 |
| 6. 攒批+冷却 | 攒够条数/时间或重大新闻触发 | 日均 1~2 封 |

### 分型编译（每条动态自带完整中文内容）

| 内容类型 | 邮件里的格式 |
|---|---|
| 新闻/模型发布 | 整理 → 翻译 → 总结，500~1200 字编译稿 |
| 工具/项目 | 这是什么 → 为什么对你有用 → 怎么上手 → 别人怎么用 → 注意事项 |
| 论文 | 核心发现 + 实际意义 |

### 双维度独立评估

- **影响力**（通用）：不受个人偏好污染，反映全行业分量
- **个人相关性**（定制）：由 `user_profile` 驱动，高相关项目加 ★ 标记并可豁免门槛

## 消息源（sources.json）

39 个活跃源，六类覆盖。加源/关源/改关键词改这个文件即可：

| 类别 | 示例 | 配置方式 |
|---|---|---|
| 海外官方 | OpenAI/DeepMind/Google/MS/NVIDIA/HF | 官方 RSS 直连 |
| 海外媒体 | TechCrunch/MIT TR/Ars/Wired/Interconnects | 垂类 RSS |
| 海外快讯 | smol.ai AI News(X聚合)/AlphaSignal | Newsletter RSS |
| 社区雷达 | HN 双档(300/150分)/LMArena/GitHub Trending | 热度过滤 |
| 论文雷达 | HF 每日论文/arXiv 重大(HN≥150) | 社区精选 |
| 国产官方 | DeepSeek GitHub/智谱/Qwen/Kimi HF 监视 | tags.atom/watch |

特殊源类型：
- `"type": "watch"` — 监视页面变化（如 LMArena 新模型上榜、HF 新权重上架）
- `"skip_score": true` — 跳过标题评分（适合纯版本号源）
- `"search_desc": true` — 关键词也搜摘要（适合学术源）

## 推送通道（五选一，全免费）

| 通道 | 配置键 | 说明 |
|---|---|---|
| **邮件** | `SMTP_HOST/PORT/USER/PASS/TO` | 推荐，QQ 邮箱免费不限量；`smtp_to` 逗号分隔多收件人；测试消息只发首个 |
| 企业微信 | `WECOM_WEBHOOK` | 群机器人 |
| Server酱 | `SERVERCHAN_KEY` | 微信推送，5 条/天 |
| WxPusher | `WXPUSHER_TOKEN`+`UID` | 微信推送 |
| PushPlus | `PUSHPLUS_TOKEN` | 备用 |

## 摘要通道（三级降级）

| 级别 | 配置 | 说明 |
|---|---|---|
| 1. OpenAI 兼容 API | `SUMMARY_API_KEY/BASE/MODEL` | DeepSeek/GLM/SiliconFlow 均可 |
| 2. 本机 Claude Code CLI | 自动检测 | 走已有订阅 |
| 3. 抽取式 | 零配置 | 正文前 3 句，永远兜底 |

## 调参（config.json）

| 键 | 默认 | 含义 |
|---|---|---|
| `min_score` | 4 | 关键词预筛门槛 |
| `high_score` | 7 | 关键词高分兜底线 |
| `high_influence` | 8 | LLM 影响力线：≥此值立即发 |
| `influence_floor` | 5 | 通用入池门槛（≤此值丢弃） |
| `influence_floor_cn` | 6 | 中文媒体单独门槛（降权） |
| `batch_min_items` | 6 | 攒几条发一封 |
| `batch_max_age_minutes` | 300 | 最老条目等待上限 |
| `send_cooldown_minutes` | 600 | 普通邮件冷却（10h≈1-2封/天） |
| `summary_language` | 中文 | 编译输出语言 |
| `summary_mode` | auto | auto/api/claude |

## 命令速查

```
python radar.py                # 常规：扫描+评估+推送
python radar.py --eval-only    # 只扫描+评估入池，不推送（配合定时 :45）
python radar.py --send-only    # 只处理待发池（配合定时整点 :00）
python radar.py --send-now     # 立即推送（测试用）
python radar.py --dry-run      # 只看会发什么
python radar.py --test         # 推送通道测试（只发首个收件人）
python radar.py --init         # 重建基线（换源后防旧消息涌入）
```

## 已知边界

- 国内网络下部分海外源偶发 SSL 断连 → 自动走本机代理（`RADAR_PROXY` 环境变量）或下轮重试
- 某些公司无官方 RSS（xAI/Mistral/MiniMax/月之暗面）→ 由 HN/快讯层兜底
- LLM 评估耗时与条目数成正比 → 跨源去重已省 30~50% 调用

## 许可

[MIT License](LICENSE) · 零依赖纯 Python · 消息源数据来自各公司官方渠道、[RSSHub](https://docs.rsshub.app/)、[hnrss](https://hnrss.org/)、arXiv、HuggingFace
