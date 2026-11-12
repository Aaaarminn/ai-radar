# 冒烟测试记录(tests.md)

> AI-Radar 本地无 pytest 环境,按团队约定:内嵌断言脚本跑完即删,结果记录于本文件。
> 记录格式:日期 / 任务 / 命令 / 结果 / 结论。

## 2026-11-12 t9 统一审核 guard(bili_ops.guard_text + bili_publish._guard)

- **命令**:`py -3.12 _smoke_guard.py`(脚本已按约定跑完删除;断言集覆盖见下)
- **环境**:Python 3.12,工作目录 D:\know\AI-Radar
- **结果**:`ALL SMOKE CHECKS PASSED`,exit 0,**146 项断言全部通过**
- **首轮失败说明**:首跑 6 项 FAIL 均为测试脚本自身缺陷,非被测代码问题:
  ①探测词 `没法确认`/`不能完成` 裸用不在旧 `_SHRINK_BAD_RE` 覆盖内(旧正则要求
  前缀"我",新规则逐字保留同语义)→ 探测串改为 `我没法确认`/`我不能完成`;
  ②`len/comment-150ok` 边界用例误带 topic='大模型' 导致正确地判 off_topic → 去掉 topic;
  ③`_REPLY_BAD_RE.pattern` 相等性断言须用 raw 字符串拼装(与文件源码字面一致)。
  修正后全绿。

### 断言覆盖(按类别)

| 类别 | 数量 | 内容 |
|---|---|---|
| old-reply | 17 | 旧 `_REPLY_BAD_RE` 每个分支在 reply 通道仍被拦(乱码/助手腔全词条) |
| old-shrink | 28 | 旧 `_SHRINK_BAD_RE` 元对话层每个词条在 shrink 通道仍被拦(bad_text) |
| old-dynamic | 28 | 同上词条在 dynamic 通道(`_quality_guard` 路径)仍被拦 |
| water | 20 | t11 `_REPLY_TONE_RE` 水评/宠物口癖词条并入 `_WATER_RE` 后仍被拦 |
| new | 15 | 新层命中:广告/引战/政治/@提及/隐私泄露(自称AI/radar.py/盘符路径) |
| chan | 5 | 通道差异:`自动化/脚本` reply 放行、comment 拦;shrink 不拦 URL(动态带原文链接) |
| len | 5 | comment 30-150 / reply ≤160 边界(160 与 150 恰好放行,161/151 拦) |
| topic | 4 | comment 相关性兜底:相关/不相关/拉丁实词≥2/无 topic 放行 |
| compat | 3 | `_REPLY_BAD_RE.pattern` 逐字未动;`_REPLY_TONE_RE is _WATER_RE`;`BAD_TEXT_RE ⊇ 旧 _SHRINK_BAD_RE` |
| surface | 15 | 两模块对外符号不缺失(`_audit`/`cmd_*`/`_shrink_ok`/`_quality_guard` 等) |
| pub | 5 | 集成:`_shrink_ok` 拦元对话/放行合格稿;`_quality_guard` exit 4 语义保持(meta 与 cjk 不足双路径)、合格文本放行 |

### 附带验证

- `py -3.12 -m py_compile bili_ops.py bili_publish.py` 通过;`import bili_ops, bili_publish` 无副作用。
- `git diff radar.py` 为空——radar.py(py3.7 主链)零改动红线守住。
- 懒加载无循环 import:bili_publish(`__main__` 或被 import)调用 `_guard` 时
  函数内 `from bili_ops import guard_text`,运行时两模块均已就绪,冒烟全路径验证。

### 结论

统一 guard 替换三处历史规则后,旧行为全覆盖(只紧不松),通道差异符合
docs/guard-rules.md 通道矩阵;发布链 `_quality_guard` 的 exit 4 语义与消息前缀
保持,radar.py 发布子进程判定不受影响。
