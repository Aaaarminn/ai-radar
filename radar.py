#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-Radar —— AI 大厂研发动态雷达（零依赖，仅 Python 标准库）

数据源：官方博客（经 RSSHub 桥接，多实例容错）+ GitHub Releases Atom
        + Hacker News / Reddit 社区雷达 + 中文科技媒体 RSS
推送：PushPlus -> 微信
运行：GitHub Actions 每 30 分钟（见 .github/workflows/radar.yml），或本地 python radar.py

用法：
  python radar.py            # 常规扫描：抓新 -> 推微信 -> 写回 state.json
  python radar.py --dry-run  # 只抓取打印，不推送不写状态
  python radar.py --test     # 发一条测试消息验证微信通道
  python radar.py --init     # 重建基线：记录当前全部条目但不推送
"""
import argparse
import datetime
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:  # Windows 控制台中文输出兜底
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:  # noqa: BLE001
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE, 'sources.json')
STATE_PATH = os.path.join(BASE, 'state.json')

# RSSHub 公共实例（{rsshub} 占位符按序尝试）
RSSHUB_INSTANCES = [
    'https://rsshub.app',
    'https://rsshub.rssforever.com',
    'https://hub.slarker.me',
    'https://rsshub.pseudoyu.com',
    'https://rsshub.ktachibana.party',
    'https://rss.owo.nz',
    'https://rsshub.moeyy.xyz',
]
UA = 'Mozilla/5.0 (compatible; ai-radar/1.0)'
MAX_STATE = 800        # state 里最多保留的已见条目


# ---------------------------------------------------------------- 用户配置（config.json 可覆盖，SUMMARY_MODE 环境变量优先）
def _load_config():
    p = os.path.join(BASE, 'config.json')
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {}


CFG = _load_config()
MIN_SCORE = int(CFG.get('min_score', 4))            # 入选门槛
HIGH_SCORE = int(CFG.get('high_score', 7))          # 重大新闻：跳过攒批与冷却立即发
BATCH_MIN_ITEMS = int(CFG.get('batch_min_items', 6))  # 攒够 N 条发一封
BATCH_MAX_AGE = int(CFG.get('batch_max_age_minutes', 300))  # 或最早一条已等 N 分钟
MAX_PER_EMAIL = int(CFG.get('max_per_email', 12))   # 单封邮件（聚类后）最多条数
SUMMARY_LIMIT = int(CFG.get('summary_limit', 8))    # 单封最多摘要条数
SEND_COOLDOWN = int(CFG.get('send_cooldown_minutes', 90))   # 两封邮件最小间隔（重大新闻豁免）
OLD_CUTOFF_DAYS = int(CFG.get('old_cutoff_days', 7))


# ---------------------------------------------------------------- 抓取与解析
try:
    import certifi
    _SSL_CTX = urllib.request.ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    _SSL_CTX = None


def _https_handler():
    if _SSL_CTX is not None:
        return urllib.request.HTTPSHandler(context=_SSL_CTX)
    return urllib.request.HTTPSHandler()


def fetch(url, timeout=25):
    """直连优先，失败自动走本机代理（clash）重试一次"""
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        opener = urllib.request.build_opener(_https_handler())
        with opener.open(req, timeout=timeout) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        proxy = os.environ.get('RADAR_PROXY', 'http://127.0.0.1:7897')
        if not proxy:
            raise
        opener = urllib.request.build_opener(
            _https_handler(),
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        with opener.open(req, timeout=timeout) as r:
            return r.read()


def resolve(url):
    """{rsshub} 占位符 -> 依次尝试实例，返回第一个成功的响应"""
    if '{rsshub}' not in url:
        return fetch(url)
    last = None
    for inst in RSSHUB_INSTANCES:
        try:
            return fetch(url.replace('{rsshub}', inst))
        except Exception as e:  # noqa: BLE001
            last = e
    raise last if last else RuntimeError('no rsshub instance')


def _tag(el):
    return el.tag.rsplit('}', 1)[-1] if isinstance(el.tag, str) else ''


def parse_feed(xml_bytes):
    """RSS/Atom 解析（带容错：去控制字符、修复裸 & 符号）"""
    data = xml_bytes
    for attempt in range(3):
        try:
            return _parse_root(ET.fromstring(data))
        except ET.ParseError:
            if attempt == 0:
                data = re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]', b'', data)
            elif attempt == 1:
                data = re.sub(rb'&(?!#?[a-zA-Z0-9]{1,8};)', b'&amp;', data)
            else:
                raise


def _parse_root(root):
    items = []
    for el in root.iter():
        t = _tag(el)
        if t == 'item':                      # RSS 2.0 / RDF
            title = (el.findtext('title') or '').strip()
            link = (el.findtext('link') or '').strip()
            date = (el.findtext('pubDate') or el.findtext('date') or '').strip()
            desc = (el.findtext('description') or '').strip()
            if title:
                items.append((title, link, date, desc))
        elif t == 'entry':                   # Atom
            title, link, date, desc = '', '', '', ''
            for ch in el:
                ct = _tag(ch)
                if ct == 'title':
                    title = (ch.text or '').strip()
                elif ct == 'link' and ch.get('href'):
                    if ch.get('rel') in (None, 'alternate', ''):
                        link = ch.get('href')
                elif ct in ('updated', 'published') and not date:
                    date = (ch.text or '').strip()
                elif ct in ('summary', 'content') and not desc:
                    desc = (ch.text or '').strip()
            if title:
                items.append((title, link, date, desc))
    return items


def parse_date(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s)
    except Exception:  # noqa: BLE001
        pass
    try:
        return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:  # noqa: BLE001
        return None


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def is_too_old(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (_utcnow() - dt) > datetime.timedelta(days=OLD_CUTOFF_DAYS)


def item_key(title, link):
    return hashlib.sha1((link or title).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------- 推送（多通道，全免费）
def _sec(env_name):
    """取配置：环境变量优先，其次本地 secrets.local.json（键名=环境变量小写）"""
    v = os.environ.get(env_name, '')
    if v:
        return v
    try:
        with open(os.path.join(BASE, 'secrets.local.json'), encoding='utf-8') as f:
            return str(json.load(f).get(env_name.lower(), '') or '')
    except Exception:  # noqa: BLE001
        return ''

def _cut(text, limit=3800):
    """各通道字数上限保护"""
    if len(text) <= limit:
        return text
    return text[:limit] + '\n…（已截断）'


def _post_json(url, payload, timeout=20):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _post_form(url, payload, timeout=20):
    data = urllib.parse.urlencode(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def push_serverchan(key, title, content):
    """Server酱（免费 5 条/天）：sct.ft07.com 微信扫码即得 SendKey"""
    resp = _post_form('https://sctapi.ft07.com/%s.send' % key,
                      {'title': title[:32], 'desp': _cut(content)})
    return resp.get('code') == 0


def push_wecom(hook, title, content):
    """企业微信群机器人 webhook（免费不限量）：消息进企业微信 App"""
    resp = _post_json(hook, {'msgtype': 'markdown',
                             'markdown': {'content': _cut('**%s**\n%s' % (title, content), 3800)}})
    return resp.get('errcode') == 0


def push_wxpusher(token, uid, title, content):
    """WxPusher（免费）：appToken + UID"""
    resp = _post_json('https://wxpusher.zjiecode.com/api/send/message',
                      {'appToken': token, 'uids': [uid], 'summary': title[:100],
                       'content': _cut(content), 'contentType': 3})
    return resp.get('code') == 1000


def push_smtp(cfg, title, content):
    """邮件通道（免费不限量）：QQ邮箱等 SMTP"""
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    msg = MIMEText(_cut(content, 20000), 'plain', 'utf-8')
    msg['Subject'] = Header(title, 'utf-8')
    msg['From'] = cfg['user']
    msg['To'] = cfg['to']
    cls = smtplib.SMTP_SSL if str(cfg.get('port', '465')) == '465' else smtplib.SMTP
    with cls(cfg['host'], int(cfg.get('port', 465)), timeout=25) as s:
        s.login(cfg['user'], cfg['pass'])
        s.sendmail(cfg['user'], [cfg['to']], msg.as_string())
    return True


def push_pushplus(token, title, content):
    resp = _post_json('https://www.pushplus.plus/send',
                      {'token': token, 'title': title,
                       'content': _cut(content, 7000), 'template': 'markdown'})
    return resp.get('code') == 200


def push(title, content):
    """按已配置的通道推送（优先级：企业微信 > Server酱 > 邮件 > WxPusher > PushPlus）
    配置来源：环境变量 或 secrets.local.json（适配计划任务等无环境变量场景）"""
    try:
        if _sec('WECOM_WEBHOOK'):
            return push_wecom(_sec('WECOM_WEBHOOK'), title, content)
        if _sec('SERVERCHAN_KEY'):
            return push_serverchan(_sec('SERVERCHAN_KEY'), title, content)
        smtp = {'host': _sec('SMTP_HOST'), 'port': _sec('SMTP_PORT') or '465',
                'user': _sec('SMTP_USER'), 'pass': _sec('SMTP_PASS'),
                'to': _sec('SMTP_TO')}
        if smtp['host'] and smtp['user'] and smtp['pass'] and smtp['to']:
            return push_smtp(smtp, title, content)
        if _sec('WXPUSHER_TOKEN') and _sec('WXPUSHER_UID'):
            return push_wxpusher(_sec('WXPUSHER_TOKEN'), _sec('WXPUSHER_UID'), title, content)
        if _sec('PUSHPLUS_TOKEN'):
            return push_pushplus(_sec('PUSHPLUS_TOKEN'), title, content)
        print('[push] 未配置任何通道（WECOM_WEBHOOK / SERVERCHAN_KEY / SMTP_* / WXPUSHER_* / PUSHPLUS_TOKEN）')
        return False
    except Exception as e:  # noqa: BLE001
        print('[push] 失败:', e)
        return False


def build_message(fresh):
    groups = {}
    for it in fresh:
        groups.setdefault(it.get('group') or '其它', []).append(it)
    lines = []
    for g in sorted(groups):
        lines.append('**%s**' % g)
        for it in groups[g]:
            dt = it.get('dt')
            if dt:
                tstr = dt.strftime('%m-%d %H:%M')
            elif it.get('_ts'):
                tstr = time.strftime('%m-%d %H:%M', time.localtime(it['_ts'] / 1000))
            else:
                tstr = ''
            lines.append('- %s（%s %s）' % (it['title'], it.get('source', ''), tstr))
            if it.get('summary'):
                lines.append('  摘要：%s' % it['summary'])
            if it.get('_related_n'):
                lines.append('  └ 相关报道 %d 条：%s' % (it['_related_n'], '；'.join(it['_related'])))
            if it.get('link'):
                lines.append('  链接：%s' % it['link'])
        lines.append('')
    return '\n'.join(lines).strip()


# ---------------------------------------------------------------- 相关性评分
# 只推"有用"的：官方源（海外官方/国产官方）直通；社区/中文媒体源需打分过线。
# 主体词(2分/个，封顶6) + 发布动作词(+3) + 版本号(+2)；宕机/折腾/教程类强扣分。
ENTITIES = [
    'openai', 'chatgpt', 'gpt-', 'o1', 'o3', 'o4', 'anthropic', 'claude', 'deepmind',
    'gemini', 'xai', 'grok', 'meta ai', 'llama', 'mistral', 'deepseek', 'zhipu',
    '智谱', 'glm', 'qwen', '通义', 'kimi', '月之暗面', 'moonshot', 'minimax',
    '豆包', 'doubao', '文心', 'ernie', '李飞飞', 'hinton', 'lecun', 'karpathy',
    'sutskever', 'sora', 'midjourney', 'astra', 'fable', 'mythos',
]
RELEASE_WORDS = [
    'release', 'launch', 'announc', 'unveil', 'introduc', 'roll out', 'ship',
    'open-source', 'open source', '开源', '发布', '推出', '官宣', '上新', '上架',
    '亮相', '首发', 'weights', '登场', '预告',
]
NOISE_WORDS = [
    'down', 'outage', '宕机', '故障', '维护', 'ask hn', 'tell hn', 'hiring',
    'job', '教程', 'tok/s', 'tokens/s', '3090', '4090', '5090', 'rtx', '显存',
    'horoscope', '星座',
]
VERSION_RE = re.compile(r'\bv?\d+(?:\.\d+)?\b')
SCORED_GROUPS = ('社区雷达', '中文媒体', '海外官方', '国产官方')  # 需过评分的组
MIN_SCORE = 4


def score_title(title):
    tl = title.lower()
    s = 0
    hits = sum(1 for w in ENTITIES if w in tl)
    s += min(hits, 3) * 2
    if any(w in tl for w in RELEASE_WORDS):
        s += 3
    if VERSION_RE.search(tl):
        s += 2
    if any(w in tl for w in NOISE_WORDS):
        s -= 4
    return s


# ---------------------------------------------------------------- 正文抓取 + 摘要（可插拔：OpenAI兼容API / Claude CLI / 抽取式兜底）
import shutil                       # noqa: E402
import subprocess                   # noqa: E402
from html.parser import HTMLParser  # noqa: E402


def _find_claude():
    """定位本机 Claude Code CLI（无则返回 None）"""
    p = os.environ.get('CLAUDE_CMD', '')
    if p:
        return p
    for c in ('claude.cmd', 'claude.exe', 'claude'):
        w = shutil.which(c)
        if w:
            return w
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        cand = os.path.join(appdata, 'npm', 'claude.ps1')
        if os.path.exists(cand):
            return cand
    return None


CLAUDE_PS1 = _find_claude()


class _TextExtract(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'nav', 'footer', 'header', 'svg'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def fetch_article(url, timeout=12):
    """抓文章正文纯文本（最多 400KB，失败返回空串）"""
    if not url or not url.startswith('http'):
        return ''
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(400_000).decode('utf-8', errors='ignore')
        p = _TextExtract()
        p.feed(html)
        return re.sub(r'\s+', ' ', ' '.join(p.parts))
    except Exception:  # noqa: BLE001
        return ''


def _claude(prompt):
    """调用本机 Claude Code CLI（如经公司网关则用其模型），返回纯文本；不可用返回 ''"""
    if not CLAUDE_PS1:
        return ''
    if CLAUDE_PS1.endswith('.ps1'):
        cmd = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
               '-File', CLAUDE_PS1, '-p', prompt]
    else:
        cmd = [CLAUDE_PS1, '-p', prompt]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        out = (r.stdout or '').strip()
        return ' '.join(out.split()) if out else ''
    except Exception:  # noqa: BLE001
        return ''


def _summarize_api(prompt):
    """OpenAI 兼容 API 摘要（DeepSeek / GLM / SiliconFlow / OpenAI 等皆可）"""
    key = _sec('SUMMARY_API_KEY')
    if not key:
        return ''
    base = _sec('SUMMARY_API_BASE') or 'https://api.deepseek.com/v1'
    model = _sec('SUMMARY_MODEL') or 'deepseek-chat'
    try:
        body = json.dumps({'model': model, 'max_tokens': 260, 'temperature': 0.3,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode('utf-8')
        req = urllib.request.Request(
            base.rstrip('/') + '/chat/completions', data=body,
            headers={'Authorization': 'Bearer ' + key,
                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode('utf-8'))
        return (resp['choices'][0]['message'].get('content') or '').strip()
    except Exception:  # noqa: BLE001
        return ''


def summarize(title, text):
    """摘要链：OpenAI兼容API -> Claude CLI -> 抽取式兜底（模式/语言由 config 与 SUMMARY_MODE 控制）"""
    mode = (os.environ.get('SUMMARY_MODE') or CFG.get('summary_mode', 'auto')).lower()
    lang = (os.environ.get('SUMMARY_LANGUAGE') or CFG.get('summary_language', '中文'))
    excerpt = text[:1600]
    prompt = ('只输出总结本身（不超过3句%s，说清：新东西是什么/谁做的/多强/意义，'
              '不要任何前后缀和引号）：\n标题：%s\n正文节选：%s'
              % (lang, title, excerpt if excerpt else '（无正文，按标题总结）'))
    s = ''
    if mode in ('auto', 'api', 'openai'):
        s = _summarize_api(prompt)
    if not s and mode in ('auto', 'claude'):
        s = _claude(prompt)
    if s:
        return s[:320]
    if text:
        sents = re.split(r'(?<=[。！？!?])', text)[:3]
        return ''.join(sents)[:170]
    return ''


# ---------------------------------------------------------------- 主题聚类（同一事件多条报道 -> 一条）
CLUSTER_PRIORITY = ['gpt', 'astra', 'fable', 'claude', 'gemini', 'gemma', 'glm', 'qwen',
                    'deepseek', 'kimi', 'grok', 'llama', 'minimax', 'mistral', 'sora',
                    'ernie', 'doubao', 'openai', 'anthropic', 'deepmind', 'xai', 'meta']


def cluster_key(title):
    tl = title.lower()
    for e in CLUSTER_PRIORITY:
        if e in tl:
            return e
    return None


def cluster_items(items):
    """同主题多条 -> 最高分领衔 + 相关报道计数；返回按分数排序、截断 MAX_PER_EMAIL"""
    singles, groups = [], {}
    for it in sorted(items, key=lambda x: x.get('score', 0), reverse=True):
        k = cluster_key(it['title'])
        if k is None:
            singles.append(it)
        else:
            groups.setdefault(k, []).append(it)
    merged = []
    for k, lst in groups.items():
        if len(lst) == 1:
            singles.append(lst[0])
            continue
        lead = lst[0]
        lead['_related'] = [x['title'][:56] for x in lst[1:3]]
        lead['_related_n'] = len(lst) - 1
        merged.append(lead)
    out = singles + merged
    out.sort(key=lambda x: x.get('score', 0), reverse=True)
    return out[:MAX_PER_EMAIL]


# ---------------------------------------------------------------- 榜单变动监视（LMArena 等）
MODEL_HINTS = ['gpt', 'claude', 'gemini', 'glm', 'qwen', 'deepseek', 'llama', 'kimi',
               'grok', 'minimax', 'mistral', 'ernie', 'doubao', 'internlm', 'astra',
               'fable', 'nemotron', 'olmo', 'phi-', 'granite', 'command-r']


def handle_watch(src, state):
    """抓榜单页 -> 提取疑似模型名 -> 与上次快照 diff，出现新名字则生成一条播报"""
    text = fetch_article(src['url'], timeout=20)
    if not text:
        return []
    found = set()
    for m in re.finditer(r'[A-Za-z][A-Za-z0-9._\-]{2,40}', text):
        s = m.group(0)
        if any(h in s.lower() for h in MODEL_HINTS):
            found.add(s)
    watches = state.setdefault('watch', {})
    first_time = src['name'] not in watches
    prev = set(watches.get(src['name'], []))
    watches[src['name']] = sorted(found)[:400]
    if first_time:
        return []                      # 首次只建基线
    new = sorted(found - prev)
    if not new:
        return []
    title = '出现新上榜模型：%s' % '、'.join(new[:6])
    key = item_key(title + str(len(new)), src['url'])
    return [{'title': title, 'link': src['url'], 'source': src['name'],
             'group': src.get('group', ''), 'dt': None, '_key': key}]


# ---------------------------------------------------------------- 主流程
def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding='utf-8-sig') as f:   # 容忍 Windows 记事本写入的 BOM
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            print('[state] 读取失败，重建:', e)
    return {'seen': {}, 'first_run': True}


def save_state(state):
    seen = state['seen']
    if len(seen) > MAX_STATE:  # 保留最新部分（按插入序近似即可）
        state['seen'] = dict(list(seen.items())[-MAX_STATE:])
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def main():
    ap = argparse.ArgumentParser(description='AI-Radar')
    ap.add_argument('--dry-run', action='store_true', help='只抓取打印，不推送不写状态')
    ap.add_argument('--test', action='store_true', help='向微信发测试消息')
    ap.add_argument('--init', action='store_true', help='重建基线（记录现状不推送）')
    ap.add_argument('--send-now', action='store_true',
                    help='无视攒批/冷却规则，立即把待发池整封发出')
    args = ap.parse_args()

    if args.test:
        ok = push('🤖 AI-Radar 通道测试', '通道正常！接下来 AI 大厂的新动态会出现在这里。')
        print('TEST:', 'OK' if ok else 'FAILED')
        sys.exit(0)

    with open(SOURCES_PATH, encoding='utf-8') as f:
        sources = json.load(f)

    state = load_state()
    fresh, errors = [], []
    run_seen = set()

    for src in sources:
        if not src.get('enabled', True):
            continue
        # ---- 榜单监视型源（如 LMArena）：抓页面 -> 提取模型名 -> 与上次快照对比 ----
        if src.get('type') == 'watch':
            try:
                for it in handle_watch(src, state):
                    if it['_key'] not in state['seen'] and it['_key'] not in run_seen:
                        fresh.append(it)
                        run_seen.add(it['_key'])
                        print('%-22s 榜单变动: %s' % (src['name'], it['title'][:40]))
            except Exception as e:  # noqa: BLE001
                errors.append('%s: %s' % (src['name'], str(e)[:120]))
            continue
        try:
            data = resolve(src['url'])
            items = parse_feed(data)
        except Exception as e:  # noqa: BLE001
            errors.append('%s: %s' % (src['name'], str(e)[:120]))
            continue
        kws = [k.lower() for k in src.get('keywords', [])]
        use_desc = bool(src.get('search_desc', False))
        need_score = (src.get('group', '') in SCORED_GROUPS
                      and not src.get('skip_score', False))   # GitHub tag 源豁免（标题纯版本号）
        cap = int(src.get('max_items', 0))
        kept = 0
        for title, link, dstr, desc in items:
            if not title:
                continue
            hay = (title + ' ' + (desc or '')).lower() if use_desc else title.lower()
            if kws and not any(k in hay for k in kws):
                continue
            sc = score_title(title)
            if need_score and sc < MIN_SCORE:
                continue   # 有用性不足：社区噪音/硬件折腾/宕机吐槽等，静默丢弃
            key = item_key(title, link)
            if key in state['seen'] or key in run_seen:
                continue
            dt = parse_date(dstr)
            if dt is not None and is_too_old(dt):
                continue
            fresh.append({'title': title, 'link': link, 'source': src['name'],
                          'group': src.get('group', ''), 'dt': dt, '_key': key,
                          'score': sc if need_score else 9})
            run_seen.add(key)
            kept += 1
            if cap and kept >= cap:
                break
        print('%-22s %3d 条解析 / %d 条新增' % (src['name'], len(items), kept))

    for e in errors:
        print('[源失败] %s' % e)

    # ---- dry-run：只看不推不写（优先于建基线，本地测试用） ----
    if args.dry_run:
        print('---- dry-run：以下 %d 条满足条件 ----' % len(fresh))
        for it in fresh[:40]:
            print('  [%s] %s' % (it['group'], it['title']))
        return

    # ---- 首跑 / 显式 init：建基线，不推送 ----
    if args.init or state.get('first_run', False):
        for it in fresh:
            state['seen'][it['_key']] = it['title'][:80]
        state['first_run'] = False
        state['pending'] = []
        save_state(state)
        print('基线建立完成：记录 %d 条当前条目，本次不推送。' % len(fresh))
        return

    # ---- 新条目入待发池（同时记 seen 防重复抓取） ----
    now_ms = int(time.time() * 1000)
    pending = state.setdefault('pending', [])
    for it in fresh:
        state['seen'][it['_key']] = it['title'][:80]
        pending.append({'title': it['title'], 'link': it['link'], 'source': it['source'],
                        'group': it.get('group', ''), '_key': it['_key'],
                        '_ts': now_ms, 'score': it.get('score', 0)})

    if not pending:
        print('无新消息（待发池空）。')
        save_state(state)
        return

    # ---- 发送节奏控制：日均约 3 封；重大新闻立即发；闲时攒批 ----
    oldest_age = (now_ms - min(p['_ts'] for p in pending)) / 60000.0
    has_high = any(p.get('score', 0) >= HIGH_SCORE for p in pending)
    since_last = (now_ms - state.get('last_sent', 0)) / 60000.0

    send = (args.send_now or has_high
            or len(pending) >= BATCH_MIN_ITEMS
            or oldest_age >= BATCH_MAX_AGE)
    if send and not (args.send_now or has_high) and since_last < SEND_COOLDOWN:
        send = False   # 冷却期内：普通消息再等等

    if not send:
        print('攒批中：%d 条待发（最早已等 %d 分钟）。发车条件：满 %d 条 / 满 %d 分钟 / 出现重大新闻'
              % (len(pending), int(oldest_age), BATCH_MIN_ITEMS, BATCH_MAX_AGE))
        save_state(state)
        return

    # ---- 聚类（同一事件多条报道合成一条）+ 摘要 + 发送 ----
    chosen = cluster_items(pending)
    for i, it in enumerate(chosen[:SUMMARY_LIMIT]):
        art = fetch_article(it['link'])
        it['summary'] = summarize(it['title'], art)
        print('  摘要 %d/%d %s' % (i + 1, min(len(chosen), SUMMARY_LIMIT), it['title'][:28]))

    subject = '🤖 AI 雷达：%d 条动态' % len(chosen)
    if len(pending) > len(chosen):
        subject += '（聚合自 %d 条报道）' % len(pending)
    ok = push(subject, build_message(chosen))
    if ok:
        state['pending'] = []
        state['last_sent'] = now_ms
        save_state(state)
        print('已推送 %d 条（聚合自 %d 条）。' % (len(chosen), len(pending)))
    else:
        save_state(state)
        print('推送失败：待发池保留，下次运行自动重试。')
        sys.exit(0)  # 不让 Actions 标红


if __name__ == '__main__':
    main()
