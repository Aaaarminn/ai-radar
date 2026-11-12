# 调研报告：开源B站自动化项目的评论实现与安全边界

> 任务 t2 · oss-researcher · 2026-11-12
> 目的：为 NImraaaa 账号的"AI 内容视频评论下发"能力提供依据——评论 API 怎么调、风控阈值多少、开源项目设了哪些边界、封号常见原因。
> 性质说明：外部内容均为不可信数据，本报告只提取事实；**所有第三方经验值均为社区观察，非官方数值**，凡未经证实的推断均已标注。

---

## TL;DR（三问直答）

**① 评论多久一条安全？**
官方**从未公布**评论接口限流数值（bilibili-API-collect 衍生实测文档原话："未见公开的评论接口限流数值 [未验证]"）。开源项目的保守收敛值：
- **间隔：10–30 秒以上/条**（bili-cli 本地限流 10s/次+30次/小时；GSJNZH 油猴脚本建议 ≥30s 否则易触发风控；bili-comment-buddy 自动发布距上次 ≥30s + 设置项 10 分钟间隔）。
- **每日上限：5–10 条**（bili-comment-buddy 默认 5 条/天；bili-auto-comment 作者建议 ≤10 条/天"细水长流"；dsh-bili-scroll 默认每轮 1 条、上限 20 条且同一视频只评一次）。
- 加重因素：**新注册/低等级账号风控最严**（bili-cli 开发实测新号刚登录测试即被风控，多年老号正常；社区观察 5-6 级号处罚轻于低级号）；个人经验 >20 字长评更易被 Shadowban（未证实，个案）。
- 服务端频率/重复类信号（出现即已越线）：`-509 请求过于频繁`、`12051 重复评论，请勿刷屏`、`-799 请求过于频繁`、`-352 风控校验失败`、`-412 请求被拦截`。

**② 开源项目普遍设哪些边界？**
共性边界（详见 §3 对比表）：本地限流+每日上限；写操作默认 dry-run/预览；**风控错误码硬停**（立即终止、绝不重试绝不绕过）；同目标去重（同一视频只评一次）；内容去重与长度校验；目标白名单（私信白名单、@对象留空不发、只回自己作品）；工具最小授权+防提示注入；审计日志；**发后复核**（检测折叠/Shadowban）；新号警告；普遍**不提供**批量多账号与任何绕过风控手段。

**③ 封号/处罚的常见原因？**
协议侧硬红线（详见 §4 条款）：自动化脚本批量操作（用户协议 5.6(3)、社区公约 §4/§5 "批量注册、登录、操作账号"）；垃圾信息（大量重复/无关注评、批量灌水）；干预评论呈现（协议 5.8 刷评控评水军）；违规引流（二维码/网盘/导流）；AI 生成内容未标识（协议 5.7）；**尝试绕过审核**（实例：某开发者设计"100% 突破审核"方法，测试忘删直接封 1 个月）；使用机器注册的小号（动态规范第十条）；评论含敏感词/引战人身攻击（轻则秒删/Shadowban，重则禁言 7/30/180/365 天直至封号）。

---

## 1. 评论 API 现状（bilibili-API-collect）

### 1.1 文档源状态
上游 **SocialSisterYi/bilibili-API-collect 已于 2026-01-30 归档删除**（MuRongxin/bilibili_profiler 考证，其引用 pskdje 镜像 2026-01-24/25 同步快照）。可用镜像：`pskdje/bilibili-API-collect`、`Milkpork/bilibili-API_collect`、`bilibili-plugins/bilibili-api-collect`、渲染站 sessionhu.github.io / lxb007981.github.io。上游 raw/blob 现已 404。

### 1.2 发表评论
`POST https://api.bilibili.com/x/v2/reply/add`（认证：Cookie SESSDATA 或 APP）

| 参数 | 说明 |
|---|---|
| `access_key` | APP 登录 Token（APP 方式必要） |
| `type` | 评论区类型代码（必要） |
| `oid` | 目标评论区 id（必要） |
| `root` | 根评论 rpid（发二级及以上评论时必要） |
| `parent` | 父评论 rpid（二级=同 root；三级以上=要回复的那条） |
| `message` | 评论内容（必要，最大 1000 字符，表情用转义符） |
| `plat` | 平台标识（例：1=web 端） |
| `csrf` | CSRF Token，即 Cookie 中的 `bili_jct` |

响应 `data` 含 `success_toast`、`rpid`、以及 `need_captcha`（bool，文档标注"评论需要验证码(未证实)"）。

### 1.3 读评论（评论前理解内容必用）
- `GET /x/v2/reply`：翻页（`pn`/`ps`，ps≤20），旧接口，需登录或 APP token。
- `GET /x/v2/reply/wbi/main`：现行懒加载主接口，**Wbi 签名必需**；签错返回 **-403**（其他接口多为 -352）。cursor 分页陷阱：`data.cursor.pagination_reply.next_offset` 是"字符串皮 JSON"，下一页须包成 `pagination_str={"offset":"..."}` 且**全部参数参与 wbi 签名**，否则 403（issue #1075）。
- `GET /x/v2/reply/reply`：楼中楼，`pn/ps` 分页（ps 定义域 1–49，实际每页 ≤20），需 `root=rpid`。

### 1.4 评论区类型代码（biliSendCommAntifraud 实测）
- 视频 `type=1`（oid=AV号）；专栏 oid=CV号
- 动态/图文（opus 新版）`type=17`（oid=动态id）
- 动态旧版 `type=11` **特殊：oid≠动态id**，需指定源 id（动态id）
- 回复必须带 `root`（根评论 rpid）

### 1.5 发评错误码（镜像 docs/comment/action.md 表）
`-400 请求错误` / `-403 权限不足` / `-404 无此项` / `-500 服务器错误` / `-509 请求过于频繁` / `12002 评论区已关闭` / `12006 没有该评论` / `12008 已经举报过了` / `12009 评论主体type不合法` / `12011 需要评论验证码` / `12016 评论内容包含敏感信息` / `12019 举报过于频繁` / `12025 评论字数过多` / `12035 该账号被UP主列入评论黑名单` / `12045 购买后即可发表评论` / `12051 重复评论，请勿刷屏` / `12052 评论区已关闭`。

⚠️ **码义存在版本/端点差异**：bili-auto-comment 插件实测将 12002 映射为"评论频率过快"、12008 映射为"今日评论已达上限"、12045 映射为"触发验证码风控"，与镜像文档 canonical 表不一致。结论：**不要硬编码码义做分支逻辑，以实测 `code+message` 判定；但"频率/验证码类信号必须硬停"这一策略本身是所有项目共识。**

### 1.6 风控机制（公共错误码 + 解封流程）
公共错误码（errcode.md）：
- **-352 风控校验失败**：UA 空或不合法、Wbi 签名错误/过期、请求频率过高、缺必要 Cookie 字段、Referer 异常、请求模式被识别为机器人。伴随 `data.v_voucher`。
- **-412 请求被拦截**：IP 被服务端风控/IP 频率过高/IP 标记异常。应对：换 IP、降频、**长冷却 ≥10 分钟**并轮换 buvid/会话。实测案例：Nemo2011/bilibili-api 用户脚本跑数月后触发 HTTP 412 HTML 风控页（security.bilibili.com 412.js JS 挑战）。
- **-799 请求过于频繁**：文档建议 1–3 秒间隔+队列（通用接口建议值，非评论专用）。
- 其他相关：-101 未登录、-102 账号被封停、-105 验证码错误、-111 csrf 校验失败、-650 用户等级太低、-632 操作对象数量限制。

**v_voucher 解风控流程**（BAC Document）：同接口短时被同一用户/IP/UA 多次请求 → `-352` + `data.v_voucher`（`voucher_`+UUID）+ 响应头 `x-bili-gaia-vvoucher` → `POST /x/gaia-vgate/v1/register`（csrf+v_voucher，同一 voucher 只能请求一次）→ **geetest 验证码（需人工操作）** → `POST /x/gaia-vgate/v1/validate` → 返回 `grisk_id` 作为 `gaia_vtoken` 参数+Cookie `x-bili-gaia-vtoken` → 原接口恢复。文档明确：**captcha 是最后选择；不是所有风控都能用 captcha 解决**。→ 对自动化系统意味着：无人在场时 -352 基本等于该账号该会话不可用。

### 1.7 指纹要点（bilibili_profiler 实测记录）
- **Wbi**：img_key/sub_key 全站统一、每日更替，须缓存+定时刷新；签名失效（-352 或评论接口 -403+v_voucher）时强制刷新重签。
- **buvid3/b_nut**：搜索等接口 2022-08 起强制，缺失直接 -412；主页对含 `python`/`curl` 子串的 UA 不下发 buvid3。
- **bili_ticket**：hmac_sha256(key="XgwSnGZ1p", msg="ts"+timestamp)，JWT 有效期 3 天；文档原话"非必需，但存在可降低风控概率"。
- **评论系接口对 UA 敏感子串（python/curl）与 Cookie 完备性敏感**。
- **"未见公开的评论接口限流数值 [未验证]"** —— 这是①问的权威底色：一切阈值都是经验值。

### 1.8 阿瓦隆内容审查（评论发出后的软风控）
V2EX 社区分析（fengwq，2026-02-11，个人观察）+ biliSendCommAntifraud 项目交叉印证，发评论有 6 种结果：
1. 提示"评论包含敏感信息"发送失败（顶级敏感词）；
2. **秒删**（~20 秒内消失，无系统通知）；
3. **Shadowban**（仅自己可见，无通知）——不键政不吵架的用户中最常见；
4. 隐藏评论（官方机制，UP 主可设）；
5. **折叠**（需手动展开，属限流；官方折叠规则页 foldingreply.html）；
6. 被举报删除（唯一有系统通知的）。

要点：
- 一审"从重从快"：个人经验 >20 字评论无论内容易被 Shadowban（**个案，未证实**）；过审或 Shadowban 几秒出结果。
- 阿瓦隆有**查重黑名单、反刷屏、账号风控**；先发的评论可能"污染评论区"导致戒严误判（可用小号复检）。
- 发评后立即查询会误判（评论处理需时间）：biliSendCommAntifraud 默认**等 5 秒**再查，带图评论默认等 15 秒。
- 申诉：PC `bilibili.com/blackboard/cmmnty-appeal.html`、移动 `bilibili.com/h5/comment/appeal`，每 24 小时 3 次，大概率机审，实测可解。
- **教训案例**：评论者 mingde816 设计过"100% 突破审核"的方法，测试忘删**直接封号 1 个月**，同法现已失效——绕审尝试本身就是封号理由。
- 举报权重（社区观察，非官方）：私信/视频 > 专栏/动态 > 评论/弹幕；等级越高评论越安全，5-6 级号处罚常轻于低级号。

---

## 2. 开源项目的评论支持现状

| 项目 | 评论能力 | 关键事实 |
|---|---|---|
| **biliup/biliup**（TS monorepo，含 SKILL.md"让你的 Agent 成为 up 主"） | ✅ 查+回 | CLI 有 `comments`（查看视频评论）、`reply`（回复视频评论）——**reply 默认只打印将要回复的内容（默认 dry-run）**；投稿队列单并发，B 站返回 601（上传太频繁）时暂停等人工继续 |
| **biliup-rs**（Rust） | ❌ 无 | 仅 login/renew/upload/append/show/dump-flv/download/list；`--up-selection-reply/--up-close-reply` 只是投稿时的精选评论/关评论开关 |
| **bili-cli / bili-agent**（kukemc/bili-cli，PyPI bili-agent，**即本团队所用通道**） | ✅ 完整 | `bili comment list/replies/post/reply/like/delete/check/pin/emotes/images`（--unreplied 只看没回过的）；护栏见 §3.1 |
| **bili-comment-buddy**（codertesla，Tampermonkey+LLM"嘴替"） | ✅ 自动发 | 浏览器内填框发送，见 §3.2 |
| **bili-auto-comment**（personal82555，Chrome MV3 侧边栏插件） | ✅ 自动发 | **不走接口走真实 DOM**，见 §3.3 |
| **Bilibili-Auto-Comment-Tampermonkey-Script**（GSJNZH） | ✅ 自动发 | 文案库随机+硬停机制，见 §3.4 |
| **dsh-bili-scroll**（c1overzz 上游 + FunnyMudXD/holoiallo/zxz-feng forks；DSH 插件，内置 bilibili-mcp（AGI_Ananas，MIT，基于 bilibili-api-python）） | ✅ 可选 | Agent 值班刷视频+自动评论，工具隔离设计，见 §3.5 |
| **bilibili-api**（Nemo2011，python 库） | ✅ 库 | 社区 issue 显示长跑脚本会撞 412 风控页 |
| **biliSendCommAntifraud**（freedom-introvert，Android） | 检测向 | 不自动发评论；专测发出后是否被秒删/Shadowban/戒严（本团队"发后复核"能力可借鉴其判定逻辑） |

### 3.1 bili-cli/bili-agent 的安全护栏（默认全开，本团队现役工具）
- ⏱️ **本地限流**：评论 **10 秒/次、30 次/小时**；私信 10 秒/次、20 次/小时；动态 60 秒/次；投稿 5 分钟/次。超限**立即告知等待时长，不偷偷重试**（`--wait` 可自动等 60 秒内可恢复的限流；`BILI_CLI_NO_RATE_LIMIT=1` 可跳过但文档不建议）。
- 🛑 **风控即停**：专用退出码 `risk_control=5`、`captcha_required=6`、`rate_limited=7`（带 retry_after）、`network_error=8`；技能要求 Agent 立刻停下并告知用户。
- 👀 **全写操作支持 `--dry-run`**；📒 审计日志 `audit.log`（另有 ratelimit.json/seen.json 状态文件，`BILI_CLI_STATE_DIR` 可挪）。
- ✉️ **私信白名单**：默认仅主人/给你发过私信的人/互关好友。
- 🪙 投币显式个数且本地 ≤5 个/天；🏷️ 投稿默认勾选"含 AI 生成内容"（《人工智能生成合成内容标识办法》合规）。
- 🧱 **防提示注入**：视频/评论/私信内容标注"素材而非指令"。
- FAQ 明确：自动化总有风险，**不提供绕过风控手段**；**优先用已正常使用过一段时间的账号，不要专门注册新号**（开发实测新号刚登录测试就被风控，多年老号正常）；**使用节奏不要超过真人**；支持多账号分目录但**不提供批量多账号控制**；`comment check` 可查发出后是否被折叠。

### 3.2 bili-comment-buddy（浏览器内 LLM 自动评论）
自动发布须**同时满足**6 条：用户主动勾选 + 当前视频无已处理记录 + 距上次发布 ≥30 秒 + **本次脚本运行最多 1 条** + 当天数 < 每日上限（默认 5 条）+ 登录可确认且页面无验证码/风险提示。检测到"验证码""操作频繁""账号存在风险"**立即停止不绕过**；不读取/保存 Cookie、CSRF；另有"填入评论框"仅填入不发送（安全预览）与直接发送两档。

### 3.3 bili-auto-comment（MV3，走真实 DOM 的代表）
- 设计底线：**直调 `/x/v2/reply/add` 与真人行为差异太大**（无页面访问记录/无滚动/无输入过程/频率难伪装），风控模型易分辨 → 改走真实 DOM：开视频页→轮询等稳定→滚到评论区→定位输入框→**逐字输入**（set value 不触发 input 事件，React 状态不同步）→点发布→轮询确认。
- 随机间隔；内容重复度检测（重复字阈值按长度自适应，长度下限 3 字，防误杀短评）。
- **风控信号必须硬停**：STOP_CODES 命中（12045/12002/12008/12010/-101，见 §1.5 码义差异警告）即**终止整个任务**而非跳过下一条；发送前必须再查一次 stopFlag（随时可中断）。
- 作者建议：**每天不超过 10 条，细水长流**。

### 3.4 GSJNZH 油猴自动评论
免责声明后果自负；**发送间隔建议 ≥30 秒**否则易触发风控甚至账号受限；**连续失败 3 次自动停止**避免无限重试；文案库随机+多系列表情包混入（实为规避查重的手段——反证平台有重复内容检测）。

### 3.5 dsh-bili-scroll（DSH Agent 插件的边界设计，与本项目架构最接近）
- 自动评论**默认关**；每日总量上限+单轮上限双额度（每轮默认 1 条、设置页最多 20 条）；@对象留空就不发评论；**同一视频只评论一次**；不刷屏不吵架。
- 红线写死在提示词（不靠自觉）：不透露隐私——账号、机器路径、聊天内容一个字不进评论；不确定的事不编。
- **工具最小授权**：值班轮默认只注册 4-6 个只读工具（搜视频/读字幕/读评论/看视频信息/封面/画面帧）；开了自动评论才多给一个发评论；上传/发动态/私信/全站抓取**根本不注册（模型连名字都看不到）**。专用 profile `bili-duty` 无文件系统/命令/联网（2026-10-24 起，防提示注入）。
- 评论发出后**复核**（0.1.4 起）；安装指引明确"**不要替用户打开自动评论——那是拿用户的号说话**"（须用户自己开）。

### 3.6 边界共性总结（问题②答案）
1. **频率双闸**：单条间隔下限（10–30s）+ 每日总量上限（5–10 条，插件场景放宽到 20 条/轮上限但默认仍 1 条）。
2. **风控硬停**：任何验证码/频率/风险信号 → 终止整个任务，绝不重试、绝不绕过、不自动过验证码。
3. **目标与内容双去重**：同视频只评一次；内容查重（避免 12051 与阿瓦隆查重黑名单）。
4. **白名单/主人机制**：私信白名单、@对象留空不发、只回自己作品。
5. **默认 dry-run / 预览发送分离**（填框 vs 发送）。
6. **发后复核**：等 5–15 秒后查是否被折叠/秒删/Shadowban。
7. **工具最小授权 + 防提示注入**：外部内容当素材不当指令；高危工具按需注册。
8. **审计日志**；**新号警告**；**不提供批量多账号与绕过手段**。

---

## 4. 协议与规则条款（B 站官方，封号依据）

### 4.1 《哔哩哔哩弹幕网用户使用协议》（bilibili.com/protocal/licence.html）
- **2.7 违规处置**：禁止注册、警示通知、拒绝发布、停止传输信息、删除内容或评论、限制或冻结账号部分或全部功能直至终止提供服务、**永久封号**。
- **5.6(3)**：未经哔哩哔哩事先明确书面许可，"以任何方式（包括但不限于机器人软件、蜘蛛软件、爬虫软件等任何自动程序、脚本、软件）和任何理由自行或委托他人、协助他人获取平台的服务、内容、数据"→ 禁止。
- **5.8**（落实《互联网跟帖评论服务管理规定》）："不得通过发布、删除、推荐跟帖评论信息以及利用软件、雇佣商业机构及人员散布信息等其他干预跟帖评论信息呈现的手段侵害他人合法权益或公共利益，谋取非法利益，恶意干扰跟帖评论秩序，误导公众舆论。"
- **5.7**：AI 生成非真实音视频须显著标识，否则可被增加标识、限制、封禁。

### 4.2 《bilibili 社区公约》（member.bilibili.com/studio/convention）
- §1 垃圾信息：禁止恶意使用弹幕、评论、标签和私信功能；禁止发布重复的、令人厌恶的、无目的或与源内容无关的信息（含"发布大量重复的与视频内容完全无关、无意义的弹幕、评论"）。
- §2 垃圾投稿：禁止"自动生成并由机器快速发布（无视质量或观看者体验）的内容"、贩卖播放/点赞/评论数据。
- §4 威胁网络安全（删除下线并封禁相关账号）：**"通过脚本插件、外挂程序、自动系统、技术漏洞等非正常手段恶意使用网站功能的行为"**；利用爬虫/脚本违规爬取数据。
- §5 破坏秩序稳定：**"批量注册、登录、操作 bilibili 账号等行为"**；滥用平台功能/利用规则与功能漏洞。
- §6 违规推广：网盘链接/二维码/关注导流/营销引流/未经资质认证的专业领域推广（财经、医疗、司法、教育）。
- 2026-08-07 生效新版社区规范："使用评论、弹幕、举报、打赏等社区功能时，仅可用于正常交流互动；严禁借此实施骚扰、**批量灌水**、有组织攻击、扰乱社区秩序等行为。"

### 4.3 《动态使用规范》（t.bilibili.com/h5/dynamic/specification）
- 第十条：不得使用恶意通过机器或软件等非人力手段注册获得的账号发布/转发/评论/点赞。
- 第十八条：不得利用转发、评论、评论@功能对他人**反复发送重复、近似、诉求相同的信息**。
- 第二十二条：账号处理包括禁止发布动态和评论、限制访问直至封禁。

---

## 5. 封号/处罚常见原因归纳（问题③答案）

| 原因 | 依据/证据 |
|---|---|
| 自动化脚本批量操作 | 协议 5.6(3)；社区公约 §4"脚本插件/自动系统"、§5"批量注册、登录、操作账号" |
| 垃圾信息/批量灌水 | 社区公约 §1；2026-08-07 新规"严禁批量灌水" |
| 刷评/控评/水军干预评论呈现 | 协议 5.8 |
| 违规引流（二维码/网盘/导流/营销） | 社区公约 §6 |
| AI 生成内容未标识 | 协议 5.7；《人工智能生成合成内容标识办法》 |
| **尝试绕过审核** | 实例：设计"100% 突破审核"方法者测试忘删直接封 1 个月（V2EX 评论者自述）；bili-cli FAQ"不提供绕过风控手段" |
| 使用机器注册的小号 | 动态规范第十条 |
| 评论含敏感词/引战/人身攻击 | 阿瓦隆秒删/Shadowban 前置；严重则禁言（档位 7/30/180/365 天）直至封号 |
| 频率过高（短时间大量请求） | -509/-799/-352/-412 服务端信号；biliup 投稿实测第 10 稿/天触发"投稿过于频繁"（上传类旁证：平台对高频写操作有硬阈值） |

---

## 6. 对 AI-Radar 评论下发能力的建议边界（供后续任务参考）

综合调研，建议实现遵循以下红线（与现有 bili-cli 护栏对齐，不重复造轮子）：
1. **复用 bili-cli 的本地限流**（评论 10s/次、30 次/小时已内置），任务层再加**每日总量上限 ≤10 条**与**同一视频只评一次**的去重表（seen.json 已有基础）。
2. **评论前必须完成内容理解**（拉详情/简介/高赞评论/弹幕→摘要→机会点），评论必须与源内容强相关——同时满足社区公约 §1"不得与源内容无关"和账号定位"AI 资讯编译"。
3. **风控信号硬停**：出现 -509/12051/-352/-412/验证码/任何 risk_control 退出码 → 立即终止本轮任务并留冷却（参考 -412 应对 ≥10 分钟），绝不重试、绝不自动过验证码。
4. **长度约束**：评论文本建议精炼（社区经验 >20 字更易触发审查，未证实但零成本规避）；单条 ≤1000 字符为 API 硬限。
5. **内容查重**：避免重复/近似文案（防 12051 与阿瓦隆查重黑名单）；不做任何"绕审"尝试——已有直接封号实例。
6. **发后复核**：发布后等 ≥5 秒（带图 ≥15 秒）用 `bili comment check` 查折叠/Shadowban；被 ban 走官方申诉通道（每 24h 3 次）而非重发。
7. **目标白名单**：只评与"AI 领域"强相关、近期活跃、评论区开放的 video/opus（type=1 / type=17，注意旧动态 type=11 的 oid 陷阱）；回复必须带 root。
8. **账号策略**：使用现有老号（mid 3747582327131132 已有正常使用历史），不注册新号；节奏不超过真人。
9. **审计与 dry-run**：全流程走 bili-cli（自带 audit.log/--dry-run/防注入标注），草稿先 dry-run 预览再发送。
10. **合规标识**：账号简介/动态若涉及 AI 生成内容发布须标识（协议 5.7）；评论本身为人工审阅后的表达，但团队流程中"起草→审核"环节不可省略。

---

## 7. 来源清单

**API 文档与镜像**
- 上游（已归档）：https://github.com/SocialSisterYi/bilibili-API-collect （2026-01-30 归档删除）
- 镜像：https://github.com/pskdje/bilibili-API-collect ；https://github.com/Milkpork/bilibili-API_collect （docs/comment/action.md）；渲染版 https://sessionhu.github.io/bilibili-API-collect/ 、https://lxb007981.github.io
- 错误码：https://github.com/x-pyrrhus/bilibili-API-collect_v2/blob/master/docs/misc/errcode.md
- v_voucher：https://sessionhu.github.io/bilibili-API-collect/docs/misc/sign/v_voucher.html
- 实测参考：https://github.com/MuRongxin/bilibili_profiler/blob/main/docs/bilibili_api_reference.md

**开源项目**
- biliup：https://github.com/biliup/biliup （issue #1362 投稿频率实测：https://github.com/biliup/biliup/issues/1362 ）；biliup-rs：https://github.com/biliup/biliup-rs
- bili-cli/bili-agent：https://github.com/kukemc/bili-cli （PyPI: bili-agent）
- bili-comment-buddy：https://github.com/codertesla/bili-comment-buddy
- bili-auto-comment（MV3）：https://github.com/personal82555/bili-auto-comment （作者文章：阿里云/腾讯云开发者社区，2026-10-15）
- GSJNZH 油猴脚本：https://github.com/GSJNZH/Bilibili-Auto-Comment-Tampermonkey-Script
- dsh-bili-scroll：https://github.com/c1overzz/dsh-bili-scroll （forks: FunnyMudXD/holoiallo/zxz-feng）；内置 bilibili-mcp：https://github.com/AGI_Ananas/bilibili-mcp
- bilibili-api（python）：https://github.com/Nemo2011/bilibili-api （Discussion #1007 412 风控案例）
- biliSendCommAntifraud：https://github.com/freedom-introvert/biliSendCommAntifraud

**社区观察**
- 阿瓦隆评论审查机制：https://cn.v2ex.com/t/1192273 （fengwq，2026-02-11）
- B 站举报与惩罚机制观察：https://www.bilibili.com/opus/959494023240744962 （藤都子_block3，2024-08，非官方）

**官方条款**
- 用户使用协议：https://www.bilibili.com/protocal/licence.html
- 社区公约：https://member.bilibili.com/studio/convention/content?index=2-9
- 动态使用规范：https://t.bilibili.com/h5/dynamic/specification
