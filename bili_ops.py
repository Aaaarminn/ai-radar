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
  py -3.12 bili_ops.py report         运营日报:指标快照+动作量统计+明日建议
  py -3.12 bili_ops.py report --raw   日报之外附原始 JSON(排障用)

report 说明:
  - 拉取 me show / me stats / dynamic list,落快照 metrics/<date>.json
    (粉丝/周播放/周访客/周新粉/累计获赞/评论/转发/动态数),与前一快照自动对比
  - 从 bili_audit.jsonl 统计每日动作量(reply/like/comment/dynamic),
    发动态量另并 state.json 的 bili_sent 记账(radar.py 发布侧暂无审计)
  - 输出可贴给用户看的运营日报文本:今日发布/账号数据/趋势/明日建议
  - me stats 偶发 SSL reset(curl 35),_bili_retry 重试即可
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili_publish import (_run_bili, _claude_cli as _claude,  # noqa: E402
                          _load_json)  # 复用 bili-cli 通道/claude CLI/JSON 读取

BASE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(BASE, 'bili_audit.jsonl')
STATE = os.path.join(BASE, 'state.json')
METRICS_DIR = os.path.join(BASE, 'metrics')

# 运营日报统计的动作口径:reply/like/comment 来自审计日志;dynamic 发布量
# 合并 bili_sent 记账(radar.py 发布侧暂无审计,站点动态列表为最终权威)
REPORT_ACTIONS = ('reply', 'like', 'comment', 'dynamic')

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


# ---------------- report:数据看板/运营日报 ----------------

def _bili_retry(args, tries=3, timeout=60, pause=4):
    """带重试的 bili-cli 调用:me stats 偶发 SSL reset(curl 35),重试即可。"""
    rc, payload, out = -1, None, ''
    for i in range(tries):
        rc, payload, out = _run_bili(args, timeout=timeout)
        if rc == 0 and payload and payload.get('ok'):
            return rc, payload, out
        if i < tries - 1:
            print('[ops] %s 第%d次失败,%ds 后重试: %s'
                  % (' '.join(args), i + 1, pause, out.strip()[:120]))
            time.sleep(pause)
    return rc, payload, out


def _num(v):
    """JSON 数值/None -> int 或 None(null 保持无数据,不做 0 假设)。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _load_audit_counts():
    """bili_audit.jsonl -> {date: {action: n}}。skip_* 归并为一类 skip,
    便于日报展示"被审核拦下"的量。"""
    per_day = {}
    try:
        with open(AUDIT, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                day = str(rec.get('ts') or '')[:10]
                act = str(rec.get('action') or '?')
                if act.startswith('skip_'):
                    act = 'skip'
                d = per_day.setdefault(day, {})
                d[act] = d.get(act, 0) + 1
    except OSError:
        pass
    return per_day


def _load_sent_days():
    """state.json bili_sent 记账 -> {date: n}(每日发布动态条数)。"""
    sent = _load_json(STATE, {}).get('bili_sent') or []
    per_day = {}
    for it in sent:
        day = str(it.get('d') or '')[:10]
        if day:
            per_day[day] = per_day.get(day, 0) + 1
    return per_day


def _collect_snapshot(today):
    """拉 me show/me stats/dynamic list 组装当日指标快照。
    me stats 偶发 curl 35 由 _bili_retry 兜;单项失败降级为 None,不炸整体。"""
    snap = {'date': today, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
            'name': '', 'sign': '', 'level': None,
            'fans': None, 'unfollowed_week': None,
            'plays_week': None, 'visitors_week': None, 'new_fans_week': None,
            'stats_data_date': '',
            'likes_total': None, 'comments_total': None, 'forwards_total': None,
            'dynamics_total': None, 'posted_today': []}

    rc, payload, out = _bili_retry(['me', 'show'])
    if payload and payload.get('ok'):
        d = payload.get('data') or {}
        snap['name'] = d.get('name') or ''
        snap['sign'] = d.get('sign') or ''
        snap['level'] = _num(d.get('level'))

    rc, payload, out = _bili_retry(['me', 'stats'])
    if payload and payload.get('ok'):
        st = payload.get('data') or {}
        snap['stats_data_date'] = str(st.get('data_date') or '')
        fans = st.get('fans') or {}
        snap['fans'] = _num(fans.get('total'))
        snap['unfollowed_week'] = _num(fans.get('unfollowed'))
        snap['plays_week'] = _num((st.get('plays') or {}).get('now'))
        snap['visitors_week'] = _num((st.get('visitors') or {}).get('now'))
        snap['new_fans_week'] = _num((st.get('new_fans') or {}).get('now'))

    rc, payload, out = _bili_retry(['dynamic', 'list'])
    items = (payload.get('data') or {}).get('items') if payload and payload.get('ok') else None
    if items:
        likes = comments = forwards = 0
        for it in items:
            stat = it.get('stat') or {}
            likes += _num(stat.get('like')) or 0
            comments += _num(stat.get('comment')) or 0
            forwards += _num(stat.get('forward')) or 0
            t = str(it.get('time') or '')
            if t[:10] == today:
                title = (it.get('text') or '').strip().split('\n')[0][:60]
                snap['posted_today'].append(
                    {'time': t, 'title': title, 'url': it.get('url') or ''})
        snap['likes_total'] = likes
        snap['comments_total'] = comments
        snap['forwards_total'] = forwards
        snap['dynamics_total'] = len(items)
    return snap


def _save_snapshot(snap):
    os.makedirs(METRICS_DIR, exist_ok=True)
    path = os.path.join(METRICS_DIR, '%s.json' % snap['date'])
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    return path


def _snap_date(s):
    return str(s.get('date') or '')


def _load_snapshots():
    """metrics/ 下全部快照按日期升序(坏文件跳过)。"""
    out = []
    try:
        names = sorted(f for f in os.listdir(METRICS_DIR) if f.endswith('.json'))
    except OSError:
        return out
    for fn in names:
        s = _load_json(os.path.join(METRICS_DIR, fn), None)
        if isinstance(s, dict) and _snap_date(s):
            out.append(s)
    out.sort(key=_snap_date)
    return out


def _prev_snapshot(today):
    """早于 today 的最近一份快照(无则 None)。"""
    prev = [s for s in _load_snapshots() if _snap_date(s) < today]
    return prev[-1] if prev else None


def _fmt_delta(cur, prev):
    """对比展示:null 不做 0 假设,如实标"无数据"。"""
    if cur is None:
        return '无数据'
    if prev is None:
        return '%s' % cur
    return '%d(较前日%+d)' % (cur, cur - prev)


def _trend_table(snaps):
    """近 7 日快照趋势表(粉丝/获赞/评论/动态/周播放)。"""
    lines = ['  日期        粉丝  获赞  评论  动态  周播放',
             '  ' + '-' * 42]
    for s in snaps[-7:]:
        lines.append('  %s  %4s  %4s  %4s  %4s  %5s' % (
            _snap_date(s),
            s.get('fans') if s.get('fans') is not None else '-',
            s.get('likes_total') if s.get('likes_total') is not None else '-',
            s.get('comments_total') if s.get('comments_total') is not None else '-',
            s.get('dynamics_total') if s.get('dynamics_total') is not None else '-',
            s.get('plays_week') if s.get('plays_week') is not None else '-'))
    return lines


def _action_table(audit_days, sent_days, days=7):
    """近 N 日动作量表:点赞/回复/评论出自审计日志,发动态=审计+发布记账。"""
    today = time.strftime('%Y-%m-%d')
    dates = sorted(set(audit_days) | set(sent_days))[-days:]
    lines = ['  日期         点赞  回复  评论  发动态  被审核拦下',
             '  ' + '-' * 46]
    for day in dates:
        a = audit_days.get(day, {})
        lines.append('  %s  %4d  %4d  %4d  %5d  %8d%s' % (
            day,
            a.get('like', 0), a.get('reply', 0), a.get('comment', 0),
            a.get('dynamic', 0) + sent_days.get(day, 0),
            a.get('skip', 0),
            '  (今日)' if day == today else ''))
    return lines


def _suggestions(snap, prev, audit_today, sent_today):
    """规则化"明日计划建议":只依据快照/审计事实,不编数据。"""
    tips = []
    posted = snap.get('posted_today') or []
    if not posted:
        tips.append('今日未发动态:radar 编译稿会自动进 bili_drafts,'
                    '可用 bili_publish.py preview/send 补发,保持日更节奏。')
    else:
        tips.append('保持日更节奏(今日已发 %d 条),发布稳定在下午至晚间流量高峰。'
                    % len(posted))
    if (snap.get('comments_total') or 0) > 0 and audit_today.get('reply', 0) == 0:
        tips.append('动态已有评论但今日未跑 inbox 回复:明日先处理评论回复,'
                    '互动响应速度影响起步期权重。')
    fans, prev_fans = snap.get('fans'), (prev or {}).get('fans')
    if fans is not None and prev_fans is not None and fans <= prev_fans:
        tips.append('粉丝零增长:加大站内互动(patrol 点赞/评论垂直领域内容),'
                    '让目标作者与观众回访主页。')
    if snap.get('plays_week') in (None, 0):
        tips.append('周播放数据尚未生成(B站创作中心周报滞后,新号起步期):'
                    '以内容稳定输出为主,周报出数后再看内容方向调整。')
    if audit_today.get('skip', 0) >= 3:
        tips.append('今日被审核拦下的回复/草稿较多(%d 条):明日检查起草质量,'
                    '必要时收紧 PERSONA。' % audit_today.get('skip', 0))
    if not tips:
        tips.append('数据正常,按现有节奏继续。')
    return tips


def _build_report(snap, prev, audit_days, sent_days):
    """组装可贴给用户看的运营日报文本。"""
    today = snap['date']
    L = []
    L.append('==== %s 运营日报(%s)====' % (snap.get('name') or 'NImraaaa', today))
    if snap.get('sign'):
        L.append('定位: %s%s' % (snap.get('sign'), ' | Lv.%s' % snap['level']
                                 if snap.get('level') is not None else ''))
    L.append('')

    posted = snap.get('posted_today') or []
    L.append('【今日发布】%d 条' % len(posted))
    if posted:
        for p in posted:
            hhmm = (p.get('time') or '')[11:16] or '??:??'
            L.append('  - %s %s -> %s' % (hhmm, p.get('title') or '(无标题)',
                                          p.get('url') or ''))
    else:
        L.append('  (今日未发动态)')
    L.append('')

    L.append('【账号数据】')
    L.append('  粉丝 %s | 获赞 %s | 评论 %s | 转发 %s | 动态 %s' % (
        _fmt_delta(snap.get('fans'), (prev or {}).get('fans')),
        _fmt_delta(snap.get('likes_total'), (prev or {}).get('likes_total')),
        _fmt_delta(snap.get('comments_total'), (prev or {}).get('comments_total')),
        _fmt_delta(snap.get('forwards_total'), (prev or {}).get('forwards_total')),
        _fmt_delta(snap.get('dynamics_total'), (prev or {}).get('dynamics_total'))))
    dd = snap.get('stats_data_date') or ''
    L.append('  周报(B站数据日期 %s):周播放 %s | 周访客 %s | 周新粉 %s | 周掉粉 %s' % (
        dd or '无',
        _fmt_delta(snap.get('plays_week'), (prev or {}).get('plays_week')),
        _fmt_delta(snap.get('visitors_week'), (prev or {}).get('visitors_week')),
        _fmt_delta(snap.get('new_fans_week'), (prev or {}).get('new_fans_week')),
        _fmt_delta(snap.get('unfollowed_week'), (prev or {}).get('unfollowed_week'))))
    L.append('')

    a_today = audit_days.get(today, {})
    L.append('【今日动作量】(来源: bili_audit.jsonl + bili_sent 记账)')
    L.append('  点赞 %d | 回复 %d | 评论 %d | 发动态 %d | 被审核拦下 %d' % (
        a_today.get('like', 0), a_today.get('reply', 0), a_today.get('comment', 0),
        a_today.get('dynamic', 0) + sent_days.get(today, 0),
        a_today.get('skip', 0)))
    L.append('')
    L.append('【近7日动作量】')
    L.extend(_action_table(audit_days, sent_days))
    L.append('  (发动态为日志记账口径;站点实发以【今日发布】为准)')
    L.append('')
    L.append('【指标趋势】(metrics/ 快照对比)')
    snaps = [s for s in _load_snapshots() if _snap_date(s) <= today]
    if len(snaps) >= 2:
        L.extend(_trend_table(snaps))
    else:
        L.append('  快照积累中(当前 %d 份):自明日起可看粉丝/获赞/评论逐日变化。'
                 % len(snaps))
    L.append('')
    L.append('【明日计划建议】')
    for i, tip in enumerate(_suggestions(snap, prev, a_today,
                                         sent_days.get(today, 0)), 1):
        L.append('  %d. %s' % (i, tip))
    return '\n'.join(L)


def cmd_report(argv):
    today = time.strftime('%Y-%m-%d')
    # ① 拉取三项基础数据,落当日指标快照(me stats 偶发 curl 35,重试兜底)
    snap = _collect_snapshot(today)
    path = _save_snapshot(snap)
    prev = _prev_snapshot(today)          # ② 与前一快照对比
    audit_days = _load_audit_counts()     # ③ 审计日志每日动作量
    sent_days = _load_sent_days()         # 发布记账(补 radar 发布侧无审计)
    print(_build_report(snap, prev, audit_days, sent_days))  # ④ 运营日报
    print()
    print('[ops] 快照已存: %s' % path)
    if '--raw' in argv:                   # 排障:附原始 JSON
        rc, payload, out = _bili_retry(['me', 'stats'])
        print('== me stats raw ==\n%s' % out.strip()[:1200])
        rc, payload, out = _bili_retry(['dynamic', 'list'])
        print('== dynamic list raw ==\n%s' % out.strip()[:1200])


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('inbox', 'patrol', 'report'):
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'report':
        cmd_report(sys.argv[2:])
    else:
        {'inbox': cmd_inbox, 'patrol': cmd_patrol}[cmd]()


if __name__ == '__main__':
    main()
