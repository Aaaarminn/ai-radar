# -*- coding: utf-8 -*-
"""B站账号运营脚本(运行于 Python 3.12,调 bili-cli)。

账号定位(用户 2026-11-12 授权全权运营):
  自媒体/AI 领域信息同步博主——转播消息、科普、信息同步。
  不是卖萌宠物号:不无意义灌水,所有互动围绕内容质量。

守则(硬编码预算,防风控防刷屏):
  - 回复评论: 每次 <=MAX_REPLIES_PER_RUN 条,回复基于动态原文事实,不编造,不接战
  - 站内互动: 点赞 <=MAX_LIKES_PER_RUN,关注 <=MAX_FOLLOWS_PER_RUN
  - 所有写动作追加审计日志 bili_audit.jsonl
  - 回复/内容过 _quality_guard 同款审核(乱码/助手腔拦截)

用法:
  py -3.12 bili_ops.py inbox          处理未读:评论回复/@我 → LLM 起草 → 回复
  py -3.12 bili_ops.py patrol         站内巡查:搜AI内容 → 适量点赞/关注
  py -3.12 bili_ops.py report         账号报告(资料/动态列表/数据)
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili_publish import _run_bili, _claude_cli as _claude  # noqa: E402  复用 bili-cli 通道与 claude CLI

BASE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(BASE, 'bili_audit.jsonl')

# 回复轻审核:乱码/错误文本/助手腔(发布级 _quality_guard 有字数门槛,回复不适用)
_REPLY_BAD_RE = re.compile(
    r'API Error[:：]|不安全或敏感内容|\uFFFD|[\uE000-\uF8FF]'
    r'|[\x00-\x08\x0b\x0c\x0e-\x1f]'
    r'|为您|帮你|作为一个AI|作为一名AI|仅供参考|希望这(?:篇|条|份|能)|祝你'
    r'|(?:^|\n)\s*(?:以下是|好的[,，!！.。]|当然[,，!！])')

MAX_REPLIES_PER_RUN = 5
MAX_LIKES_PER_RUN = 6          # 每天约 2 次 patrol,合计 <=12 赞/天,低频防风控
MAX_FOLLOWS_PER_RUN = 1

# 站内巡查关键词(轮换),AI 信息同步博主的内容领域
PATROL_KEYWORDS = ['人工智能', 'AI 大模型', 'OpenAI', '英伟达', 'DeepSeek', 'Anthropic Claude']

# 回复起草人设(system 片段)
PERSONA = (
    '你运营着一个B站账号,定位是 AI 领域的信息同步/科普博主(自媒体),'
    '不是卖萌助手。回复评论时:专业、克制、有信息量;只依据给定动态原文的事实,'
    '不知道就说不知道并建议看原文链接;不接战不抬杠,遇到质疑礼貌给出依据;'
    '不用表情包堆砌,不喊口号;长度不超过100字,中文,B站社区口吻但保持专业。'
)


def _audit(action, detail):
    rec = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'action': action}
    rec.update(detail)
    with open(AUDIT, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def cmd_inbox():
    """处理未读消息:拉收到的评论回复与@我,LLM 起草回复并发出去。
    结构实测:feed inbox -> data{unread{...}};feed replies/mentions 返回条目带 reply_with。"""
    rc, payload, out = _run_bili(['feed', 'inbox'], timeout=60)
    if not (payload and payload.get('ok')):
        print('[ops] inbox 拉取失败: %s' % out.strip()[:200])
        sys.exit(3)
    unread = (payload.get('data') or {}).get('unread') or {}
    n_reply = int(unread.get('replies') or 0)
    n_mention = int(unread.get('mentions') or 0)
    print('[ops] 未读: 回复%d @我%d' % (n_reply, n_mention))
    if n_reply == 0 and n_mention == 0:
        print('[ops] 无需处理。')
        return

    handled = 0
    if n_reply:
        handled += _handle_feed('feed', 'replies', min(n_reply, MAX_REPLIES_PER_RUN))
    if n_mention and handled < MAX_REPLIES_PER_RUN:
        handled += _handle_feed('feed', 'mentions',
                                min(n_mention, MAX_REPLIES_PER_RUN - handled))
    print('[ops] 本轮回复 %d 条。' % handled)


def _handle_feed(group, sub, limit):
    """拉 feed replies/mentions 条目,逐条起草并回复。返回成功回复数。"""
    rc, payload, out = _run_bili([group, sub, '--limit', str(max(limit, 1))], timeout=60)
    items = (payload.get('data') or {}).get('items') if payload and payload.get('ok') else None
    if not items:
        print('[ops] %s %s 无条目或结构变化: %s' % (group, sub, out.strip()[:150]))
        return 0
    done = 0
    for it in items[:limit]:
        rw = it.get('reply_with') or {}
        cmd = rw.get('command') if isinstance(rw, dict) else None
        commenter = it.get('user', {}).get('name') if isinstance(it.get('user'), dict) else ''
        text = it.get('content', {}).get('message') if isinstance(it.get('content'), dict) else ''
        if not cmd:
            # 结构变化时保底:只记审计不动手,避免误发
            _audit('skip_no_command', {'sub': sub, 'item_keys': list(it.keys())})
            continue
        prompt = ('%s\n\n网友「%s」在你的B站动态下评论:%s\n\n请直接输出回复正文,'
                  '只要正文本身,不要任何解释或引号。'
                  % (PERSONA, commenter, text))
        draft = _claude(prompt)
        if not draft:
            _audit('skip_draft_fail', {'commenter': commenter, 'text': (text or '')[:80]})
            continue
        draft = draft.strip().strip('\"“”')
        if len(draft) > 150:                     # 超长二次收紧,仍超则放弃
            draft = _claude(prompt + '\n(严格压缩到80字内)')
            draft = (draft or '').strip().strip('\"“”')
        if not draft or len(draft) > 160:
            _audit('skip_too_long', {'commenter': commenter})
            continue
        if _REPLY_BAD_RE.search(draft):          # 回复轻审核:乱码/助手腔
            _audit('skip_guard', {'draft': draft[:80]})
            continue
        rc2, p2, out2 = _run_bili(cmd if isinstance(cmd, list) else cmd.split(), timeout=60)
        ok = bool(p2 and p2.get('ok'))
        _audit('reply', {'to': commenter, 'draft': draft, 'ok': ok,
                         'err': (p2 or {}).get('error')})
        print('[ops] 回复%s -> %s' % (commenter, 'OK' if ok else out2.strip()[:120]))
        if ok:
            done += 1
        time.sleep(8)                            # 动作间隔,降低风控风险
    return done


def cmd_patrol():
    """站内巡查:轮换关键词搜AI视频,点赞相关度高的(预算内);累计关注<=1。"""
    kw = PATROL_KEYWORDS[int(time.time() // 86400) % len(PATROL_KEYWORDS)]
    print('[ops] 巡查关键词: %s' % kw)
    rc, payload, out = _run_bili(['search', 'video', kw, '--size', '10'], timeout=60)
    items = (payload.get('data') or {}).get('items') if payload and payload.get('ok') else None
    if not items:
        print('[ops] 搜索失败或无结果: %s' % out.strip()[:150])
        sys.exit(3)
    liked = 0
    for it in items:
        if liked >= MAX_LIKES_PER_RUN:
            break
        bvid = it.get('bvid') or ''
        title = it.get('title') or ''
        if not bvid:
            continue
        # 只点赞标题含明确 AI 关键词的(垂直领域相关度)
        t = title.lower()
        if not any(k.lower() in t for k in ('ai', '人工智能', '大模型', 'gpt', 'llm',
                                            'deepseek', 'claude', 'openai', '英伟达', '智能体')):
            continue
        rc2, p2, out2 = _run_bili(['video', 'like', bvid], timeout=60)
        ok = bool(p2 and p2.get('ok'))
        _audit('like', {'bvid': bvid, 'title': title[:60], 'ok': ok})
        print('[ops] 点赞 %s %s -> %s' % (bvid, title[:30], 'OK' if ok else 'FAIL'))
        if ok:
            liked += 1
        time.sleep(6)
    print('[ops] 本轮点赞 %d 个。' % liked)


def cmd_report():
    for args in (['me', 'show'], ['me', 'stats'], ['dynamic', 'list']):
        rc, payload, out = _run_bili(args, timeout=60)
        print('== %s ==\n%s' % (' '.join(args), out.strip()[:600]))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('inbox', 'patrol', 'report'):
        print(__doc__)
        sys.exit(1)
    {'inbox': cmd_inbox, 'patrol': cmd_patrol, 'report': cmd_report}[sys.argv[1]]()


if __name__ == '__main__':
    main()
