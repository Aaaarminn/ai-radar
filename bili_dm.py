# -*- coding: utf-8 -*-
"""B站私信(DM)回复通道 —— t13。

背景:bili-cli 117 条命令无私信接口,本文件用 bilibili-api-python(本机已装)的
session 模块补齐私信链路:拉会话 → 人设起草(复用 bili_ops.PERSONA/_SKIP_RE)→
guard_text 统一审核(复用 t9)→ 预算(私信每日≤3,独立于评论)→ 发送 → 审计。

安全边界(t13 合同):
  * 默认 dry-run:只拉取+起草+审核+审计(dry=True),不发送;--send 才真正发送。
  * B站官方系统号(uid 844424930131964 登录/安全通知等)永不回复 → skip_dm_system。
  * 诱导/钓鱼/促销诈骗嫌疑(链接/加微信/兼职返利等特征)一律跳过 → skip_dm_suspect。
  * 每条会话只回最新一条收到的消息,msg_key 记账防重复回复(bili_dm_state.json)。
  * 风控异常(ResponseCodeException 等)不重试,审计 ok=False 后终止本轮。

复跑:  py -3.12 bili_dm.py            (dry-run)
        py -3.12 bili_dm.py --send     (真实发送,预算内)
        py -3.12 bili_dm.py --selftest (模拟私信走通起草→审核→(dry)发送链路)
"""
import argparse
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from bili_ops import PERSONA, _SKIP_RE, _audit, guard_text  # 人设/跳过出口/审计/统一审核
from bili_publish import _claude_cli as _claude             # 同款 claude.exe 通道

from bilibili_api import Credential, session as bs          # noqa: E402  私信走旧库
from bilibili_api.session import EventType                  # noqa: E402
from bilibili_api.utils.sync import sync as _sync           # noqa: E402

CRED_PATH = os.path.join(os.environ.get('APPDATA', ''), 'bili-cli', 'credential.json')
STATE_PATH = os.path.join(BASE, 'bili_dm_state.json')
DM_DAILY_LIMIT = 3            # 私信每日预算,独立于评论/回复预算(t13 合同)
SEND_GAP_SECONDS = 8
# B站官方系统号,永不回复:…964=官方助手(登录/安全通知)、…966=创作助手(实测会话中出现)
SYSTEM_TALKER_UIDS = {844424930131964, 844424930131966}

# 陌生人私信可疑特征:链接/域名/联系方式/兼职返利/优惠券等(保守起见宁可错杀,
# 误杀代价=少回一条私信;放行钓鱼的代价远高于此)
_DM_SUSPECT_RE = re.compile(
    r'https?://|www\.|\.(?:com|cn|net|xyz|top|vip)\b|[a-zA-Z0-9]\.[a-zA-Z]{2,}\b'
    r'|加(?:我)?(?:微信|QQ|qq|Q)|私聊我|加群|群号|兼职|刷单|返利|代练|代刷|包过|接单'
    r'|低价|优惠|激活码|中奖|福利群|带赚|稳赚|内幕|名额|免费领|扫码|点击链接|领取'
    r'|合作推广|商务合作|互推|引流',
    re.IGNORECASE)

try:  # Windows GBK 控制台兜底,避免中文输出炸管道
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _load_cred():
    """%APPDATA%\\bili-cli\\credential.json -> (Credential, my_mid)。值永不打印。"""
    with open(CRED_PATH, encoding='utf-8') as f:
        c = json.load(f)
    cred = Credential(sessdata=c['sessdata'], bili_jct=c['bili_jct'],
                      dedeuserid=c['dedeuserid'], ac_time_value=c['ac_time_value'])
    return cred, int(c['dedeuserid'])   # dedeuserid 即本账号 mid(3747582327131132)


def _load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def _dm_sent_today():
    """今日已真实发送的私信数(审计 action=reply & channel=dm & ok=True)。dry 不占预算。"""
    path = os.path.join(BASE, 'bili_audit.jsonl')
    today = time.strftime('%Y-%m-%d')
    n = 0
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get('action') == 'reply' and r.get('channel') == 'dm'
                        and r.get('ok') is True and str(r.get('ts', '')).startswith(today)):
                    n += 1
    except FileNotFoundError:
        pass
    return n


def _suspect(text):
    return bool(_DM_SUSPECT_RE.search(text or ''))


def _parse_content(raw):
    """消息 content 字段是 JSON 字符串 {"content": "..."};解析失败按原文返回。"""
    if not isinstance(raw, str):
        return ''
    try:
        inner = json.loads(raw)
        if isinstance(inner, dict) and isinstance(inner.get('content'), str):
            return inner['content']
    except Exception:
        pass
    return raw


def draft_dm(sender_name, text):
    """私信人设起草(与 _draft_reply 同款规则,仅上下文措辞改为"私信")。
    返回 (draft, skip_reason, hit),skip_reason ∈ draft_fail/no_value/too_long/guard。"""
    prompt = ('%s\n\n网友「%s」通过B站私信给你发来消息:%s\n\n'
              '直接输出回复正文本身,不要解释、不要引号;'
              '若按人设规则应跳过,只输出:[跳过]' % (PERSONA, sender_name, text))
    draft = _claude(prompt)
    if not draft:
        return None, 'draft_fail', (text or '')[:80]
    draft = draft.strip().strip('\"“”')
    if _SKIP_RE.fullmatch(draft):                 # 无实质可回应 → 宁缺毋滥
        return None, 'no_value', (text or '')[:80]
    if len(draft) > 150:
        draft = _claude(prompt + '\n(严格压缩到80字内,保留具体事实)')
        draft = (draft or '').strip().strip('\"“”')
    if not draft or len(draft) > 160:
        return None, 'too_long', ''
    ok, code, hit = guard_text(draft, 'reply')    # t9 统一审核,与评论线同一标准
    if not ok:
        return None, 'guard', hit or code
    return draft, '', ''


def _process_one(sess, msg, cred, send):
    """处理一个会话的最新一条来讯:红线→起草→审核→(dry)发送→审计。
    返回 'replied'/'dry'/'skip:<why>'/'none'。"""
    talker = sess.get('talker_id')
    if not talker or sess.get('session_type') != 1:      # 只处理单聊
        return 'none'
    if talker in SYSTEM_TALKER_UIDS or talker == sess.get('_my_mid'):
        _audit('skip_dm_system', {'talker_id': talker})
        return 'skip:system'
    content = _parse_content(msg.get('content'))
    key = str(msg.get('msg_key') or msg.get('msg_seqno') or msg.get('timestamp') or '')
    state = _load_state()
    if state.get(str(talker)) == key:                    # 已回过这条,防重复
        return 'none'
    if _suspect(content):                                # 诱导/钓鱼/促销诈骗嫌疑
        _audit('skip_dm_suspect', {'talker_id': talker, 'preview': content[:60]})
        state[str(talker)] = key
        _save_state(state)
        return 'skip:suspect'
    sender_name = ((sess.get('account_info') or {}).get('name')) or str(talker)
    draft, why, hit = draft_dm(sender_name, content)
    if draft is None:
        _audit('skip_dm_' + why, {'talker_id': talker, 'hit': hit})
        state[str(talker)] = key
        _save_state(state)
        return 'skip:' + why
    ok = None
    err = None
    if send:
        try:
            r = _sync(bs.send_msg(cred, talker, EventType.TEXT, draft))
            ok = isinstance(r, dict) and int(r.get('code', -1)) == 0
            err = None if ok else json.dumps(r, ensure_ascii=False)[:200]
        except Exception as e:                           # 风控/网络异常:不重试
            ok = False
            err = str(e)[:200]
    _audit('reply', {'channel': 'dm', 'dry': not send, 'to_mid': talker,
                     'to_name': sender_name, 'draft': draft, 'ok': ok, 'err': err})
    state[str(talker)] = key
    _save_state(state)
    return 'dry' if not send else ('replied' if ok else 'reply_failed')


def cmd_dm(send=False, limit=3):
    """拉会话列表,逐个处理最新来讯。预算:今日真实发送 < DM_DAILY_LIMIT。"""
    cred, my_mid = _load_cred()
    used = _dm_sent_today()
    remain = DM_DAILY_LIMIT - used
    print('[dm] 今日私信已发 %d/%d,剩余 %d(%s模式)'
          % (used, DM_DAILY_LIMIT, remain, '发送' if send else 'dry'))
    if remain <= 0:
        print('[dm] 私信预算用尽,本轮不处理。')
        return
    data = _sync(bs.get_sessions(cred, session_type=4))
    sessions = data.get('session_list') or []
    print('[dm] 会话 %d 个。' % len(sessions))
    acted = 0
    for s in sessions:
        if acted >= min(limit, remain):
            break
        if s.get('session_type') != 1:
            continue
        if s.get('talker_id') in SYSTEM_TALKER_UIDS:
            continue                                     # 官方系统号:静默跳过,不写审计(避免日报 skip 列虚高)
        s['_my_mid'] = my_mid
        try:
            msgs = _sync(bs.fetch_session_msgs(s['talker_id'], cred, session_type=1))
        except Exception as e:
            _audit('skip_dm_fetch_fail', {'talker_id': s.get('talker_id'), 'err': str(e)[:120]})
            continue
        ml = msgs.get('messages') or []
        if not ml:
            continue
        latest = max(ml, key=lambda m: m.get('timestamp') or 0)
        if latest.get('sender_uid') == my_mid:           # 最后一条是自己发的,无需回
            continue
        try:
            outcome = _process_one(s, latest, cred, send)
        except Exception as e:
            outcome = 'error:' + str(e)[:100]
            _audit('skip_dm_error', {'talker_id': s.get('talker_id'), 'err': str(e)[:120]})
        print('[dm] %s(%s) -> %s' % (s.get('talker_id'),
                                     (s.get('account_info') or {}).get('name'), outcome))
        if outcome in ('replied', 'dry'):
            acted += 1
            if send:
                time.sleep(SEND_GAP_SECONDS)             # 发送间隔,降风控风险
    print('[dm] 本轮处理 %d 条。' % acted)


def selftest():
    """模拟私信走通 红线→起草→审核→(dry)发送 链路(不碰网络发送,合同要求)。"""
    cred, my_mid = _load_cred()
    mocks = [
        ('正常咨询', 111111, {'session_type': 1, 'talker_id': 111111,
                              'account_info': {'name': '好奇的同学'}},
         {'msg_key': 9001, 'sender_uid': 111111, 'timestamp': 1,
          'content': json.dumps({'content': '你好,想转载你昨天的AI日报到我们的学习群,'
                                           '会完整标注出处和作者,可以吗?'} , ensure_ascii=False)}),
        ('钓鱼链接', 222222, {'session_type': 1, 'talker_id': 222222,
                              'account_info': {'name': '皮肤中介'}},
         {'msg_key': 9002, 'sender_uid': 222222, 'timestamp': 2,
          'content': json.dumps({'content': '兄弟送你限定皮肤,点这个链接免费领取 '
                                            'https://bili-skin-free.xyz/top 快来'}, ensure_ascii=False)}),
        ('促销拉客', 333333, {'session_type': 1, 'talker_id': 333333,
                              'account_info': {'name': '课程推广小王'}},
         {'msg_key': 9003, 'sender_uid': 333333, 'timestamp': 3,
          'content': json.dumps({'content': '你好,我们是做AI课程推广的,想跟你合作推广,'
                                            '佣金丰厚,加我微信详聊'}, ensure_ascii=False)}),
        ('官方系统', 844424930131964, {'session_type': 1, 'talker_id': 844424930131964,
                                       'account_info': {'name': '哔哩哔哩官方助手'}},
         {'msg_key': 9004, 'sender_uid': 844424930131964, 'timestamp': 4,
          'content': json.dumps({'content': '您的账号于新设备登录成功…'}, ensure_ascii=False)}),
        ('无实质内容', 444444, {'session_type': 1, 'talker_id': 444444,
                                'account_info': {'name': '路人'}},
         {'msg_key': 9005, 'sender_uid': 444444, 'timestamp': 5,
          'content': json.dumps({'content': '在吗?'}, ensure_ascii=False)}),
    ]
    print('=== 私信链路自测(模拟,不发送) ===')
    for name, mid, sess, msg in mocks:
        sess['_my_mid'] = my_mid
        # 先清 state,保证 mock 可重复
        st = _load_state()
        st.pop(str(mid), None)
        _save_state(st)
        outcome = _process_one(sess, msg, cred, send=False)
        print('[%s] uid=%s -> %s' % (name, mid, outcome))
    print('=== 审计尾部 ===')
    path = os.path.join(BASE, 'bili_audit.jsonl')
    with open(path, encoding='utf-8') as f:
        for line in f.readlines()[-8:]:
            r = json.loads(line)
            if str(r.get('action', '')).startswith(('reply', 'skip_dm')):
                print(json.dumps(r, ensure_ascii=False)[:220])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='B站私信回复通道(默认 dry-run)')
    ap.add_argument('--send', action='store_true', help='真正发送(默认只起草+审计 dry)')
    ap.add_argument('--limit', type=int, default=3, help='本轮最多处理的私信数')
    ap.add_argument('--selftest', action='store_true', help='模拟私信走通全链路(不发送)')
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        cmd_dm(send=a.send, limit=max(1, min(a.limit, DM_DAILY_LIMIT)))
