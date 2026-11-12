# AI-Radar 全天运营节奏表(rhythm)

> t12 产出(2026-11-12)。目标:四类写动作(动态发布/评论/点赞/回复)在全天维度
> **像真人一样分时段分散**,避开 t1/t2 调研确认的风控特征:批量挂机、整点整分
> 机械节律、单时段动作扎堆。本文是节奏设计文档,§7 的定时任务调整命令**未注册**,
> 留待用户确认后执行。

## 1. 设计原则(源自 t1 §6.3 / t2 调研)

1. **频率是第一红线**:翻车案例全部源于批量挂机失控;活下来的账号都是
   「每天个位数评论 / 同视频只评一次」量级。起步期每日评论 1–3 条,
   稳定 2–4 周后再评估放宽(硬顶 5,代码闸 `DAILY_MAX`)。
2. **只在"人会在的时段"行动**:评论/点赞集中在午间与晚间两个自然浏览时段;
   深夜(22:00–08:00)不做评论/点赞,只保留低频回复守候。
3. **同类型动作不挤在同一分钟**:不同任务错开 ≥30 分钟;同一次运行内部靠
   代码内建间隔(点赞 6s / 回复 8s / 评论后 10s / 两条评论 ≥10min)。
4. **不同类型动作可以在同一时段交错**:真人晚间会"回弹幕→看视频→点赞→评论"
   混合操作,回复(20:17)→巡逻评论+点赞(20:47)→独立评论(21:33)的
   交错序列反而像真人;要避免的是**同分钟并发多路写动作**。
5. **预算留余量**:每日额度不满打满算,给人工( L1 send / 手动补评)留 1 条。
6. **硬停自愈**:任何风控硬停由 `comment_state.json` 的 `halted_until` 门拦住
   后续发布,节奏表无需人工改动;解除走 `bili_comment.py resume` 人工确认。

## 2. 现状盘点(2026-11-12 实测 schtasks + 代码常量)

| 任务 | 实测节奏 | 写动作 | 单次上限(代码) |
|---|---|---|---|
| AI-Radar-Eval | 每 2h :45(12 次/天) | 无(只读评估) | — |
| AI-Radar-Send | **每 4h 整点**(1/5/9/13/17/21) | 动态发布 | ≤1 条/天(`bili_max_per_day`)+影响力≥8 |
| AI-Radar-BiliInbox | 每 4h(0/4/8/12/16/20 点的 :17) | 回复评论/回复@ | ≤5 条/次,间隔 8s(`MAX_REPLIES_PER_RUN`) |
| AI-Radar-BiliPatrol-AM | 每日 11:17 | 评论+点赞 | 评论 ≤1(10min 间隔闸实际拦到 1)+点赞 ≤6(6s 间隔) |
| AI-Radar-BiliPatrol-PM | 每日 20:17 | 评论+点赞 | 同上 |
| AI-Radar-Comment | 每 2h :15(12 次/天,今日 20:15 起) | 独立发现→评论 | ≤1 条/次(`PER_RUN_POST_MAX`) |

实测发现的三个问题:

- **P1 同分钟碰撞**:BiliPatrol-PM 与 BiliInbox 都在 20:17 触发——评论+点赞与
  回复两路写动作每天在同一分钟并发,是最明显的机械特征。
- **P2 评论全天摊开**:AI-Radar-Comment 每 2h 一跑,与「评论集中在两个时段」
  的目标相反;且 t7 试运行证实发现层候选多为非 AI 域,绝大多数轮次空转,
  白烧 bili-cli 读调用与 LLM tokens(12 跑/天里约 10 跑无产出)。
- **P3 文档笔误**:docs/operations.md §2 写 Send「每 2h 整点」,实测为每 4h
  (13:00 起:1/5/9/13/17/21)。本文以实测为准,operations.md 待另行修正。

## 3. 目标全天节奏表

粗体行 = 本次调整项;其余保持现状。

| 时刻 | 任务 | 写动作 | 说明 |
|---|---|---|---|
| 00:17 | BiliInbox | 回复 | 夜间守候,实际多为空转(只读) |
| 00:45 | Eval | — | 只读 |
| 01:00 | Send | 动态(通常无) | 日额度一般已在白天用掉 |
| 02:45 | Eval | — | 只读 |
| 04:17 | BiliInbox | 回复 | 同 00:17 |
| 04:45 | Eval | — | |
| 05:00 | Send | 动态(通常无) | |
| 06:45 | Eval | — | |
| 08:17 | BiliInbox | 回复 | 早间回复窗口 |
| 08:45 | Eval | — | |
| 09:00 | Send | 动态(常见首发窗口) | 全天 6 窗口共享 ≤1 条额度 |
| 10:45 | Eval | — | |
| 11:17 | BiliPatrol-AM | 评论 ≤1 + 点赞 ≤6 | AM 锚点,不动;评论链 top1-2 |
| **11:53** | **Comment-AM(新)** | **评论 ≤1** | 独立发现池(热榜+自有关键词),错峰 +36min |
| 12:17 | BiliInbox | 回复 | 与 11:53 评论隔 24min,不同动作类型 |
| 12:45 | Eval | — | |
| 13:00 | Send | 动态(若额度未用) | |
| 14:45 / 16:17 / 16:45 | Eval / Inbox / Eval | 回复 | 午后低频段,无评论无点赞 |
| 17:00 | Send | 动态 | |
| 18:45 | Eval | — | |
| 20:17 | BiliInbox | 回复 | 晚间回复窗口(不动) |
| 20:45 | Eval | — | 只读 |
| **20:47** | **BiliPatrol-PM(移)** | **评论 ≤1 + 点赞 ≤6** | 由 20:17 移来,与 inbox 错开 30min |
| 21:00 | Send | 动态(若额度未用) | 与 20:47 巡逻隔 13min,不同类型 |
| **21:33** | **Comment-PM(新)** | **评论 ≤1** | 错峰 +46min;裸 run+auto 自带 0–180s 抖动 |
| 22:45 | Eval | — | 此后至 08:00 无评论/点赞 |

## 4. 设计理由

- **两个评论集群**:11:17–11:56(午间)与 20:47–21:36(晚间)。集群内两条评论
  天然间隔 36/46 分钟(代码 10min 闸兜底);集群间隔约 9 小时。每日计划评论
  ≤4 条,符合 t1「起步 1–3、上限 5」并给人工留 1 条余量。
- **独立评论任务排在巡逻之后**:patrol 用当日轮换单一关键词搜索,top1-2 进评论链;
  独立 run 的发现层是 热榜+独立关键词(不同候选池)。排在后面可接住巡逻没覆盖的
  热榜候选,且 `seen` 去重保证两路永不重复评论同一视频。
- **20:47 而非 20:17**:与 inbox 20:17 错开 30 分钟消除 P1 碰撞,同时保住晚间
  黄金时段(评论落在流量高峰里更容易被自然流量看到);AM 侧 11:17 与 12:17
  inbox 本就隔 60 分钟,不动。
- **深夜安静**:22:45 后无任何评论/点赞。夜间 inbox(0:17/4:17)保留——多为
  只读空转,偶尔回复深夜评论也符合真人;若日后想更保守可裁(见 §8)。
- **动态发布不动**:radar 主链 6 个 Send 窗口 + ≤1 条/天 + 影响力≥8 选题闸,
  本身就是低频、内容驱动、时点由新闻到达时间决定——保留这种"事件驱动"特征
  恰好是去机械化的加分项。

## 5. 每日预算上限总表(最坏情况)

| 动作 | 计划上限 | 硬闸(代码/config) |
|---|---|---|
| 动态发布 | ≤1 | `bili_max_per_day=1` + 影响力≥8 + 24h 冷却 |
| 评论 | ≤4(2 巡逻 + 2 独立) | `comment_daily_max=5`;间隔≥10min;同 UP≤1/天;同视频永不二评;单次运行≤1 |
| 点赞 | ≤12(6×2 巡逻) | `MAX_LIKES_PER_RUN=6`,间隔 6s |
| 回复 | 理论 ≤30(5×6),实际看来信(通常 0–5) | `MAX_REPLIES_PER_RUN=5`,间隔 8s,guard 拦噪音 |
| 关注 | 未排程 | `MAX_FOLLOWS_PER_RUN=1`(仅手动) |

## 6. 代码已内置的节律保护(无需改动)

- 单条流水线内:点赞 6s / 回复 8s / 评论发布后→下一动作 10s;发后复核
  8s+3×20s(`bili_comment.py:1082-1091`)。
- 两条评论 ≥10min、同 UP ≤1/天、同视频终身一次(`budget_gate`,
  `bili_comment.py:758-786`,按 bili_audit 当日实际计数,config 只可收紧)。
- 裸 run+auto 启动随机延迟 0–180s(`STARTUP_DELAY_MAX`,去机械节律);
  patrol 链无抖动——已知局限,见 §9。
- 风控硬停矩阵:-352/-412/-509/12015/12016→停 30min;折叠/隐藏/消失→停 24h;
  验证码→等人工 resume。硬停期间所有发布被 `halted_until` 门拦住,
  节奏表自动"失活",无需人工改计划。

## 7. 定时任务调整清单(未注册,待用户确认后执行)

共 2 项:①评论任务从「每 2h」改为「每日两班」;②patrol-PM 20:17→20:47。
其余任务(含 Eval/Send/Inbox/Patrol-AM)零改动。

```powershell
# 前置查看(可选)
schtasks /Query /TN AI-Radar-Comment /V
schtasks /Query /TN AI-Radar-BiliPatrol-PM /V

# ① 评论任务:删旧建新(与 install-comment-task.ps1 同款 Action/Settings,
#    仅触发器从「每 2h :15」改为「每日固定两班」)
Unregister-ScheduledTask -TaskName 'AI-Radar-Comment' -Confirm:$false
$dir  = 'D:\know\AI-Radar'
$a    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c cd /d $dir && py -3.12 bili_comment.py run"
$set  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
$tAM  = New-ScheduledTaskTrigger -Daily -At 11:53
$tPM  = New-ScheduledTaskTrigger -Daily -At 21:33
Register-ScheduledTask -TaskName 'AI-Radar-Comment-AM' -Action $a -Trigger $tAM -Settings $set `
  -Description 'AI评论管线·午间班:独立发现池(热榜+关键词),错峰于 11:17 巡逻后 36min;模式见 config.json comment_mode'
Register-ScheduledTask -TaskName 'AI-Radar-Comment-PM' -Action $a -Trigger $tPM -Settings $set `
  -Description 'AI评论管线·晚间班:错峰于 20:47 巡逻后 46min;模式见 config.json comment_mode'

# ② patrol-PM 与 inbox 20:17 错开 30 分钟
schtasks /Change /TN AI-Radar-BiliPatrol-PM /ST 20:47

# 验证
schtasks /Query /TN AI-Radar-Comment-AM /V
schtasks /Query /TN AI-Radar-Comment-PM /V
schtasks /Query /TN AI-Radar-BiliPatrol-PM /V
```

回滚:

```powershell
Unregister-ScheduledTask -TaskName 'AI-Radar-Comment-AM' -Confirm:$false
Unregister-ScheduledTask -TaskName 'AI-Radar-Comment-PM' -Confirm:$false
schtasks /Change /TN AI-Radar-BiliPatrol-PM /ST 20:17
# 恢复全天每 2h 评论任务:重跑 D:\know\AI-Radar\install-comment-task.ps1
```

## 8. 可选增强(本次不做,仅记录)

- **触发器随机延迟**:`New-ScheduledTaskTrigger -Daily -At 11:53 -RandomDelay (New-TimeSpan -Minutes 20)` 可让两班评论每天实际触发时刻浮动 ≤20min,进一步去机械化。代价:与巡逻的 36/46min 错峰变成 16–66min 浮动区间(10min 间隔闸仍兜底)。建议 L2 稳定运行后再考虑。
- **夜间 inbox 裁剪**:若想深夜零写动作,可把 BiliInbox 改为 8/12/16/20 点四班(砍 0:17/4:17)。当前保留全六班(任务约定"每 4h 保持")。
- **patrol 抖动**:patrol 无启动抖动(裸 run+auto 才有 0–180s)。若要,可给 `bili_ops.cmd_patrol` 加同款随机延迟——代码改动,另行任务。

## 9. 已知局限

- 定时任务是**固定时刻**(11:53/20:47/21:33 每天相同),真人是浮动的;
  当前靠"非整点分钟 + 混合动作类型 + 低总量"缓解,彻底解法见 §8 前两项。
- L1(review)阶段实际发布由人工 `send` 触发,时刻不受本表约束——人工节奏
  天然去机械化,只需建议**当日审批当日发**(楼中楼/热评有时效)。
- 本表基于 2026-11-12 实测;若日后新增/改名任务,以 `schtasks /Query` 实测为准。

## 10. 与阶段的关系(L0→L2)

| 阶段 | comment_mode | 本表意义 |
|---|---|---|
| L0(当前) | dryrun | 各班次只出草稿+留档,不发布;调整定时任务零风险,随时可做 |
| L1 | review | 草稿在 11:17–12:00 / 20:47–21:36 两个窗口入队;人工 send 建议同窗口完成 |
| L2 | auto | 发布时刻即本表;独立班次自带 0–180s 抖动 |

---

*变更记录:2026-11-12 t12 初版(现状实测 → 目标节奏 → 调整命令清单待确认)。*
