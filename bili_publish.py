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
import re
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

# 长文本动态安全上限:B站纯文本动态实测上限约 1000 字符(4126115 文字太长),
# 留出标题/链接余量取 950,超出尾部截断并附说明。
MAX_DYN_CHARS = 950

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


def _claude_cli(prompt, timeout=120):
    """调本机 claude CLI(公司网关只放行 CLI);失败返回 ''。

    prompt 必须走 stdin(-p 不带值时 claude 从 stdin 读)。
    必须直调 claude.exe:经 claude.ps1 时其 "$input |" 管道在 PS 5.1 下
    按 ANSI 代码页重编码 stdin,中文全变 '?',模型只收到问号(2026-11-12
    端到端复现);直调 exe 字节直通,中文完好且快约 4 倍。
    stderr 只打日志不进返回值:[claude-code:unrecognized_model] 是网关
    警告,此前拼入正文会跟着压缩稿进动态,被 _quality_guard 拦 exit 4。"""
    try:
        r = subprocess.run(
            [r'D:\dev\npm-global\node_modules\@anthropic-ai\claude-code\bin\claude.exe', '-p'],
            input=prompt.encode('utf-8'), capture_output=True, timeout=timeout)
        if r.stderr:
            dbg = r.stderr.decode('utf-8', 'replace').strip()
            if dbg:
                print('[claude-cli] stderr: %s' % dbg[:200])
        return (r.stdout or b'').decode('utf-8', 'replace').strip()
    except Exception as e:
        print('[shrink] claude CLI 调用失败: %s' % e)
        return ''


_SHRINK_BAD_RE = re.compile(
    r'API Error[:：]|不安全或敏感内容|\uFFFD|[\uE000-\uF8FF]|[\x00-\x08\x0b\x0c\x0e-\x1f]'
    r'|为您|帮你|作为一个AI|作为一名AI|仅供参考|希望这(?:篇|条|份|能)|祝你'
    r'|(?:^|\n)\s*(?:以下是|好的[,，!！.。]|当然[,，!！])'
    # LLM 元对话(如"编译稿没有传完整——我这边只收到了开头一句"):新闻正文绝不该出现
    r'|编译稿|传(?:得)?完整|只收到|我这边|补写|编造|没法凭|无法凭|请提供|请把|贴给我'
    r'|两个建议|建议(?:如下|一|二)|未收到|收到(?:的|了)?(?:开头|半截|部分)'
    r'|我(?:无法|没法|不能)(?:凭|确认|完成)')

# 提示词(2026-11-12 t8 调优):把"守字数/完整收尾"列为硬性要求并解释后果,
# 引导 LLM 用删段落而不是压句子来守预算,保住数字/人名/机构/时间线等信息密度。
_SHRINK_PROMPT = (
    '你是新闻编辑。把下面的 AI 新闻中文编译稿压缩成一篇不超过 %d 字的完整新闻摘要。\n'
    '硬性要求:\n'
    '1. 全文不超过 %d 字——这是发布平台的硬上限,超出会被机器从中间截断;'
    '请通过删掉整段次要内容来守预算,不要把每句都压成半句。\n'
    '2. 保留全部关键事实:具体数字(百分比/金额/参数/排名)、人名、公司/机构名、'
    '时间线、结论与因果链,一个都不能丢。\n'
    '3. 只删次要细节、重复表述与空话过渡;删完仍须是连贯可读的新闻正文,不是要点罗列。\n'
    '4. 必须以完整句子自然收尾:最后一句语意必须完整,严禁半句截断,'
    '禁止以省略号/逗号/冒号结尾。\n'
    '5. 输出纯正文:不要标题、前言、解释、编号列表或引号。\n\n%s')


def _ends_badly(t):
    """压缩稿烂尾检测:以省略号收尾,或停在逗号/冒号/左括号等半句位置。"""
    t = t.rstrip()
    if not t:
        return True
    if t.endswith('……') or t.endswith('...'):
        return True
    return t[-1] in '，,、：:；(（“"'


def _shrink_ok(out, limit):
    """压缩稿合格判定:无异常文本、不超预算、完整收尾、中文占比达标。"""
    if not out or _SHRINK_BAD_RE.search(out):
        return False
    t = out.strip()
    if len(t) > limit or _ends_badly(t):
        return False
    cjk = len(re.findall(r'[\u4e00-\u9fff]', t))
    nonws = len(re.sub(r'\s', '', t))
    return cjk >= 200 and (not nonws or cjk / nonws >= 0.5)


def _shrink_body(body, limit):
    """LLM 把编译稿压缩到 limit 字内的新闻摘要(保全部关键事实、完整收尾)。

    首稿超预算或烂尾(半句/省略号)时,带重申提示重试一次;仍不合格返回 ''
    (调用方走句子边界裁剪兜底)。"""
    prompt = _SHRINK_PROMPT % (limit, limit, body)
    out = _claude_cli(prompt)
    if _shrink_ok(out, limit):
        return out.strip()
    if out:
        print('[shrink] 首稿不合格(len=%d, %s),重申约束重试' % (
            len(out), '超预算' if len(out) > limit else '烂尾/异常'))
    retry = _claude_cli(prompt + '\n\n(重申:全文不超过 %d 字,'
                        '必须以完整句子收尾,禁止以省略号或逗号结尾。)' % limit)
    if _shrink_ok(retry, limit):
        return retry.strip()
    return ''


def _trim_to_sentence(text, budget):
    """把 text 裁进 budget,裁点选预算内最后一个完整句末标点(。！？!?\n)。

    取代旧的 body[:keep]+'……' 字符截断——那是首条动态尾部出现
    『有没有真实生产客户』目…… 半句的根因。优先句末标点;预算内没有
    句末标点时退而求其次取逗号/分号边界(≥预算一半);仍没有才硬截,
    且硬截点回退到最近的非字母数字边界,避免把英文单词/URL 撕成碎片。"""
    if len(text) <= budget:
        return text
    cut = text[:budget]
    end = None
    for m in re.finditer(r'[。！？!?\n]', cut):
        end = m
    if end:                              # 完整句子优先:短而完整好过长而残缺
        return cut[:end.end()].rstrip()
    m2 = None                            # 次选:逗号/分号边界(至少保留一半预算)
    for mm in re.finditer(r'[，,、;；]', cut):
        if mm.end() >= budget // 2:
            m2 = mm
            break
    if m2:
        return cut[:m2.start()].rstrip(' ,，、;；:：') + '……'
    m = None                             # 硬截兜底:回退到最近非字母数字边界
    for mm in re.finditer(r'[A-Za-z0-9_\-./]+[A-Za-z0-9_\-./]', cut):
        m = mm
    if m and m.start() >= 30:
        cut = cut[:m.start()]
    return cut.rstrip(' ,，、;；:：') + '……'


def _assemble(title, body, link, tags):
    parts = [title, '', body, '', '原文: ' + link]
    if tags:
        parts += ['', tags]
    return '\n'.join(parts)


def _build_text(d, tags):
    """候选条目 -> 动态全文(标题 + 编译稿 + 原文链接;tags 为空则不拼标签)。

    超长策略:正文先交给 LLM 压缩到一条动态装得下的量(用户要求"缩减到一次
    动态能够包含的量");压缩失败才兜底截断,且只砍正文、链接行永远保留
    (此前整串截断曾把"原文: 链接"砍掉,动态出现"见链接"却无链接)。"""
    title = (d.get('title_cn') or d.get('title') or '(无标题)').strip()
    body = (d.get('full_text') or '').strip()
    if not body:
        body = (d.get('summary') or '').strip()
    if not body:
        body = '(编译稿缺失)'
    link = (d.get('link') or '').strip()

    text = _assemble(title, body, link, tags)
    if len(text) <= MAX_DYN_CHARS:
        return text

    # 超长:给正文算预算(动态总长 - 标题 - 链接 - 结构字符,再留 10% 余量)
    overhead = len(title) + len(link) + len('\n\n原文: \n') + (len(tags) + 2 if tags else 0)
    budget = max(300, int((MAX_DYN_CHARS - overhead) * 0.95))
    shrunk = _shrink_body(body, budget)
    if shrunk and len(shrunk) > budget:
        # LLM 仍超预算(重试后):对压缩稿二次压缩;再不行句子边界裁剪,绝不硬截半句
        tighter = _shrink_body(shrunk, int(budget * 0.85))
        if tighter:
            shrunk = tighter if len(tighter) <= budget else _trim_to_sentence(tighter, budget)
        else:
            shrunk = _trim_to_sentence(shrunk, budget)
    if shrunk:
        text = _assemble(title, shrunk, link, tags)
    if len(text) > MAX_DYN_CHARS:            # 压缩失败或仍超长:句子边界裁剪正文,永保链接
        keep = max(100, MAX_DYN_CHARS - overhead - 20)
        body = _trim_to_sentence(shrunk or body, keep)
        text = _assemble(title, body, link, tags)
    return text


def _quality_guard(text):
    """发布前最后一道审核(与 radar.py _dyn_publishable 同规则的独立实现,双保险):
    拦乱码(替换符/PUA/控制字符)、错误/审查文本、助手腔,以及中文占比<60% 或
    正文<200 字(英文正文未译/内容单薄)。不通过 exit 4。"""
    bad = re.compile(
        r'API Error[:：]|不安全或敏感内容'
        r'|\uFFFD|[\uE000-\uF8FF]'
        r'|[\x00-\x08\x0b\x0c\x0e-\x1f]'
        r'|为您|帮你|作为一个AI|作为一名AI|仅供参考|希望这(?:篇|条|份|能)|祝你'
        r'|(?:^|\n)\s*(?:以下是|好的[,，!！.。]|当然[,，!！])'
        # LLM 元对话/工具自述("编译稿没有传完整""我这边只收到…"):新闻正文绝不该出现
        r'|编译稿|传(?:得)?完整|只收到|我这边|补写|编造|没法凭|无法凭|请提供|请把|贴给我'
        r'|两个建议|建议(?:如下|一|二)|未收到|收到(?:的|了)?(?:开头|半截|部分)'
        r'|我(?:无法|没法|不能)(?:凭|确认|完成)'
        r'|claude-code:|\[claude-code')
    if bad.search(text):
        print('质量审核未通过: 检测到乱码/错误文本/助手腔')
        sys.exit(4)
    body = text.split('\n原文:')[0]           # 去掉尾部链接/标签,只审正文
    cjk = len(re.findall(r'[\u4e00-\u9fff]', body))
    nonws = len(re.sub(r'\s', '', body))
    if cjk < 200 or (nonws and cjk / nonws < 0.5):   # 同上:0.5 阈值容忍技术术语,仍拦英文未译
        print('质量审核未通过: 正文中文占比/长度不足(cjk=%d nonws=%d)' % (cjk, nonws))
        sys.exit(4)


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
    tags = cfg.get('bili_tags', '#AI快讯# #科技新闻# #人工智能#')
    text = _build_text(d, tags)
    print('---- 动态全文(共 %d 字) ----' % len(text))
    print(text)
    print('----')
    if not do_send:
        return

    _quality_guard(text)                      # 发布前最后审核:乱码/助手腔/中文占比,不过 exit 4

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
