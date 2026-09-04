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
MIN_SCORE = int(CFG.get('min_score', 4))            # 入选门槛（关键词预筛，控成本）
HIGH_SCORE = int(CFG.get('high_score', 7))          # 关键词高分兜底（LLM 不可用时）
HIGH_INFLUENCE = int(CFG.get('high_influence', 8))  # LLM 影响力≥此值：立即发+冷却豁免
INFLUENCE_FLOOR = int(CFG.get('influence_floor', 4))  # 影响力≤此值：评估后直接不入池
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


def push_smtp(cfg, title, items):
    """邮件通道：HTML 卡片排版 + 纯文本降级（免费不限量）"""
    import smtplib
    from email.header import Header
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    if isinstance(items, str):
        html, plain = '<pre style="font-family:sans-serif;">%s</pre>' % _esc(items), items
    else:
        html, plain = build_html(title, items), build_plain(items)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(title, 'utf-8')
    msg['From'] = cfg['user']
    msg['To'] = cfg['to']
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
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


def push(title, items):
    """按已配置的通道推送。items 为条目列表时各通道自动排版（邮件=HTML卡片，
    企微/Server酱等=Markdown）；items 为字符串时按原文发送。
    配置来源：环境变量 或 secrets.local.json（适配计划任务等无环境变量场景）"""
    try:
        if _sec('WECOM_WEBHOOK'):
            c = items if isinstance(items, str) else build_markdown(items)
            return push_wecom(_sec('WECOM_WEBHOOK'), title, c)
        if _sec('SERVERCHAN_KEY'):
            c = items if isinstance(items, str) else build_markdown(items)
            return push_serverchan(_sec('SERVERCHAN_KEY'), title, c)
        smtp = {'host': _sec('SMTP_HOST'), 'port': _sec('SMTP_PORT') or '465',
                'user': _sec('SMTP_USER'), 'pass': _sec('SMTP_PASS'),
                'to': _sec('SMTP_TO')}
        if smtp['host'] and smtp['user'] and smtp['pass'] and smtp['to']:
            return push_smtp(smtp, title, items)
        if _sec('WXPUSHER_TOKEN') and _sec('WXPUSHER_UID'):
            c = items if isinstance(items, str) else build_markdown(items)
            return push_wxpusher(_sec('WXPUSHER_TOKEN'), _sec('WXPUSHER_UID'), title, c)
        if _sec('PUSHPLUS_TOKEN'):
            c = items if isinstance(items, str) else build_markdown(items)
            return push_pushplus(_sec('PUSHPLUS_TOKEN'), title, c)
        print('[push] 未配置任何通道（WECOM_WEBHOOK / SERVERCHAN_KEY / SMTP_* / WXPUSHER_* / PUSHPLUS_TOKEN）')
        return False
    except Exception as e:  # noqa: BLE001
        print('[push] 失败:', e)
        return False


def _esc(s):
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _dt_ms(dt):
    """发布时间 -> 毫秒时间戳（入待发池时保留；naive 视为 UTC）"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _tstr(it):
    """显示时间 = 信息发布时间（优先）；无发布时间才退回采集时间"""
    dt = it.get('dt')
    if dt is None and it.get('_dt'):
        dt = datetime.datetime.fromtimestamp(it['_dt'] / 1000, datetime.timezone.utc)
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone().strftime('%m-%d %H:%M')
    if it.get('_ts'):
        return time.strftime('%m-%d %H:%M', time.localtime(it['_ts'] / 1000))
    return ''


def _disp_title(it):
    """主标题：中文标题优先"""
    return it.get('title_cn') or it['title']


def _orig_title(it):
    """原题（仅当原标题为外文且已翻译时返回，用于副行展示）"""
    if it.get('title_cn') and it['title_cn'] != it['title'] and not _has_cjk(it['title']):
        return it['title']
    return ''


def _group_items(items):
    groups = {}
    for it in items:
        groups.setdefault(it.get('group') or '其它', []).append(it)
    return groups


def build_markdown(items):
    """Markdown 排版（企微/Server酱/PushPlus/WxPusher 用）"""
    lines = []
    for g, lst in sorted(_group_items(items).items()):
        lines.append('**▎%s**' % g)
        for it in lst:
            lines.append('- **%s**（%s %s）' % (_disp_title(it), it.get('source', ''), _tstr(it)))
            orig = _orig_title(it)
            if orig:
                lines.append('  > 原题：%s' % orig)
            if it.get('summary'):
                lines.append('  > %s' % it['summary'])
            if it.get('_related_n'):
                lines.append('  > └ 相关报道 %d 条：%s' % (it['_related_n'], '；'.join(it['_related'])))
            if it.get('link'):
                lines.append('  > [阅读原文](%s)' % it['link'])
        lines.append('')
    return '\n'.join(lines).strip()


def build_plain(items):
    """纯文本排版（邮件降级/无 HTML 客户端用）"""
    out = []
    for i, it in enumerate(items, 1):
        out.append('[%02d] %s' % (i, _disp_title(it)))
        orig = _orig_title(it)
        meta = '%s · %s' % (it.get('source', ''), _tstr(it))
        out.append('     %s%s' % (meta, (' · 原题：' + orig) if orig else ''))
        if it.get('summary'):
            out.append('     摘要：%s' % it['summary'])
        if it.get('_related_n'):
            out.append('     └ 相关报道 %d 条：%s' % (it['_related_n'], '；'.join(it['_related'])))
        if it.get('link'):
            out.append('     原文：%s' % it['link'])
        out.append('')
    return '\n'.join(out).strip()


def build_html(title, items):
    """HTML 邮件排版（卡片式，内联样式兼容各邮箱客户端）"""
    css_card = ('style="background:#ffffff;border:1px solid #e3e8ee;border-radius:10px;'
                'padding:14px 16px;margin:0 0 12px 0;font-family:-apple-system,'
                "'PingFang SC','Microsoft YaHei',sans-serif;\"")
    parts = [
        '<div style="max-width:640px;margin:0 auto;font-family:-apple-system,'
        "'PingFang SC','Microsoft YaHei',sans-serif;\">",
        '<div style="background:#01579B;border-radius:10px 10px 0 0;color:#ffffff;'
        'padding:16px 20px;">'
        '<div style="font-size:18px;font-weight:bold;">🤖 %s</div>'
        '<div style="font-size:12px;opacity:.85;margin-top:4px;">%s · 共 %d 条'
        '</div></div>' % (_esc(title),
                          time.strftime('%Y-%m-%d %H:%M'), len(items)),
        '<div style="background:#f4f7fa;border-radius:0 0 10px 10px;padding:14px 12px;">',
    ]
    for g, lst in sorted(_group_items(items).items()):
        parts.append('<div style="font-size:12px;color:#5f6b7a;font-weight:bold;'
                     'letter-spacing:2px;margin:10px 4px 8px;">▎%s</div>' % _esc(g))
        for it in lst:
            parts.append('<div %s>' % css_card)
            parts.append('<div style="font-size:17px;font-weight:bold;color:#0f181f;'
                         'line-height:1.5;margin-bottom:2px;">%s</div>' % _esc(_disp_title(it)))
            orig = _orig_title(it)
            meta = '%s · %s' % (_esc(it.get('source', '')), _tstr(it))
            if orig:
                meta += ' · 原题：%s' % _esc(orig)
            parts.append('<div style="font-size:12px;color:#8a95a1;margin:4px 0 10px;">'
                         '%s</div>' % meta)
            if it.get('summary'):
                sents = [x for x in re.split(r'(?<=[。！？!?；])\s*', it['summary']) if x.strip()]
                lines = ''.join('<div style="margin:0 0 4px 0;">%s</div>' % _esc(s)
                                for s in sents) or _esc(it['summary'])
                parts.append('<div style="font-size:13px;color:#37424e;line-height:1.7;'
                             'background:#f8fafc;border-left:3px solid #0288D1;'
                             'padding:10px 12px;border-radius:0 6px 6px 0;">%s</div>' % lines)
            if it.get('_related_n'):
                parts.append('<div style="font-size:12px;color:#8a95a1;margin-top:6px;">'
                             '└ 相关报道 %d 条：%s</div>'
                             % (it['_related_n'], _esc('；'.join(it['_related']))))
            if it.get('link'):
                parts.append('<a href="%s" style="display:inline-block;font-size:12px;'
                             'color:#0288D1;text-decoration:none;margin-top:8px;'
                             'border:1px solid #b3d7f2;border-radius:6px;padding:3px 10px;">'
                             '阅读原文 →</a>' % _esc(it['link']))
            parts.append('</div>')
    parts.append('<div style="text-align:center;font-size:11px;color:#a5b0bc;'
                 'padding:10px 0 4px;">由 AI-Radar 自动生成 · 评分过滤 / 主题聚类 / LLM 摘要</div>')
    parts.append('</div></div>')
    return ''.join(parts)


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
    'overview', 'critical capabilities', 'safeguards', 'system card',  # 官方重磅文档信号
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
    """定位本机 Claude Code CLI：只接受可执行类型（.ps1/.cmd/.exe），
    绝不返回无扩展名的 Unix sh 包装脚本（Windows 无法直接执行，WinError 193）"""
    p = os.environ.get('CLAUDE_CMD', '')
    if p and os.path.exists(p):
        return p
    appdata = os.environ.get('APPDATA', '')          # npm 全局安装的标准位置（Windows）
    if appdata:
        cand = os.path.join(appdata, 'npm', 'claude.ps1')
        if os.path.exists(cand):
            return cand
    for c in ('claude.exe', 'claude.cmd'):
        w = shutil.which(c)
        if w:
            return w
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


# 中文科技媒体行文样板（媒体署名/记者名/日期播报等）——污染摘要，统一剥离
_META_PATTERNS = [
    r'\d{1,2}月\d{1,2}日消息[，,]?',
    r'20\d{2}年\d{1,2}月\d{1,2}日[，,]?',
    r'\d{1,2}月\d{1,2}日[，,]?',
    r'发自[\u4e00-\u9fff]{2,6}',
    r'记者[\u4e00-\u9fff]{2,5}(报道)?',
    r'(量子位|机器之心|新智元|DeepTech|智东西|财联社|科创板日报|雷峰网|TechWeb)\s*[|｜]',
    r'点击关注[\u4e00-\u9fff]{0,10}',
    r'关注[\u4e00-\u9fff]{2,8}\s*[:：]\s*[\u4e00-\u9fffA-Za-z0-9]{3,12}',
    r'本文来自[\u4e00-\u9fff]{2,12}',
    r'题图来自[\u4e00-\u9fff]{0,12}',
    r'(综合|据)\s*(报道|消息)',
]


def _strip_meta(s):
    for p in _META_PATTERNS:
        s = re.sub(p, ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def fetch_article(url, timeout=12):
    """抓文章正文纯文本（最多 400KB；清除残留HTML标签与媒体样板，失败返回空串）"""
    if not url or not url.startswith('http'):
        return ''
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(400_000).decode('utf-8', errors='ignore')
        p = _TextExtract()
        p.feed(html)
        text = ' '.join(p.parts)
        text = re.sub(r'<[^>]{1,300}>', ' ', text)       # 网页里被转义的 <img id=... 等残留
        text = re.sub(r'https?://\S{40,}', ' ', text)     # 正文里的超长裸链接
        return _strip_meta(text)
    except Exception:  # noqa: BLE001
        return ''


def _claude(prompt, timeout=120):
    """调用本机 Claude Code CLI；失败重试1次，带诊断输出；不可用返回 ''"""
    if not CLAUDE_PS1:
        return ''
    if CLAUDE_PS1.endswith('.ps1'):
        cmd = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
               '-File', CLAUDE_PS1, '-p', prompt]
    elif CLAUDE_PS1.endswith('.cmd') or CLAUDE_PS1.endswith('.bat'):
        cmd = ['cmd.exe', '/c', CLAUDE_PS1, '-p', prompt]
    else:
        cmd = [CLAUDE_PS1, '-p', prompt]
    for attempt in (1, 2):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=timeout)
            out = (r.stdout or '').strip()
            if out:
                return ' '.join(out.split())
            print('[claude] 第%d次返回空' % attempt)
        except subprocess.TimeoutExpired:
            print('[claude] 第%d次超时(%ds)' % (attempt, timeout))
        except Exception as e:  # noqa: BLE001
            print('[claude] 异常: %s' % str(e)[:100])
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


def _has_cjk(s):
    return any('\u4e00' <= c <= '\u9fff' for c in (s or ''))


def _sent_cut(s, limit):
    """超长时按句子边界截断（不切半句）"""
    s = (s or '').strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    for stop in '。！？!?.；;':
        i = head.rfind(stop)
        if i > limit * 0.5:
            return head[:i + 1]
    return head.rstrip() + '…'


def summarize(title, text):
    """理解式评估：通读正文后输出 (中文标题|None, 影响力1-10|None, 摘要)。
    影响力标准：9-10 里程碑级（新旗舰模型/重磅开源/行业格局变化）；7-8 重要（大厂重要
    版本或产品）；5-6 一般常规更新；4以下 边缘/营销/个案。抽取式兜底时影响力为 None。"""
    mode = (os.environ.get('SUMMARY_MODE') or CFG.get('summary_mode', 'auto')).lower()
    lang = (os.environ.get('SUMMARY_LANGUAGE') or CFG.get('summary_language', '中文'))
    excerpt = text[:1800]
    prompt = ('严格按以下三行格式输出，不要任何其它内容：\n'
              '中文标题：<把下面的新闻标题翻译成自然%s；若原标题已是%s则原样输出；'
              '产品名/型号保留原文；去掉标题里的媒体名/栏目名前缀>\n'
              '影响力：<1-10整数。标尺：10=全球顶级实验室旗舰模型发布'
              '（如 OpenAI GPT-6 Astra、Anthropic Claude 5.1 Fable、Gemini 旗舰）；'
              '9=国产旗舰大模型发布（如 GLM-5.3、DeepSeek V4 Pro）；'
              '8=重要衍生版本或旗舰级工具（如 GLM-5.3 Flash、DeepSeek V4 Flash、'
              '官方重要框架/工具链）；7=大厂重要产品或功能更新、重要论文；'
              '5-6=常规更新、第三方适配与集成；4以下=营销活动/客户个案/边缘话题>\n'
              '摘要：<用精炼的%s总结，1~3句、总共不超过120字；只保留最有信息量的要点'
              '（新东西是什么/谁做的/多强/关键数据），删除铺垫、修饰与重复；'
              '摘要里禁止出现媒体名、记者名、发布日期、"据报道"等一切来源信息；'
              '忽略正文里的HTML标签或代码垃圾>\n'
              '标题：%s\n正文节选：%s'
              % (lang, lang, lang, title, excerpt if excerpt else '（无正文，按标题评估）'))
    s = ''
    if mode in ('auto', 'api', 'openai'):
        s = _summarize_api(prompt)
    if not s and mode in ('auto', 'claude'):
        s = _claude(prompt)
    if s:
        m_t = re.search(r'中文标题[:：]\s*(.+)', s)
        m_i = re.search(r'影响力[:：]\s*[^0-9]*(\d{1,2})', s)
        m_s = re.search(r'摘要[:：]\s*([\s\S]+)', s)
        title_cn = None
        if m_t:
            t = re.split(r'\s*(?:影响力|摘要)[:：]', m_t.group(1))[0].strip()
            title_cn = t[:120] or None
        influence = min(10, max(1, int(m_i.group(1)))) if m_i else None
        summary = m_s.group(1).strip() if m_s else s.strip()
        title_cn = _strip_meta(title_cn) if title_cn else None
        summary = _strip_meta(summary)
        if title_cn and title_cn == title:
            title_cn = None
        # ---- 语言保真：模型不听话时强制重译 ----
        if summary and not _has_cjk(summary):
            s2 = _summarize_api('把下面的内容翻译成精炼中文摘要（1~3句、120字内，'
                                '禁止媒体名/记者名/日期）：\n' + title + '\n' + summary)
            if not s2 and mode in ('auto', 'claude'):
                s2 = _claude('把下面的内容翻译成精炼中文摘要（1~3句、120字内，'
                             '禁止媒体名/记者名/日期）：\n' + title + '\n' + summary)
            if s2 and _has_cjk(s2):
                summary = _strip_meta(s2)
        if not _has_cjk(title) and not title_cn:
            t2 = _summarize_api('只输出译文本身：把这句新闻标题翻译成自然中文'
                                '（产品名/型号保留原文）：' + title)
            if not t2 and mode in ('auto', 'claude'):
                t2 = _claude('只输出译文本身：把这句新闻标题翻译成自然中文'
                             '（产品名/型号保留原文）：' + title)
            if t2 and _has_cjk(t2):
                title_cn = _strip_meta(t2.splitlines()[0])[:120]
        return title_cn, influence, _sent_cut(summary, 220)
    if text:
        sents = re.split(r'(?<=[。！？!?])', text)[:3]
        return None, None, _sent_cut(''.join(sents), 200)
    return None, None, ''


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


def _prio(x):
    """排序/触发优先级：LLM 影响力优先，关键词分兜底"""
    return x.get('influence') or 0, x.get('score') or 0


def cluster_items(items):
    """同主题多条 -> 影响力最高者领衔 + 相关报道计数；按优先级排序、截断 MAX_PER_EMAIL"""
    singles, groups = [], {}
    for it in sorted(items, key=_prio, reverse=True):
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
    out.sort(key=_prio, reverse=True)
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


def flush_pending(state, force=False, slot=False):
    """发送窗口：处理待发池（聚类->补摘要->推送）。slot=True 表示整点窗口模式：有料就发。"""
    pending = state.setdefault('pending', [])
    if not pending:
        print('无新消息（待发池空）。')
        save_state(state)
        return
    now_ms = int(time.time() * 1000)
    oldest_age = (now_ms - min(p['_ts'] for p in pending)) / 60000.0
    has_high = any((p.get('influence') or 0) >= HIGH_INFLUENCE
                   or p.get('score', 0) >= HIGH_SCORE for p in pending)
    since_last = (now_ms - state.get('last_sent', 0)) / 60000.0

    if slot:
        send = force or has_high or since_last >= SEND_COOLDOWN
    else:
        send = (force or has_high or len(pending) >= BATCH_MIN_ITEMS
                or oldest_age >= BATCH_MAX_AGE)
        if send and not (force or has_high) and since_last < SEND_COOLDOWN:
            send = False
    if not send:
        print('攒批中：%d 条待发（最早已等 %d 分钟，冷却剩 %d 分钟）'
              % (len(pending), int(oldest_age), max(0, int(SEND_COOLDOWN - since_last))))
        save_state(state)
        return

    chosen = cluster_items(pending)
    for it in chosen[:SUMMARY_LIMIT]:
        if not it.get('summary'):        # 老池子条目补评估
            art = fetch_article(it['link'])
            it['title_cn'], it['influence'], it['summary'] = summarize(it['title'], art)

    subject = '🤖 AI 雷达：%d 条动态' % len(chosen)
    if len(pending) > len(chosen):
        subject += '（聚合自 %d 条报道）' % len(pending)
    ok = push(subject, chosen)
    if ok:
        state['pending'] = []
        state['last_sent'] = now_ms
        save_state(state)
        print('已推送 %d 条（聚合自 %d 条）。' % (len(chosen), len(pending)))
    else:
        save_state(state)
        print('推送失败：待发池保留，下次运行自动重试。')


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
    ap.add_argument('--eval-only', action='store_true',
                    help='只扫描+评估+入池，不推送（配合定时：X:45 评估）')
    ap.add_argument('--send-only', action='store_true',
                    help='跳过扫描，只处理待发池（配合定时：整点发送窗口）')
    args = ap.parse_args()

    if args.test:
        ok = push('🤖 AI-Radar 通道测试', '通道正常！接下来 AI 大厂的新动态会出现在这里。')
        print('TEST:', 'OK' if ok else 'FAILED')
        sys.exit(0)

    state = load_state()

    # ---- 发送窗口模式：跳过扫描，只处理待发池（配合定时任务整点触发） ----
    if args.send_only:
        flush_pending(state, force=bool(args.send_now), slot=True)
        return

    with open(SOURCES_PATH, encoding='utf-8') as f:
        sources = json.load(f)

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

    # ---- 新条目：理解式评估（GLM 通读正文 -> 中文标题/影响力/摘要）后入待发池 ----
    now_ms = int(time.time() * 1000)
    pending = state.setdefault('pending', [])
    for i, it in enumerate(fresh):
        state['seen'][it['_key']] = it['title'][:80]
        art = fetch_article(it['link'])
        it['title_cn'], it['influence'], it['summary'] = summarize(it['title'], art)
        inf = it.get('influence')
        print('  评估%d/%d 影响%s %s' % (i + 1, len(fresh), inf or '-',
                                         (it.get('title_cn') or it['title'])[:38]))
        if inf is not None and inf <= INFLUENCE_FLOOR:
            continue   # 低影响力（营销/个案/边缘）：已记 seen，不入池不推送
        pending.append({'title': it['title'], 'link': it['link'], 'source': it['source'],
                        'group': it.get('group', ''), '_key': it['_key'],
                        '_ts': now_ms, 'score': it.get('score', 0),
                        '_dt': _dt_ms(it.get('dt')),
                        'title_cn': it.get('title_cn'), 'summary': it.get('summary', ''),
                        'influence': inf})
    save_state(state)

    if args.eval_only:
        print('评估完成：%d 条在池，等待发送窗口。' % len(pending))
        return

    flush_pending(state, force=bool(args.send_now), slot=False)


if __name__ == '__main__':
    main()
