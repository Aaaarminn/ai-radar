# -*- coding: utf-8 -*-
"""B站动态发布工具(运行于 Python 3.12,通过 bili-cli / bili.exe 发布)

依赖: pip install bili-agent  (命令 bili;凭据由 bili-cli 自动管理与续期,
      存于 %APPDATA%/bili-cli,本脚本不再经手 cookie)

用法:
  py -3.12 bili_publish.py login        扫码登录(调 bili auth login,弹二维码等待扫码)
  py -3.12 bili_publish.py preview N    预览第 N 条候选动态全文(不发布)
  py -3.12 bili_publish.py send N       发布第 N 条候选动态到 B 站

候选存于 state.json 的 bili_drafts 列表(由 radar.py 发邮件后写入)。
发布成功后本脚本只打印结果(exit 0),不动 state.json —— 草稿移除与
bili_sent 记账由 radar.py 的 bili_check_replies 统一完成(避免双写冲突)。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

# 被管道调用时 Python 默认用 GBK 编码 stdout(中文/emoji 可能炸),
# 统一强制 UTF-8,方便 radar.py 侧捕获解析。
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, 'state.json')

# 长文本动态安全上限:B站动态正文极长会被截断,6000 字内稳妥
MAX_DYN_CHARS = 6000

# 本机 NO_PROXY 含裸 IPv6 字面量(::1/[::1])会引爆 httpx 的代理解析
# (InvalidURL: Invalid port ':1]'),调 bili.exe 一律传干净环境。
_BILI_EXE_CANDIDATES = [
    os.path.join(os.environ.get('LOCALAPPDATA', ''),
                 r'Programs\Python\Python312\Scripts\bili.exe'),
    os.path.join(os.environ.get('APPDATA', ''), r'..\Local\Programs\Python\Python312\Scripts\bili.exe'),
]


def _bili_env():
    # 1) 本机 NO_PROXY 含裸 IPv6 字面量(::1/[::1])会引爆 httpx 的代理解析
    #    (InvalidURL: Invalid port ':1]');
    # 2) 走系统代理(127.0.0.1:7897)发动态会被 B 站重置(curl 56 Connection
    #    reset,疑似代理把 api.vc.bilibili.com 分流到海外出口)。B 站国内
    #    直连即可:把代理变量全部剥掉。
    drop = {'NO_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
            'all_proxy', 'ALL_PROXY'}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env['NO_PROXY'] = 'localhost,127.0.0.1'
    return env


def _find_bili():
    for p in _BILI_EXE_CANDIDATES:
        if p and os.path.exists(p):
            return p
    import shutil
    return shutil.which('bili') or 'bili'


def _run_bili(args, timeout=180):
    r = subprocess.run([_find_bili()] + args, capture_output=True,
                       env=_bili_env(), timeout=timeout)
    out = (r.stdout or b'').decode('utf-8', 'replace')
    try:
        payload = json.loads(out)
    except Exception:
        payload = None
    return r.returncode, payload, out


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


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


def _get_draft(n):
    state = _load_json(STATE, {})
    drafts = [d for d in (state.get('bili_drafts') or []) if d.get('n') == n]
    if not drafts:
        print('候选 %s 不存在(现有: %s)' % (
            n, [d.get('n') for d in (state.get('bili_drafts') or [])]))
        sys.exit(1)
    return drafts[0]


def cmd_login():
    rc, payload, out = _run_bili(['auth', 'login'], timeout=30)
    if not (payload and payload.get('ok')):
        print('bili auth login 失败: %s' % out.strip()[:300])
        sys.exit(2)
    data = payload.get('data') or {}
    qr_image = data.get('qr_image') or ''
    print('二维码: %s (%d 秒内扫描)' % (qr_image, data.get('expires_in') or 180))
    if qr_image and os.path.exists(qr_image):
        os.startfile(qr_image)          # 弹出图片查看器
    deadline = time.time() + (data.get('expires_in') or 180)
    while time.time() < deadline:
        time.sleep(5)
        rc, st, out = _run_bili(['auth', 'status'], timeout=30)
        if st and (st.get('data') or {}).get('logged_in'):
            u = (st.get('data') or {}).get('user') or {}
            print('登录成功: %s (mid=%s)' % (u.get('name'), u.get('mid')))
            return
        print('等待扫码/确认……')
    print('等待扫码超时,请重新运行 login')
    sys.exit(2)


def cmd_preview_send(n, do_send):
    d = _get_draft(n)
    cfg = _load_json(os.path.join(BASE, 'config.json'), {})
    tags = cfg.get('bili_tags', '#AI日报# #人工智能#')
    text = _build_text(d, tags)
    print('---- 动态全文(共 %d 字) ----' % len(text))
    print(text)
    print('----')
    if not do_send:
        return

    tmp = os.path.join(tempfile.gettempdir(), 'bili_dyn_%d.txt' % int(time.time()))
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    try:
        rc, payload, out = _run_bili(['dynamic', 'post', '--file', tmp], timeout=180)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if payload and payload.get('ok'):
        data = payload.get('data') or {}
        dyn_id = ''
        for k in ('id_str', 'dynamic_id', 'dyn_id', 'id'):
            v = data.get(k)
            if v:
                dyn_id = str(v)
                break
        print('发布成功: %s' % json.dumps(data, ensure_ascii=False)[:200])
        if dyn_id:
            print('dyn_url=https://t.bilibili.com/%s' % dyn_id)
        return
    print('发布失败: %s' % out.strip()[:300])
    sys.exit(3)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'login':
        cmd_login()
    elif cmd in ('preview', 'send') and len(sys.argv) > 2:
        cmd_preview_send(int(sys.argv[2]), do_send=(cmd == 'send'))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
