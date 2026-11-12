# 统一审核 guard 规则清单(guard-rules.md)

> 任务 t9(2026-11-12)。目标:把散落三处、近似但不完全一致的回复/评论审核规则
> 统一成一套**代码级**分层规则,reply(inbox 回复)与 comment(视频评论)双通道
> 共用,动态压缩/发布链(shrink/dynamic)复用同一粗筛。设计依据
> [comment-design.md](comment-design.md) §5.1;红线:radar.py(py3.7 主链)零改动。

## 1. 统一前的三处历史规则

| 位置 | 常量/函数 | 覆盖 | 来源 |
|---|---|---|---|
| bili_ops.py | `_REPLY_BAD_RE` | 乱码 + 助手腔(inbox 回复线) | 原始版本 |
| bili_publish.py | `_SHRINK_BAD_RE` | 上表全部 + 元对话(压缩稿粗筛) | commit 63848bc |
| bili_publish.py | `_quality_guard` 内联正则 | `_SHRINK_BAD_RE` 全部 + `claude-code:`/`[claude-code` 标记(发布前终审) | commit 63848bc |
| bili_ops.py | `_REPLY_TONE_RE`(t11) | 水评/养成系口癖 + 助手腔补项(inbox 回复线) | t11 校准 2026-11-12 |

统一原则:**各通道相对其旧规则只紧不松**(宁缺毋滥),旧常量名与语义保留,
防止引用方(article 清洗 / bili_comment.py)断链。

## 2. 分层规则清单(bili_ops.py 统一 guard 段)

| 层 | 常量 | 淘汰码 | 内容 | 词条来源 |
|---|---|---|---|---|
| L1 | `_GARBLED_RE` | `garbled` | `API Error[:：]|不安全或敏感内容|\uFFFD|[\uE000-\uF8FF]|[\x00-\x08\x0b\x0c\x0e-\x1f]`(乱码/错误文本/PUA/控制字符) | 旧 `_REPLY_BAD_RE` 前半,逐字保留 |
| L2 | `_ASSISTANT_RE` | `assistant_tone` | `为您|帮你|作为一个AI|作为一名AI|仅供参考|希望这(篇\|条\|份\|能)|祝你` + 行首 `以下是/好的，/当然！` 客套 | 旧 `_REPLY_BAD_RE` 后半,逐字保留 |
| L3 | `_META_RE` | `meta_tone` | `编译稿|传(得)?完整|只收到|我这边|补写|编造|没法凭|无法凭|请提供|请把|贴给我|两个建议|建议(如下\|一\|二)|未收到|收到(的\|了)?(开头\|半截\|部分)|我(无法\|没法\|不能)(凭\|确认\|完成)` + `claude-code:|\[claude-code` | 旧 `_SHRINK_BAD_RE` 增量(commit 63848bc)+ `_quality_guard` 独有的 claude-code 标记 |
| L4 | `_FLAME_RE` | `flame` | `小粉红|喷子|杠精|废青|跪舔|五毛|美分|台蛙|支那|辱华|崇洋媚外|带节奏|nmsl|NMSL|妈还在|钩直饵咸`(引战/人身攻击) | t9 新增(t1 调研 §6.4"不接战"红线) |
| L5 | `_AD_RE` | `ad_link` | `https?://|www\.|\.com|\.cn|\.net|b23\.tv|二维码|网盘|百度网盘|加群|进群|拉群|私信我|滴滴我|领取资料|关注我的?(频道\|账号\|公众号)|VX|v信|微信公众号|优惠|折扣|代购` | 设计 §5.1 AD_RE,逐字 |
| L6 | `_POLITICS_RE` | `politics` | `领导人|政府应|国务院|台湾问题|香港问题|新疆|选举|政党|意识形态|体制问题|色情|赌博|枪支|暴力抗法|民族分裂` | 设计 §5.1 POLITICS_RE,逐字(保守高置信,主判定在 understand LLM sensitive 字段) |
| L7 | `_PRIVACY_CORE_RE` / `_PRIVACY_RE` | `privacy_leak` | core:`作为一个?AI|我是AI|人工智能助手|AI助手|由AI生成|本(评论\|回复)由|NImraaaa运营|AI-?Radar|radar\.py|bili_(ops\|comment\|publish)|[CD]:\\|/Users/`;comment 通道加 `自动化|脚本|程序` | 设计 §5.1 ASSISTANT_RE 增量拆层 |
| L8 | `_WATER_RE` | `water_word` | `说得好|学到了?|学习了|前排|沙发|打卡|路过|[mM]ark|插眼|蹲一?个|帮顶|顶一?下|赞一?个|厉害了?|太强了?|火钳|三连|已关注|支持一?下|同问|自动回复|占楼|求三连|求关注|本鲸|白饭|贴贴|[rR]ua|捏捏|嗷呜|摸摸头` | 设计 §5.1 WATER_RE ∪ t11 `_REPLY_TONE_RE` 词条 |
| L9 | `_AT_RE` | `has_at` | `@`(任何 @ 提及,防引流/对线@) | t9 新增 |
| — | 字数 | `too_short` / `too_long` | 见 §3 通道矩阵 | reply 沿用旧 160 顶;comment 按设计 30-150 |
| — | 相关性 | `off_topic` | comment 通道静态兜底 `_topic_related`:含领域词(ai/人工智能/大模型/gpt/llm/deepseek/claude/openai/英伟达/智能体/aigc/agi),或标题命中任一**非虚词**拉丁实词(t5 生产校准 2026-11-12,原 ≥2 会误杀英文标题视频的基准细节类草稿;虚词表 the/and/a/an/of/to/in/on/for/with/is/are/vs/new),或与标题共享任一 CJK 2-gram → 相关;topic 为空不做兜底 | t9 新增(设计"频道相关性";LLM understand 相关性为主判定) |

粗筛正则 **`BAD_TEXT_RE` = L1|L2|L3**(供 shrink/dynamic 通道与外部复用,
= 旧 `_SHRINK_BAD_RE` 覆盖面 + claude-code 标记,只紧不松)。

## 3. 通道矩阵(guard_text(text, channel, topic=''))

| 层 | reply(inbox 回复) | comment(视频评论) | shrink/dynamic(压缩/发布链) |
|---|---|---|---|
| L1 乱码 | ✔ | ✔ | ✔(BAD_TEXT_RE) |
| L2 助手腔 | ✔ | ✔ | ✔ |
| L3 元对话 | ✔ | ✔ | ✔ |
| L4 引战 | ✔ | ✔ | ✘(动态正文可能引用争议事件报道) |
| L5 广告 | ✔ | ✔ | ✘(动态固定带"原文: https://…"链接) |
| L6 政治 | ✔ | ✔ | ✘(新闻编译稿由 LLM sensitive + radar 链把关) |
| L7 隐私 | core(不含 自动化/脚本/程序) | 全量(+自动化/脚本/程序) | ✘ |
| L8 水评 | ✔ | ✔ | ✘("太强了"可以是新闻引用) |
| L9 @ | ✔ | ✔ | ✘(动态 tag 机制除外,不经过本函数) |
| 字数 | 1-160(旧顶保留) | 30-150(设计值) | 无(字数/中文占比由调用方 `_shrink_ok`/`_quality_guard` 原有逻辑把关) |
| topic 相关性 | ✘ | topic 非空时兜底 | ✘ |

**通道差异理由**:回复串里讨论"这个脚本/程序/自动化"是正常技术语境,只在面向
公众评论区的 comment 通道收紧(设计 §5.1 对 ASSISTANT_RE 的要求);shrink/dynamic
是长新闻编译稿,水评词/链接/争议词是合法内容,只保留"绝不可能是新闻正文"的
L1-L3。

## 4. 调用方接线

- **bili_ops._draft_reply**(inbox 回复线):`_REPLY_BAD_RE.search or _REPLY_TONE_RE.search`
  → `ok, code, hit = guard_text(draft, 'reply')`;skip_reason 仍为 `'guard'`,
  审计 `skip_guard` 口径与日报"被审核拦下"统计不变;[跳过]→`no_value` 出口(t11)保留。
- **bili_publish._shrink_ok**(压缩稿):`_SHRINK_BAD_RE.search` → `_guard(out)[0]`
  (懒加载,见下);其余(预算/收尾/中文占比)不动。
- **bili_publish._quality_guard**(发布前终审):内联正则 → `_guard(text, 'dynamic')`;
  **exit 4 语义与"质量审核未通过:"消息前缀保持**(radar.py 只认 returncode==0
  并解析 `dyn_url=`,行为不变);中文占比/长度检查不动。
- **bili_comment.py(t4/t5)**:按设计 §1.2 `from bili_ops import _audit, _REPLY_BAD_RE`
  不受影响;建议评论静态层直接 `from bili_ops import guard_text` 用
  `guard_text(text, 'comment', topic=视频标题)`,evidence 回查与 shingle 模板
  查重仍按设计留在 bili_comment 自身。
  注(t5 实测 2026-11-12):bili-cli `comment check` 对 `--type draw` 恒返回
  -404(评论存在),bili_comment.post_check 已加 `comment list` 兜底复核。

## 5. 兼容性保证(冒烟验证,见 [tests.md](tests.md))

1. `_REPLY_BAD_RE` **逐字未动**(article._clean_summary / bili_comment 复用);
2. `_REPLY_TONE_RE = _WATER_RE` 别名保留,t11 旧引用不失效;
3. `BAD_TEXT_RE ⊇ 旧 _SHRINK_BAD_RE` 覆盖面(逐词条探测)+ claude-code 标记;
4. `_quality_guard` exit 4 与消息前缀不变;`_shrink_ok` 判定只紧不松;
5. 循环 import 规避:bili_ops 顶层 `from bili_publish import …`,故 bili_publish
   侧 `_guard()` **函数内懒加载** `from bili_ops import guard_text`(运行时两模块
   均已就绪;import 失败即抛错中断发布,fail-closed 绝不静默绕过);
6. radar.py 零改动(`git diff radar.py` 为空)。

## 6. 已知取舍

- `[mM]ark` 会命中 "Markdown" 中的 mark——误杀代价=少发一条,宁缺毋滥,接受。
- reply 通道新增 L3-L9 为"只紧不松"收紧:极端情况(如回复需引用 `编造` 一词
  驳斥谣言)会被拦——按 t3"宁缺毋滥"原则接受,LLM 起草人设本就禁止此类表述。
- `_topic_related` 为保守放行型兜底(任一信号即相关),主判定在 understand LLM。
- 「三连」全拦不收窄(t11 探针边界裁量,2026-11-12):草稿复述网友"已三连支持~"
  会被 water_word 拦。裁量=维持——①「求三连|求关注」拉票形态已并存收录,收窄
  「三连」只放宽水评线、不增加召回;②复述点赞表态的草稿本身属低价值回复,拦下
  符合宁缺毋滥;③真实 inbox 同类误杀若高频,再议收窄(改 _WATER_RE 单点即可)。
- 静态相关性拉丁阈值 2→1(t5 生产校准 2026-11-12):19:10/19:12 两条"基准细节
  类"草稿被判 off_topic,其一(64.9% RoboDojo/Carbon 分项评论,标题 Claude
  Fable 5.2)只命中 'fable' 一个拉丁词、CJK 2-gram 零重叠——英文标题视频
  CJK 2-gram 常为零重叠,≥2 阈值系统性偏严。校准为命中任一**非虚词**拉丁实词
  即相关(虚词表 14 词):静态兜底是放行型,假阳性由下游 LLM③ 复核兜住,假阴性
  则该条评论被终审丢弃,不对称性支持放宽;零信号草稿仍拦(回归探针验证)。
- `_load_audit_counts` 归并扩展(t5 提出integrator/合作修复 2026-11-12):
  comment_skip/comment_drop/comment_budget_block 归入 skip,评论管线动作落
  日报"被审核拦下"列(设计 §11 假设补齐)。

## 7. 重构 diff(t9 纯净 diff,相对任务开工前快照)

### bili_ops.py(3 处:模块头守则说明 / 新增统一 guard 段 / _draft_reply 切换)

```diff
@@ -10,8 +10,10 @@
     纯表情/灌水评论无实质可回应则跳过不回(宁缺毋滥,2026-11-12 t11 校准)
   - 站内互动: 点赞 <=MAX_LIKES_PER_RUN,关注 <=MAX_FOLLOWS_PER_RUN
   - 所有写动作追加审计日志 bili_audit.jsonl
-  - 回复过双层轻审核: _REPLY_BAD_RE(乱码/助手腔,原样保留,article 复用)
-    + _REPLY_TONE_RE(水评/养成系口癖,t11 按 t3 人设规范新增)
+  - 回复过统一审核 guard_text(t9,规则清单 docs/guard-rules.md):乱码/助手腔/
+    元对话/引战/广告/政治敏感/@提及/水评口癖/隐私泄露/字数,reply 与 comment
+    双通道共用。_REPLY_BAD_RE(乱码/助手腔,article/bili_comment 复用,原样
+    保留)与 _REPLY_TONE_RE(t11 水评口癖,= _WATER_RE 兼容别名)保留为常量
```

```diff
@@ -67,8 +69,129 @@ _REPLY_TONE_RE = re.compile(
+# ---------------- 统一审核 guard(t9,2026-11-12):reply/comment 双通道 ----------------
+# 设计依据 docs/comment-design.md §5.1;规则清单与通道矩阵见 docs/guard-rules.md。
+# 目标:把三处历史规则(bili_ops._REPLY_BAD_RE / bili_publish._SHRINK_BAD_RE
+# (commit 63848bc) / bili_publish._quality_guard 内联正则)统一为一套分层规则,
+# 按通道裁剪,各通道相对旧规则只紧不松:
+#   reply    = inbox 回复线(旧 _REPLY_BAD_RE + t11 _REPLY_TONE_RE 的严格超集)
+#   comment  = 视频评论区留言(设计稿 §5.1 全量,含隐私全量/30-150 字/topic 兜底)
+#   shrink/dynamic = 动态编译稿压缩/发布链粗筛(= 旧 _SHRINK_BAD_RE 覆盖面
+#              + claude-code 标记;字数与中文占比仍由 bili_publish 原有逻辑把关)
+# 常量 _REPLY_BAD_RE 原样保留(article._clean_summary 与 bili_comment.py 复用)。
+# (完整分层正则 L1-L9 与 guard_text/_topic_related 函数体见 bili_ops.py
+#  统一 guard 段,共 ~120 行;此处不重复贴出,以仓库代码为准。)
+_REPLY_TONE_RE = _WATER_RE   # t11 兼容别名:词条已并入水评层,旧引用不失效
 # LLM 判定"无实质可回应"的跳过出口(t11 宁缺毋滥)
```

> 注:该 hunk 邻近的 `_SKIP_RE` 增补直角引号「」属 **t11** 的改动(恰在 t9 快照
> 前后落盘),非 t9 内容,已在 diff 中排除。

```diff
@@ -148,9 +271,9 @@ def _draft_reply(commenter, text):
     if not draft or len(draft) > 160:
         return None, 'too_long', ''
-    m = _REPLY_BAD_RE.search(draft) or _REPLY_TONE_RE.search(draft)
-    if m:                                    # 回复轻审核:乱码/助手腔/水评/宠物口癖
-        return None, 'guard', m.group(0)[:20]
+    ok, code, hit = guard_text(draft, 'reply')
+    if not ok:   # 统一轻审核(t9):乱码/助手腔/元对话/水评/宠物口癖/广告/@/政治/隐私
+        return None, 'guard', hit or code      # hit=命中词;太短/超长码兜底(上游已拦)
     return draft, '', ''
```

### bili_publish.py(3 处:_SHRINK_BAD_RE→_guard 懒加载 / _shrink_ok / _quality_guard)

```diff
@@ -108,14 +108,16 @@
-_SHRINK_BAD_RE = re.compile(
-    r'API Error[:：]|不安全或敏感内容|\uFFFD|[\uE000-\uF8FF]|[\x00-\x08\x0b\x0c\x0e-\x1f]'
-    r'|为您|帮你|作为一个AI|作为一名AI|仅供参考|希望这(?:篇|条|份|能)|祝你'
-    r'|(?:^|\n)\s*(?:以下是|好的[,，!！.。]|当然[,，!！])'
-    r'|编译稿|传(?:得)?完整|只收到|我这边|补写|编造|没法凭|无法凭|请提供|请把|贴给我'
-    r'|两个建议|建议(?:如下|一|二)|未收到|收到(?:的|了)?(?:开头|半截|部分)'
-    r'|我(?:无法|没法|不能)(?:凭|确认|完成)')
+# 统一审核入口(t9,2026-11-12):乱码/助手腔/元对话粗筛正则(原 _SHRINK_BAD_RE,
+# commit 63848bc)移至 bili_ops 的分层 guard(garbled|assistant|meta,即
+# BAD_TEXT_RE,已含原 _quality_guard 独有的 claude-code 标记),与 reply/comment
+# 双通道共用,规则清单见 docs/guard-rules.md。bili_ops 顶层 import 本模块,
+# 此处须函数内懒加载避免循环 import;import 失败即抛错中断发布——审核 fail
+# closed,绝不静默绕过。
+def _guard(text, channel='shrink'):
+    """统一文本审核懒加载入口:透传 bili_ops.guard_text,返回 (ok, code, hit)。"""
+    from bili_ops import guard_text
+    return guard_text(text, channel)
```

```diff
@@ -143,8 +145,9 @@ def _ends_badly(t):
 def _shrink_ok(out, limit):
-    """压缩稿合格判定:无异常文本、不超预算、完整收尾、中文占比达标。"""
-    if not out or _SHRINK_BAD_RE.search(out):
+    """压缩稿合格判定:无异常文本、不超预算、完整收尾、中文占比达标。
+    异常文本粗筛走统一 guard(t9,= 旧 _SHRINK_BAD_RE 覆盖面 + claude-code)。"""
+    if not out or not _guard(out)[0]:
         return False
```

```diff
@@ -252,19 +255,9 @@
-    bad = re.compile(
-        r'API Error[:：]|不安全或敏感内容|\uFFFD|[\uE000-\uF8FF]'
-        ...(旧内联正则全文,= _SHRINK_BAD_RE + claude-code 标记)...
-        r'|claude-code:|\[claude-code')
-    if bad.search(text):
-        print('质量审核未通过: 检测到乱码/错误文本/助手腔')
+    bad = _guard(text, 'dynamic')     # 统一粗筛(t9)= 旧内联正则 + claude-code 标记
+    if not bad[0]:
+        print('质量审核未通过: 检测到乱码/错误文本/助手腔(%s: %s)' % (bad[1], bad[2]))
         sys.exit(4)
```

> 完整未裁剪 diff 文本:`git diff --no-index <开工前副本> <当前>`(开工前副本已
> 删除,以本清单 + 仓库代码为准;冒烟见 [tests.md](tests.md))。

## 8. 验证

`py -3.12 -m py_compile` 两文件通过;冒烟 146 项断言全过(记录:[tests.md](tests.md));
`git diff radar.py` 为空。冒烟脚本按约定跑完删除。
