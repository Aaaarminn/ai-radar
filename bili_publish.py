# -*- coding: utf-8 -*-
"""B站动态发布工具(运行于 Python 3.12 + bilibili-api-python,与 radar.py 的 3.7 零依赖环境隔离)

用法:
  py -3.12 bili_publish.py login        扫码登录,凭据写回 secrets.local.json
  py -3.12 bili_publish.py preview N    预览第 N 条候选动态全文(不发布)
  py -3.12 bili_publish.py send N       发布第 N 条候选动态到 B 站

候选存于 state.json 的 bili_drafts 列表(由 radar.py 在发邮件后写入),
发布成功后该条从 drafts 移除并记入 state.json 的 bili_sent 日志。
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(BASE, 'secrets.local.json')
STATE = os.path.join(BASE, 'state.json')

# ---- 长文本动态安全上限:B站动态正文极长会被截断,6000 字内稳妥 ----
MAX_DYN_CHARS = 6000


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _build_text(d, tags):
    """候选条目 -> 动态全文(标题 + 全量编译稿 + 原文链接 + 标签)"""
    title = d.get('title_cn') or d.get('title') or '(无标题)'
    body = (d.get('full_text') or '').strip()
    if not body:
        body = (d.get('summary') or '').strip()
    if not body:
        body = '(编译稿缺失)'
    parts = ['【AI雷达】' + title, '', body, '', '原文: ' + (d.get('link') or '')]
    if tags:
        parts += ['', tags]
    text = '\n'.join(parts)
    if len(text) > MAX_DYN_CHARS:
        text = text[:MAX_DYN_CHARS - 3] + '…\n(以上内容因长度限制截断,原文见链接)'
    return text


async def _login():
    from bilibili_api import login_v2

    qrl = login_v2.QrCodeLogin(login_v2.QrCodeLoginChannel.WEB)
    await qrl.generate_qrcode()
    png_src = os.path.join(tempfile.gettempdir(), 'qrcode.png')
    png_dst = os.path.join(BASE, 'bili_qrcode.png')
    if os.path.exists(png_src):
        shutil.copyfile(png_src, png_dst)
    print('二维码已生成: %s' % png_dst)
    print('请用 B 站 APP 扫码登录(180 秒内)……')

    deadline = 180
    while deadline > 0:
        ev = await qrl.check_state()
        if ev == login_v2.QrCodeLoginEvents.DONE:
            break
        if ev == login_v2.QrCodeLoginEvents.TIMEOUT:
            print('二维码已过期,请重新运行 login')
            sys.exit(2)
        if ev == login_v2.QrCodeLoginEvents.SCAN:
            print('已扫码,请在手机上确认……')
        elif ev == login_v2.QrCodeLoginEvents.CONF:
            print('已确认,正在获取凭据……')
        await asyncio.sleep(2)
        deadline -= 2
    if deadline <= 0 and not qrl.has_done():
        print('等待扫码超时')
        sys.exit(2)

    cred = qrl.get_credential()
    sec = _load_json(SECRETS, {})
    sec['bili'] = {
        'sessdata': cred.sessdata or '',
        'bili_jct': cred.bili_jct or '',
        'buvid3': cred.buvid3 or '',
        'dedeuserid': str(cred.dedeuserid or ''),
    }
    _save_json(SECRETS, sec)
    print('登录成功,凭据已写入 secrets.local.json (bili)')


async def _send(n, do_send):
    from bilibili_api import Credential, dynamic

    state = _load_json(STATE, {})
    drafts = [d for d in (state.get('bili_drafts') or []) if d.get('n') == n]
    if not drafts:
        print('候选 %s 不存在(现有: %s)' % (
            n, [d.get('n') for d in (state.get('bili_drafts') or [])]))
        sys.exit(1)
    d = drafts[0]
    cfg = _load_json(os.path.join(BASE, 'config.json'), {})
    tags = cfg.get('bili_tags', '#AI日报# #人工智能#')
    text = _build_text(d, tags)
    print('---- 动态全文(共 %d 字) ----' % len(text))
    print(text)
    print('----')
    if not do_send:
        return

    bili = (_load_json(SECRETS, {}).get('bili') or {})
    if not bili.get('sessdata') or not bili.get('bili_jct'):
        print('缺少 B 站凭据,请先运行: py -3.12 bili_publish.py login')
        sys.exit(1)
    cred = Credential(sessdata=bili['sessdata'], bili_jct=bili['bili_jct'],
                      buvid3=bili.get('buvid3'), dedeuserid=bili.get('dedeuserid'))
    bd = dynamic.BuildDynamic.empty().add_plain_text(text)
    res = await dynamic.send_dynamic(bd, cred)
    code = (res or {}).get('code')
    if code == 0:
        print('发布成功: %s' % json.dumps(res, ensure_ascii=False)[:200])
        # 记日志 + 移除草稿
        state = _load_json(STATE, {})
        state['bili_sent'] = (state.get('bili_sent') or [])[-200:] + [{
            'ts': __import__('time').strftime('%Y-%m-%d %H:%M'),
            'n': n, 'title': d.get('title_cn') or d.get('title'),
            'link': d.get('link'), 'res': 'ok'}]
        state['bili_drafts'] = [x for x in (state.get('bili_drafts') or []) if x.get('n') != n]
        _save_json(STATE, state)
    else:
        print('发布失败 code=%s: %s' % (code, json.dumps(res, ensure_ascii=False)[:300]))
        sys.exit(3)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'login':
        asyncio.run(_login())
    elif cmd in ('preview', 'send'):
        n = int(sys.argv[2])
        asyncio.run(_send(n, do_send=(cmd == 'send')))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
