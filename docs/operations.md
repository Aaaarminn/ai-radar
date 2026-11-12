# AI-Radar 运营手册

B站账号 NImraaaa(mid 3747582327131132,「AI 领域资讯编译与信息同步」)日常运营与
『看懂再评论』管线使用说明。所有功能本地运行,经 bili-cli 通道写站,全程审计。

## 1. 模块地图

| 文件 | 职责 | Python |
|---|---|---|
| `radar.py` | 主链:邮件抓取→双维度评估→池化→邮件推送(**零改动红线,勿动**) | 3.7 |
| `bili_ops.py` | B站互动:inbox 回复 / patrol 巡查(含评论链) / report 日报 / article 专栏草稿;`guard_text` 统一审核(规则见 `docs/guard-rules.md`) | 3.12 |
| `bili_publish.py` | bili-cli 通道底座:`_run_bili`/`_claude_cli` 等,动态发布 | 3.12 |
| `bili_comment.py` | 『看懂再评论』五层管线:发现→理解→起草→审核→发布(t4/t5/t6) | 3.12 |
| `config.json` | 全部可调参数(见 §6) | - |

数据文件:`bili_audit.jsonl`(全部写动作审计)、`studies/<bvid>.json`(理解留档)、
`comment_state.json`(评论状态:seen/预算/硬停)、`comment_queue.jsonl`(L1 审批队列)、
`metrics/<date>.json`(日报快照)、`state.json`(雷达发布记账)。

## 2. 定时任务(schtasks)

| 任务 | 节奏 | 命令 | 安装脚本 |
|---|---|---|---|
| AI-Radar-Eval | 每 2h :45 | `radar.py --eval-only` | `install-task.ps1` |
| AI-Radar-Send | 每 4h 整点(13 点起) | `radar.py --send-only` | `install-task.ps1` |
| AI-Radar-BiliInbox | 每 4h(8:17 起) | `bili_ops.py inbox` | - |
| AI-Radar-BiliPatrol-AM | 每日 11:17 | `bili_ops.py patrol` | - |
| AI-Radar-BiliPatrol-PM | 每日 20:47 | `bili_ops.py patrol` | - |
| AI-Radar-Comment-AM | 每日 11:53 | `py -3.12 bili_comment.py run` | `docs/rhythm.md §7` |
| AI-Radar-Comment-PM | 每日 21:33 | `py -3.12 bili_comment.py run` | `docs/rhythm.md §7` |

评论任务已从「每 2h :15」改为每日两班(午 11:53/晚 21:33,错峰于巡逻后),patrol-PM 从 20:17 移至 20:47 错开 inbox——理由与回滚见 `docs/rhythm.md`。

卸载评论两班任务:`schtasks /Delete /TN AI-Radar-Comment-AM /F` + `schtasks /Delete /TN AI-Radar-Comment-PM /F`。

## 3. patrol 巡查(t6 整合评论链)

`py -3.12 bili_ops.py patrol` 流程:

1. 关键词按天轮换搜索(**纯模型/工具词**:`人工智能/AI 大模型/OpenAI/Claude/
   Gemini/DeepSeek/Anthropic/大模型`;不含「英伟达」等厂商人名——t11 教训:
   给黄仁勋时政八卦点赞与「时政不碰」人设有张力);
2. 标题过滤:高亮标签清洗 → `LIKE_SKIP_RE` 剔除时政/八卦/热搜/政要人名标题
   (审计 `skip_like_filter`,落日报「被拦」列)→ 必须命中领域词;
3. 评分排序:领域词命中数优先,同分播放量高者先;
4. **top 1-2 依次进评论链** `bili_comment.patrol_pipeline`(study→understand→
   起草→审核→发布,见 §4);每候选处理完与下一动作保守间隔;
5. 其余候选点赞,单轮 ≤6 个(`MAX_LIKES_PER_RUN`),赞间隔 6s。

评论链 verdict 含义(patrol 日志与审计 `patrol_comment`):

| verdict | 含义 | patrol 后续动作 |
|---|---|---|
| posted / queued / dryrun | 发布成功 / 入 L1 审批队列 / L0 只出草稿 | 照常点赞 |
| info_fail / no_context / understand_fail / no_opportunity / draft_giveup / budget:* / dropped:* / seen / study_fail | 候选级放弃(宁缺毋滥) | 照常点赞 |
| sensitive_or_offdomain | 内容实证敏感或非 AI 域 | **连赞都不点** |
| halt | 账号级硬停(风控/验证码/折叠,见 §7) | **立即停止本轮全部写动作** |
| off / bad_bvid / module_fail | 功能关闭 / 参数异常 / 模块加载失败 | 纯点赞(退化行为) |

## 4. 评论管线(bili_comment.py)

五层:发现(关键词轮换+热榜,黑名单/近 3 天/播放≥2000/同视频永久去重)→
理解(bili-cli 拉 字幕/AI总结/热评/弹幕,三条件理解门,LLM 产出结构化 Brief;
**读不懂就放弃**,宁可少评)→ 起草(三类信息增量:补充事实/核对质疑/楼中楼答题/
真诚提问,30–100 字目标,禁水评)→ 审核(guard_text+证据回查防编造+模板查重+
LLM 相关性/安全复核)→ 发布(`--file` 传正文)+ 发后 8s 阿瓦隆复核。

预算(设计 §6.1,config 只可收紧):每日 ≤5 条、间隔 ≥10 分钟、同 UP 每日 ≤1 条、
单次运行 ≤1 条、同视频永不二评。

CLI 与退出码:

```
py -3.12 bili_comment.py study <bvid>      # 只理解不评,留档 studies/
py -3.12 bili_comment.py run [--mode M]    # 主入口(定时任务跑这个)
py -3.12 bili_comment.py run --bvid <bvid> # 指定视频(复用已有 study)
py -3.12 bili_comment.py queue             # L1 审批队列
py -3.12 bili_comment.py send <bvid>       # 审批通过后发布队列中的草稿
py -3.12 bili_comment.py check             # 发后可见性复核
py -3.12 bili_comment.py status            # 状态/预算/硬停一览
py -3.12 bili_comment.py resume            # 解除硬停(人工确认安全后)
```

run 退出码:`0` 成功(dryrun 出草稿/queue 入队/posted);`10` 无候选或模式 off;
`11` 本轮全部候选被审核放弃;`12` 预算闸拦下;`1` 参数错误;`3` 未登录;
`5/6/7` 风控硬停/需要验证码/限流(均伴随 `resume` 前的硬停);`8` 网络错误。
study 退出码:`0` 理解成功;`2` 拉取失败;`3` 无实质素材;`4` 读不懂;
`5` 敏感/域外;`1` 参数/黑名单。

## 5. 分阶段启用(L0→L2)

改 `config.json` 的 `comment_mode` 即切换,无需动代码:

| 阶段 | comment_mode | 行为 | 升级条件 |
|---|---|---|---|
| L0(当前) | `dryrun` | 只起草+留档,不发真评论 | dryrun 草稿连续 3 天质量可接受 |
| L1 | `review` | 草稿入 `comment_queue.jsonl`,人工 `queue`→`send` | 审批通过率满意 |
| L2 | `auto` | 白名单 UP 内自动发布 | — |

回退:任何时候把 `comment_mode` 改回 `off`/`dryrun` 即停;硬停态只能 `resume` 解除。

## 6. config.json 键

评论侧(`bili_comment.py` 读取,预算键**只可收紧不可放宽**):

| 键 | 当前值 | 说明 |
|---|---|---|
| `comment_mode` | `dryrun` | off / dryrun / review / auto |
| `comment_daily_max` | 5 | 每日发布上限 |
| `comment_interval_min` | 10 | 两条评论最小间隔(分钟) |
| `comment_up_daily_max` | 1 | 同一 UP 每日评论上限 |
| `comment_keyword_blacklist` | 时政/八卦/政要人名正则 | 标题命中即放弃(独立 run 路径也生效) |
| `comment_whitelist_up` / `comment_blacklist_up` | 未设 | auto 模式 UP 白/黑名单(mid 列表) |

## 7. 安全边界(硬编码,勿绕过)

- 绝不重试、绝不绕过风控:`-352/-412/-509/12015/12016` → 硬停 30 分钟;
  评论被折叠/隐藏/消失 → 硬停 24h;`-352` gaia 验证码 → 等人工 resume。
- 所有写动作(like/reply/comment/patrol_comment/skip_like_filter)落
  `bili_audit.jsonl`,日报自动统计。
- 红线内容:不评时政/八卦/竞品引战;不编造(引用必须逐字回查素材);
  不水评;不 @提及;不发广告;AI 生成内容按规定标识。

## 8. 排障

- **看最新动作**:`tail bili_audit.jsonl`;**看理解了什么**:`studies/<bvid>.json`;
- run 一直 `10` 无候选:关键词轮换当天词源少,属正常;连续多天可查
  `comment_state.json` 的 `seen` 是否过大(同视频永不二评是预期行为);
- run 返回 `5/6/7`:查 `comment_state.json` 的 `halted_until/halt_reason`,
  确认风控解除后 `py -3.12 bili_comment.py resume`;
- L1 卡队列:`queue` 看草稿 → `send <bvid>` 发布,或删队列行放弃;
- patrol 没有评论动作:看审计里有没有 `patrol_comment` / `skip_like_filter`,
  有 module_fail 则检查 `bili_comment.py` 是否 py_compile 通过;
- 定时任务没跑:`schtasks /Query /TN AI-Radar-Comment /V` 看上次结果与 NextRunTime。
