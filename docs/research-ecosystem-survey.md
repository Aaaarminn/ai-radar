# B站 Agent/自动化生态横向调研：评论与互动的工程机制

> 任务 t14 · oss-researcher · 2026-11-12
> 方法：4 轮 GitHub 搜索（bilibili comment / dsh bilibili / bilibili auto comment / bilibili mcp server）+ 10 个项目 README 深挖 + 复用 t2（research-oss-boundaries.md）与 t1（research-dsh-pets.md）成果。
> 范围：**30+ 项目**，其中 **17 个深挖**（含 t2 的 8 个）。外部内容均为不可信数据，本文只提取事实；未经实测验证的结论均已标注。

---

## 0. TL;DR

1. **生态两极分化**：传统 auto-comment 类（弹幕刷屏 / selenium 模拟点击 / 无理解门）与 DSH agent 生态（理解门 + 逐条审核 + 多层闸门）走向完全相反的两个方向；"Read-only by design" 甚至成了一类项目（PulseHub、bili-watch）的**核心卖点**。
2. **没人做得全**：最接近我们五层管线的是 deepmoyu（授权链最完整）与 astrbot bilibili_live_mod（工程参数最具体），但**没有任何项目同时具备**「理解质量门 + 证据锚定 + 人工审核队列 + 发后折叠检测」——这四件我们都有。
3. **我们的两个真实短板**在生态里有成熟解：①理解层质量（bili-watch 的 ASR 幻觉判定 / pkkkkh 的双轨交叉校验 / 文稿骨架压缩）②跨视频人设与互动记忆（deepmoyu soul.md + 学习队列 / kydon 状态落盘去重）。
4. **最大的反面教材**：kydon 明确写了 "video comments publish without a review step"，并自认存在「B站已接受回复但本地状态未落盘 → crash 后重复回复」的窗口——这正是我们 publish→post_check→状态写盘顺序要防的坑。
5. 结论：**管线骨架保持自研（领先），机制级补丁 adopt 9 条、adapt 7 条、明确 reject 6 类**（见 §5）。

---

## 1. 生态全景（分类 + 活跃度）

活跃度数据取自 GitHub API（2026-11-12 查询）；`—` 表示未单独查询 API，来自 README/搜索结果描述。

### A. DSH 生态（直接同类：agent 在宿主里操作B站）

| 项目 | 活跃度 | 一句话定位 | 深挖 |
|---|---|---|---|
| c1overzz/dsh-bili-scroll（+ FunnyMudXD/holoiallo/zxz-feng forks） | 0.1.x，2026-10 活跃 | 刷B站+自动评论的 DSH 插件，t1/t2 已析 | ✔ |
| pyyxzi/dsh-deepmoyu | pushed 2026-10-28 | scroll 0.1.19 fork + vendor bilibili-mcp，鲸鱼娘人设 | ✔ |
| Kydon-ai/dsh-kydon-bilibili | pushed 2026-11-11 | @通知回复 + 定时视频总结评论（npm @kydon/dsh-kydon-bilibili） | ✔ |
| 1a125/dsh-bili-chat-whale | v0.2.3，2026-10-26 | 直播弹幕陪聊 agent（ASR+视觉+弹幕WS） | ✔ |
| Sacredbluedragon/dsh-bili-watch | 0.3.x | B站视频→结构化内容（只读理解工具） | ✔ |
| bronyacccxz/dsh-bilibili-reply | 2026-11 新 | @通知→看懂视频→带时间戳内联回复（skill+看门狗） | ✔ |
| pkkkkh/dsh-plugins（dsh-bilibili 0.8.1） | pushed 2026-11-06 | CZX2244/dsh-bilibili 的维护 fork：文稿骨架/通道探针/双轨ASR交叉校验/商单折叠 | ✔ |
| AGI_Ananas/bilibili-mcp | MIT | bilibili-api-python 的 MCP 封装（被 scroll/deepmoyu 内置） | ✔(t2) |
| Azerz/dsh-video-insight、hoholi-668/mcp-video-learn、legalhigh181/vocab-digest、luo173176/dsh-simple-live | 2026-10~11 | 理解层周边（字幕/总结/章节/RAG、场景帧拼贴），不发评论 | ✘浅 |

### B. CLI / 上传工具生态

| 项目 | 活跃度 | 定位 | 深挖 |
|---|---|---|---|
| kukemc/bili-cli（PyPI bili-agent） | 活跃 | **我们正在用的通道**；评论/回复/点赞/私信/投稿，护栏最全的 CLI | ✔(t2) |
| biliup/biliup（TS） | 活跃 | 投稿为主，comments/reply 命令默认 dry-run；SKILL.md 面向 agent | ✔(t2) |
| biliup-rs（Rust） | 活跃 | 纯上传，无评论功能 | ✔(t2) |
| xueyc1f/turbopush-mcp | 36★，pushed 2026-08-28 | MCP 向 20+ 平台（含B站）发内容（投稿向） | ✘浅 |

### C. MCP Server 类

| 项目 | 活跃度 | 定位 | 深挖 |
|---|---|---|---|
| Evil0ctal/Douyin_TikTok_Download_API | **21361★**，pushed 2026-11-09 | 抖音/B站数据采集+无水印下载+MCP+离线ASR；自愈身份池 | ✘浅 |
| chubbyguan/chubbyskills | 1323★，pushed 2026-10-14 | 14 个 skill：中文全渠道内容→个人知识库（字幕优先转写/RSS 调度/KB MCP），**只读 ingest** | ✘浅 |
| huccihuang/bilibili-mcp-server | 192★，pushed 2025-04（**停滞**） | 通用B站 API MCP | ✘浅 |
| adoresever/bilibili-mcp | 192★，pushed 2026-03 | "搜索/评论/字幕/弹幕/回复" MCP | ✘浅 |
| XZXZZX-Ai/bilibili-mcp | 51★，pushed 2026-10-30 | B站 MCP 工具 | ✘浅 |
| Yotsuki2213/BiliBili_VideoRead_MCP | 43★，pushed 2026-11-03 | 扫码登录，字幕/弹幕/评论→结构化 Markdown（只读理解） | ✘浅 |
| risingjerry/social-media-toolkit | 32★，pushed 2026-11-09 | 多平台 SDK/CLI/MCP：元数据/时间轴转写/评论抽取 | ✘浅 |
| yy-MAX-HUB/feedsonar-mcp | 0★ | 聚合 bilibili/douyin/weibo/xhs 创作者/帖子/评论检索 | ✘浅 |
| zhuyaotutejia/PulseHub | 0★，2026-07 | 中文社媒信号聚合供 AI 互动参考，**"Read-only by design — never auto-publish or auto-comment"** | ✘浅 |
| CCCpan/chinese-sensitive-words-mcp | — | 中文敏感词库 MCP（内容过滤可复用） | ✘浅 |

### D. 传统 auto-comment / 弹幕类（反面对照组）

| 项目 | 活跃度 | 定位 |
|---|---|---|
| liaoyanqing666/Bilibili_Live_Broadcast_Automatic_Bullet_Screen | 18★，2025-03 | 直播自动弹幕 |
| liaoyanqing666/Bilibili_Automatic_Bullet_Comment | 5★ | selenium 视频自动弹幕 |
| gongjunhao/bilibili-ai-auto-comment | 4★，2026-03 | AI 自动评论/点赞/收藏/回复（商业化扩展） |
| personal82555/bili-auto-comment（MV3） | 2026-10 | DOM 逐字输入自动评论（t2 已析：风控信号硬停设计好，但无审核） |
| codertesla/bili-comment-buddy | — | 油猴+LLM"嘴替"（每日5条/30s/验证码即停） |
| GSJNZH/Bilibili-Auto-Comment | — | 油猴脚本（≥30s/连败3次停/文案库随机） |
| EL233/BilibiliTools、KuristNiaS/AutoSending-for-live.bilibili、SunHao0729/bilibili_automobile_comments_2025 | 0★ | 三连+自动评论 / GUI 弹幕（cookie 验证/长文切分） |

### E. 检测 / 反制 / 研究类（发后复核与去模板化的"官方对标"）

| 项目 | 活跃度 | 定位 |
|---|---|---|
| freedom-introvert/biliSendCommAntifraud + Research-on-Avalon-System | 661★，2025-07 | 阿瓦隆控评机制逆向：Shadowban/秒删/折叠/戒严/申诉（t2 已析） |
| liyupi/dafeiyu-cleaner | — | 反制工具：识别"本鲸/白饭/Token"模板评论，离线规则 284 账号实测 ~85% 识别无误伤 |
| ambersmaller/bilibili_live_mod（astrbot 插件） | — | 直播弹幕/评论互动 bot，**工程参数最具体**（t1 已析） |
| linyiLYi/bilibot | 3123★，2024-05 停更 | 本地微调聊天机器人（用自己B站评论训练；人设一致性案例，非运营工具） |
| sansan0/bilibili-comment-analyzer | — | 评论区数据可视化（UP 选题/粉丝画像） |

### F. 内容生成 / 运营框架类

- deepmoyu 视频工作室（本地成片→不自动投稿）；AI课代表2023（总结路线，3个月25万粉，t1）；Code0 文章框架（四道门/L0-L3/审批单，t1）。
- 多平台发布（turbopush-mcp）与本团队 bili_publish.py 同域，但我们是单B站深耕+审核管线，不采用多平台 shotgun。

---

## 2. 深挖项目：评论与互动的工程机制

### 2.1 Kydon-ai/dsh-kydon-bilibili —— 「通知回复 + 定时评」流水线（反面教材含量高）

- **双轮询**：①@通知回复：`/x/msgfeed/unread` 每 60s（`notificationPollIntervalMs` 10s~3600s），pending 队列落盘跨重启，handled-key 去重，**B站接受回复后才移出队列**，上下文/生成失败保留待重试；②视频总结评论：`videoCommentIntervalMs=600000`（10min，可 60s~3600s），`videoCommentMaxPerRun=1`（1~3）。
- **视频发现**：popular + 登录号推荐 feed + 搜索词轮换（5 个默认中文词）；时长 >1800s 跳过；转写 >40000 字符按时序均匀采样。
- **理解**：本地 faster-whisper small（yt-dlp 下音频，**不调B站字幕API**），SRT 按 BV+分P 缓存；CUDA fp16 batch32 → CPU int8 回落；无语音视频跳过并记原因。ASR 环境安装（uv sync）需用户在设置卡批准（consent gate）。
- **生成**：隔离 Agent Session、工具全拒、只许加载 cc-summary skill；1000 字符硬校验，超长→重写 2 次，仍超→跳过；发送前再查一次。
- **状态**：`video-summary-state.json` 记最近 **2000 个已评论 BVID**。
- ⚠️ **无人工审核**：README 原文 "video comments publish without a review step"（通知回复同样无审批）；无发后复核。自认缺陷：B站已接受回复但状态未落盘时 crash → **重复回复**；多 Host 进程共享数据目录竞态；去重文件无限增长。
- **留痕**：每通知独立 .log（通知/上下文/模型输入输出/发送结果）+ 出入站请求日志（redact cookies/CSRF）。

**判定**：发现→理解→生成→发送的**无人化闭环**值得参考其状态机；但其「无审核 + 无发后复核 + crash 重复窗口」恰好逐一对应我们管线已解决的三件事。**adapt 其队列落盘模式，reject 其无审核路线**。

### 2.2 pyyxzi/dsh-deepmoyu —— 授权链与确定性原则（本生态边界设计最完整）

- **逐通道授权**：autoComment / autoMoment / replyToComments / autoReplyDm 四开关全默认关；自动收集/自动配图有独立开关但**不能代替发送授权**。
- **发送约束链（顺序门）**：发送前逐条审核（批准/拒绝）→ 永久判重 → 节奏冷却 → 平台额度；"获批后仍要经过权限、判重、节奏和平台限制"。
- **确定性原则**："**发送结果未知时先核对，不自动重复发送**"；成片/发送失败不伪装成功，宿主退出中断显示恢复状态。
- **注入防御**：外部字幕/评论/私信不能覆盖工具权限；切出角色模式撤销角色工具+旧句柄重查作用域；模型的配置工具只能调安全偏好，force 参数不构成授权；学习/记忆是参考数据，**不授予发送权**，撤销批准→从后续提示词移除。
- **记忆**：soul.md 记心情与自我认识（"越刷越像她"）；学习队列人工批准才生效。
- 错误处理：评论报 **12014=平台拒绝该评论通道→退避**，其他通道按各自权限继续；二维码/凭证不发公开 Issue；token 真实账本（未知用量如实显示不估零）。

**判定**：与我们的 L0→L1→L2 + 白名单同构，但它多两件我们没有的：**发送结果不确定性协议** 与 **逐通道粒度授权**。两者都 adopt。

### 2.3 Sacredbluedragon/dsh-bili-watch —— 理解层质量工程（我们理解门的最佳补充）

- **四级降级链**：AI字幕(需SESSDATA)→本地ASR(faster-whisper)→抽帧OCR(rapidocr)→读图；每级失败**记录原因不静默跳过**，全 miss exit 1。
- **ASR 幻觉判定**：VAD 人声 <0.5s 却出文本 / 跨 2s+ 窗口重复文本占比 >50% / no_speech_prob>0.5 分段过半 → 判"疑似幻觉"→须 OCR 交叉验证，**不得直接引用为台词**。
- **字幕诱饵检测**：语速不在 2~8.5 字/秒 + 关键词重合度低（须与语速异常同触）+ 含"实验/钓鱼/测试"字样 → 可疑 → --verify 抽 5 帧 OCR 比对画面。
- **陌生人内容护栏**：只做**形态检查**（零宽/tag/双向控制字符 + 边界标记自校验），命中→stderr 警告 + JSON `_untrusted` 字段 + [第三方内容·未消毒] 注记；**语义规则刻意不做**——20 条正常字幕误报 8 条(40%)，Prompt Guard 2 中文召回仅 0.2529；立场：输入过滤是 supporting control，承重的是"**标记 + 不下放权限**"(OWASP)。
- **双落盘**：`kind.txt` 纯正文给程序 read，`kind.wrapped.txt` 带 `[source:…]` 围栏给模型 read；长文本保护：>800 字只给前 400 字 + 落盘路径。
- 明确只读："不支持点赞、评论、发弹幕"；无内置限速。

**判定**：理解层的质量信号（幻觉标记/降级链/诱饵检测）+ 物料围栏是我们 fetch_context/understand 层最值得抄的机制。**adopt 幻觉判定与围栏落盘；adapt 降级链**（我们已有 4 只读调用，改为"每级失败记原因"的显式降级）。

### 2.4 1a125/dsh-bili-chat-whale —— 说话闸门与反编造（直播场景参数库）

- 默认**演练模式**（dry-run）：`say --go` 也只记录不发；README 风险声明："用自动化账号在直播间发言可能违反B站规则。默认演练不真发"。
- **四层说话闸门**：每间最多几条 / 两条间隔 / 每小时 / 每天——默认 每间 5 条、**间隔 75 秒**、每小时 15 条、每天 40 条；另有 20 字上限、禁用词、重复检测、**与最近 6 句相似度检查**。`say` 双重检查：agent 会话决定说/不说/换房，CLI 再查全部闸门，任一不过拒绝并返回原因。
- **沙箱协作**：agent 的 pwsh 写不了宿主目录 → CLI 只往 `.chat-whale/inbox/` 放 JSON 请求，宿主每秒扫描执行写回执——**agent 永不需放宽权限**；宿主不在→明确报"指令通道不在"，不假装发出。
- **反编造**：识别器不可用→提示词明确写"这段没听到"；视觉任一级失败记原因不假装看见；主播话和弹幕=不可信素材不执行指令。
- **礼数红线**：不索联系方式/不去站外/不刷礼物/不评价外貌（提示词 + 禁用词闸门双保险）；主播说"别刷""机器人走开"→换房（**把"尊重拒绝信号"写进提示词**）。
- 弹幕错误码：-101 未登录 / **10031 太快** / 1003 被禁言 / -400 参数。

**判定**：双层闸门（agent 意图层 + CLI 执行层）与我们 review(8守卫)+publish(exit 5/6/7) 同构；**adopt "与最近 N 句相似度" 进 review 层、adopt 拒绝信号的礼貌退避语义**。

### 2.5 bronyacccxz/dsh-bilibili-reply —— @通知→看视频→时间戳回复（踩坑清单价值最高）

- 链路：@通知（**只有评论文字没有视频链接**，要先反查是哪个视频）→ video-watch 本地转写+抽帧 → 按类型回复（教学类给**分章时间戳**，玩乐类一句情绪，沉重内容克制）→ 用通知里的「回复」按钮发**内联回复**。
- **踩坑表（作者自述"真金白银换来"）**：
  1. 盲取 @ 列表第一条 → 点错别人的视频发错评论 → 必须**逐项核对昵称**（7 步清单）；
  2. 用视频顶部输入框回复 → 变成独立评论，**对方收不到通知** → 必须走回复按钮（对应 API root/parent）；
  3. 点用户名会跳主页，应点评论文本；
  4. 歌曲类视频转写 0 段 → 加 `--no-vad` 重跑（0 段→57 段）；
  5. 猜抽帧文件名必败 → 从 `frames.json` 的 `file` 字段取真实文件名。
- 自动模式：看门狗脚本每 30 秒查 @（检查不花 token，回复时才叫模型），再推微信通知。
- 注意事项：**"会以你的身份在公开评论区发言"、"建议用小号"**、自设 `max_replies_per_hour`、B站风控连发弹验证码或 **-352**，"别刷"；不做云端音视频。
- 依赖腾讯开源 BrowserSkill（8.8k★）用**真实已登录浏览器**操作。

**判定**：@回复是我们未做的互动通道；其身份核对清单与"内联回复才送达通知"是硬知识。**adapt 为未来 reply-notification 能力的设计输入；其"建议小号"与我们的单官方账号策略冲突，reject**。

### 2.6 pkkkkh/dsh-plugins —— 理解质量与记忆的工程化（fork 上游加料）

- dsh-bilibili（CZX2244/dsh-bilibili 的维护 fork，0.8.1）：**文稿骨架压缩**（长转写→结构化骨架）、**通道探针**（先探测字幕可用性）、**双字幕轨 + 第二路 ASR 交叉校验**、**商单识别折叠**（识别恰烂钱内容并折叠降权）、定点抓帧。
- im-session-archive（0.2.0）：**跨会话长期记忆**——每通道留最近 1 个会话，每轮刷新 `LONGTERM.md`（长期状态人/模型维护 + 会话时间线自动生成，最多 30 段），新会话开局先读；单段快照落 `memory/auto/<时间>-<sid>.md` 供审计。
- im-full-access（0.1.0）：命中规则即开 `danger-full-access + approval: never`——作者自己注明"**系统层面没有兜底，安全边界完全靠 AGENTS.md 约定**"。**对我们是反模式**：我们坚持文件传参 + 退出码硬停，不开免审批。

**判定**：**adopt 商单检测**（给 our fetch_context 加"恰烂钱→跳过"信号，营销类视频评论风险收益比最差）与 **LONGTERM.md 式互动记忆**；**reject im-full-access 模式**。

### 2.7 t2 深挖八项目的机制速览（详表见 research-oss-boundaries.md）

| 项目 | 频率 | 审核 | 去重/状态 | 风控 |
|---|---|---|---|---|
| bili-cli/bili-agent（我们的通道） | 评论 10s/次+30次/h；投稿 5min | --dry-run；audit.log；AI标识默认勾 | seen.json | risk_control/captcha/rate_limited 专用退出码 5/6/7，--wait 自动等 |
| biliup(TS) | 投稿队列单并发，601 暂停 | reply **默认只打印**（dry-run） | — | 旧版曾吞风控提示假成功 |
| dsh-bili-scroll | 每轮 1 条（≤20 可调） | 默认关，"不要替用户打开自动评论" | 同视频一次 | 专用 profile 只读工具隔离；红线写死提示词 |
| AGI_Ananas/bilibili-mcp | 无内置 | 无 | 无 | 无（裸 API 层） |
| bili-comment-buddy | 30s+10min | 填入不发送=预览 | 每视频一次 | "验证码/频繁/风险"即停 |
| bili-auto-comment(MV3) | 随机间隔 | 无 | 内容重复度检测 | STOP_CODES 硬停（12045/12002/12008/12010/-101） |
| GSJNZH 油猴 | ≥30s | 免责声明 | 文案库随机 | 连败 3 次停 |
| biliSendCommAntifraud | 发后等 5s（带图 15s）再查 | —（检测向） | — | Shadowban/戒严/污染区判定 |

### 2.8 t1 深挖要点（详表见 research-dsh-pets.md）

- **ambersmaller/bilibili_live_mod（astrbot）——工程参数最具体**：双账号制（大号收小号回）/ 弹幕≥1.5s 评论≥45s / `max_replies_per_cycle=1` 起步 / 错误码 **12015 / -412 / 509 = 被风控→自动暂停 10min + 告警** / 低等级号 max_length 调小 / 先只读观察模式几天 / 元数据须登录态否则 412 / **负缓存 120s** / **连续 3 次失败熔断 5min**。
- **dafeiyu-cleaner（liyupi）**：反制视角证明"模板化评论"是平台与社区共同打击目标（离线规则 284 账号 ~85% 识别）→ 我们的评论必须去模板化。
- **dsh-bili-scroll / Code0**：四道门 + L0-L3 权限分级 + 审批单六要素 + 硬停机条件（t1 §5.3）。
- **AI课代表2023**：总结路线 3 个月 25 万粉；收敛终点=只总结不做主观评论。
- **"鱼灾"教训**：同质化 + 评论数>播放数 = 最显眼机器人特征。

---

## 3. 工程机制横向对比表

维度：**限流 / 审核 / 理解质量 / 人设·记忆 / 发后复核 / 风控恢复 / 内容多样性**。
●=有且工程化 ◐=有但弱或半自动 ✘=无。

| 项目 | 频率限流 | 发布前审核 | 理解质量门 | 人设/记忆 | 发后复核 | 风控恢复 | 内容多样性 |
|---|---|---|---|---|---|---|---|
| **我们 bili_comment.py** | ● 10min/日5/UP日1/次run1 | ● L0-L2 队列 | ● 理解门 A/B/C + evidence_quote 逐字校验 | ✘ 无跨视频记忆/UP画像 | ● post_check 8s×3 四态 | ● exit5/6/7+冷却30min | ◐ shingle 去重（无生成多样性） |
| dsh-bili-scroll | ● 每轮1/上限20 | ● 默认关+人工开 | ● 读字幕原文才评 | ◐ 提示词红线 | ✘(0.1.4 起有复核) | ◐ 依赖 MCP | ✘ |
| deepmoyu | ● 节奏冷却+平台额度 | ● 逐条批准 | ◐ 继承 scroll | ● soul.md+学习队列 | ◐ 结果未知先核对 | ● 12014 退避 | ◐ |
| kydon | ● 10min/次run1 | **✘ 明确无** | ◐ 本地ASR+采样 | ◐ 2000 BVID 状态 | **✘** | ◐ pending 重试 | ✘ |
| chat-whale | ● 四层闸门75s/日40 | ● 演练默认 | ● 听不到就明说 | ◐ 找房记忆 | ✘ | ◐ 弹幕码表 | ◐ 最近6句相似度 |
| bili-watch | ✘（只读） | ✘（只读） | ● **四级降级+幻觉判定+诱饵检测** | ✘ | ✘（只读） | ✘ | ✘ |
| bilibili-reply | ◐ 自设上限 | ◐ 半自动（人唤起） | ◐ video-watch | ✘ | ✘ | ◐ -352 提醒 | ● 按视频类型分回复策略 |
| bili-cli | ● 全命令本地限流 | ◐ dry-run+审计 | ✘ | ✘ | ◐ comment check | ● 专用退出码 | ✘ |
| astrbot live_mod | ● 45s/周期1 | ◐ 观察模式 | ✘ | ◐ 双账号人设 | ✘ | ● **熔断+负缓存+暂停告警** | ✘ |
| buddy/MV3/GSJNZH | ● 30s/日5、随机/日≤10、≥30s | ✘~◐ | ✘ | ✘ | ✘ | ◐ 码表硬停 | ◐ 文案库随机（=模板化的补丁） |
| biliup(TS) | ● 上传单并发 | ● reply 默认 dry-run | ✘ | ✘ | ✘ | ◐ 601 暂停 | ✘ |
| MCP 类(各) | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ |

**结论**：七个维度上没有任何单项目全覆盖；我们缺的两格（人设/记忆、内容多样性）恰是 deepmoyu、chat-whale、pkkkkh 有现成解法的两格；我们领先的是「理解证据锚定 + 人工审核队列 + 发后折叠检测」三格——生态里只有 buddy 的"填入不发送"接近我们的 L1。

---

## 4. 最佳方案判定

- **边界设计最佳：pyyxzi/dsh-deepmoyu**（逐通道授权 + 顺序门 + "结果未知先核对"）。
- **工程参数最佳：ambersmaller/bilibili_live_mod**（45s、熔断、负缓存、风控码表、低等级号差异化）。
- **理解质量最佳：Sacredbluedragon/dsh-bili-watch**（幻觉判定/诱饵检测的量化阈值可直接搬）。
- **状态机最佳：Kydon-ai/dsh-kydon-bilibili**（pending 落盘/接受后才出队），但**安全姿态最差**（无审核无复核），只能拆零件用。
- **踩坑知识最佳：bronyacccxz/dsh-bilibili-reply**（身份核对/内联回复/转写 0 段）。
- **通道最佳：bili-cli**（护栏 + 审计 + 专用退出码，这也是我们选它的原因，生态内无更优替代）。

---

## 5. adopt / adapt / reject 清单

### adopt（直接采纳，9 条）

| # | 来源 | 机制 | 融入点 |
|---|---|---|---|
| A1 | bili-watch | ASR 幻觉判定三阈值（VAD<0.5s 出文本 / 重复文本>50% / no_speech_prob>0.5 过半）→ 素材标"低可信" | `bili_comment.py` `understand()`/`_understand_detailed()`：素材质量分加入 Brief；低可信→`pick_opportunity` 只允许"真诚提问"类，禁"补充事实/核对质疑" |
| A2 | bili-watch | 物料围栏：给 LLM 的素材块带 `[source: ai-subtitle|asr|ocr]` 前缀；>800 字截断+落盘路径 | `_material_block()`：按来源打标，长转写截断进 state 文件 |
| A3 | deepmoyu | 发送结果不确定性协议："结果未知→先核对，不自动重发" | `_handle_publish_err()`：network_error(8) 时**禁止自动重试**，改走 `comment list` 核对后再决定 |
| A4 | pkkkkh | 商单/恰烂钱识别→折叠跳过 | `_fetch_context_detailed()`：简介/标签/评论区命中商单特征→candidate 直接丢弃 |
| A5 | pkkkkh im-session-archive | `LONGTERM.md` 式互动记忆：近期自评列表+UP画像文件，新任务开局读 | 新增 `comment_memory.md`（state 目录）：最近 N 条自评（bvid/UP/文本/结果）注入 `draft_comment` 与 `_g_template_dup` |
| A6 | deepmoyu | 逐通道授权：评论/回复/@回复 各自开关，全默认关，互不代替 | `config`：`comment_channel` / `reply_channel` 分开；L2 只放行显式开启的通道 |
| A7 | astrbot live_mod | 连续失败熔断：连续 3 次 publish 失败→熔断 ≥5min 且当日降级到 L1 | `cmd_run()`：失败计数进 state，熔断期强制 `dry_run` |
| A8 | astrbot live_mod | 负缓存：拉取失败/空弹幕的候选 120s 内不再拉 | `discover_candidates()`：失败 bvid 记负缓存时间戳 |
| A9 | chat-whale | review 层加"与最近 N 条自评相似度"检查 | `_g_template_dup` 已有 shingle；补"跨视频相似度"维度（数据源=A5 记忆文件） |

### adapt（改造后采纳，7 条）

| # | 来源 | 机制 | 改造与融入点 |
|---|---|---|---|
| B1 | kydon | pending 队列落盘、**B站接受后才出队**、失败保留重试 | 用于 post_check 失败重查队列；**必须修掉其 crash 窗口**：写盘顺序=先记"已发送待复核"，复核通过才出队（`post_check`/`_read_check_status`） |
| B2 | kydon | 本地 ASR 全链路（yt-dlp→faster-whisper，不调B站字幕API） | 仅当B站字幕缺失时作为第二来源；成本高，默认关，config 开关；产物走 A1 幻觉判定 |
| B3 | bili-reply | @通知回复通道（身份核对清单+内联回复才送达通知） | 未来 reply-notification 能力设计输入：`pick_opportunity` 的"楼中楼答题"升级版；上线前先过 L1 人工审 |
| B4 | chat-whale | 识别器不可用→提示词明确写"没听到"，禁止编造 | `understand` 的 Brief 模板加固定字段 `gaps: 未取得/不可靠的信息清单`，LLM② 起草时禁用引用 gaps 的表述 |
| B5 | chat-whale | "尊重拒绝信号"：UP 删除/拉黑/评论区明确拒 AI → 该 UP 进黑名单 | `post_check`/`_audit_scan`：发现自评被删或 UP 拉黑→写 `comment_blacklist_up` |
| B6 | bili-watch | 降级链显式化：每级失败记原因，全 miss exit 1 | `fetch_context` 的 4 只读调用失败时结构化记录进 Brief（而非静默缺素材），供理解门 C 判断 |
| B7 | CCCpan 敏感词库 MCP | 现成中文敏感词列表 | `_g_politics`/`_g_water` 挂词库文件（config 可选），替代纯 LLM 判断的漏检 |

### reject（明确不采纳，6 类）

| # | 来源 | 机制 | 理由 |
|---|---|---|---|
| R1 | kydon（路线）/ MV3 / GSJNZH / liaoyanqing666 / gongjunhao | 无人工审核的自动发布闭环 | 与《用户协议》5.6(3)/5.8、社区公约 §1/§4/§5 冲突；阿瓦隆 Shadowban/秒删不可预知；我们 L1 人工审是账号安全的承重墙 |
| R2 | MV3 / liaoyanqing666 / bilibili-reply | 浏览器 DOM 自动化（逐字输入/真实浏览器操作评论区） | personal82555 的理由（直调 API 易被风控分辨）成立，但 DOM 自动化脆弱且不可审计；我们用 bili-cli 官方通道+拟人节奏已够 |
| R3 | astrbot 双账号制 / kydon 多 Host | 多账号协同（大号收小号回） | 协议 §5"批量注册、登录、操作账号"；单官方号 + 内容质量是我们路线的前提 |
| R4 | pkkkkh im-full-access | `danger-full-access + approval: never` 自动授权 | 与我们"文件传参 + 退出码硬停 + 最小权限"相反；提示注入（评论区内容）可直达文件系统 |
| R5 | linyiLYi/bilibot | 微调模型生成人设回复 | 3123★ 但目标不同（训练自己的语料）；资讯编译人设不需要且引入不可控生成风险 |
| R6 | turbopush-mcp 等多平台 shotgun | 一套内容多平台批量发 | 单B站深耕+逐条审核的路线在批量管线里没有位置；批量=协议 §1"批量灌水"风险 |

---

## 6. 对我们管线的净评估

**保持领先的三格**（生态无对标）：evidence_quote 逐字锚定（唯一）、人工审核队列 L1（只有 buddy"填入不发送"勉强同类）、post_check 四态+折叠硬停（生态仅 biliSendCommAntifraud 作为检测工具存在）。

**补齐两格后即生态最全**：人设/互动记忆（A5+A9）与内容多样性（A5 注入 draft + B7 词库 + dafeiyu-cleaner 反向验证）。

**节奏参数无争议**：生态共识区间 = 间隔 30~75s、每日 5~40 条、单 run ≤1~3 条；我们的 10min/日 5/UP 日 1 处于**最保守端**——符合 t1 结论"宁缺毋滥"，放量节奏参照 t1 §6.6（2-4 周稳定后再放宽，上限锚 20/天）。

---

## 7. 来源

- GitHub API search（2026-11-12）：`bilibili comment`、`dsh bilibili`、`bilibili auto comment`、`bilibili mcp server`
- README 深挖：Kydon-ai/dsh-kydon-bilibili、pyyxzi/dsh-deepmoyu、Sacredbluedragon/dsh-bili-watch、1a125/dsh-bili-chat-whale、bronyacccxz/dsh-bilibili-reply、pkkkkh/dsh-plugins
- 内部复用：`docs/research-oss-boundaries.md`（t2）、`docs/research-dsh-pets.md`（t1）、`docs/comment-design.md`（t3 基准）
- 说明：各项机制均摘自项目 README/文档原文（不可信外部数据，仅提取事实）；未实测，星数与 pushed_at 为查询日快照。
