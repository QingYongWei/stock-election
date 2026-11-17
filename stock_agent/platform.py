"""Local platform storage. Secrets are stored in local SQLite, not encrypted.

No operation performs network I/O except an explicit test_provider call.
Call initialize on the application's existing connection after core schema setup.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

BRAND = '创作罗盘（暂名）'
PROFILE_TEXT = {'brand':80, 'goal':4000, 'audience':2000, 'tone':2000,
                'stock_list':1000, 'materials':12000, 'rules':4000,
                'topics':4000, 'reader':2000, 'style':2000, 'watch':4000,
                'dsa_report_dir':4000, 'dsa_repo':4000, 'dsa_python':4000,
                'schedule_time':5, 'timezone':80}
FINANCE_ONLY = {'stock_list', 'dsa_report_dir', 'dsa_repo', 'dsa_python', 'dsa_run_enabled', 'dsa_timeout'}
PROFILE_FIELDS = set(PROFILE_TEXT) | {'schedule_enabled', 'weekly_day', 'dsa_run_enabled', 'dsa_timeout'}
PRESETS = [
    dict(id='deepseek',name='DeepSeek',api_base='https://api.deepseek.com',website='https://platform.deepseek.com',promotion_url=''),
    dict(id='aliyun',name='阿里云百炼',api_base='https://dashscope.aliyuncs.com/compatible-mode/v1',website='https://bailian.console.aliyun.com',promotion_url=''),
    dict(id='siliconflow',name='硅基流动',api_base='https://api.siliconflow.cn/v1',website='https://cloud.siliconflow.cn',promotion_url=''),
    dict(id='custom',name='自定义 OpenAI 兼容服务',api_base='',website='',promotion_url=''),
]


def _core():
    return importlib.import_module('.core', __package__)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _object(value, allowed):
    if type(value) is not dict or any(type(k) is not str or k not in allowed for k in value):
        raise ValueError('JSON对象包含不支持的字段')
    try:
        _dump(value)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('JSON数据无效') from None


def _text(value, limit, empty=True):
    if type(value) is not str or len(value) > limit or '\x00' in value:
        raise ValueError('文本类型或长度无效')
    value = value.strip()
    if not empty and not value:
        raise ValueError('必填文本不能为空')
    return value


def _id(value, prefix):
    if type(value) is not str or not re.fullmatch(re.escape(prefix) + r'[a-z0-9-]{1,64}', value):
        raise ValueError('ID格式无效')
    return value


def directions():
    return json.loads(Path(__file__).with_name('directions.json').read_text(encoding='utf-8'))


def get_direction(id):
    if type(id) is not str:
        raise ValueError('方向ID无效')
    d = next((d for d in directions() if d['id'] == id), None)
    if d is None:
        raise KeyError('方向不存在')
    return d


def _setting(c, key, default=None):
    row = c.execute('SELECT value FROM platform_settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set(c, key, value):
    c.execute('INSERT INTO platform_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, _dump(value)))


def _workspace(c, wid):
    _id(wid, 'workspace-')
    row = c.execute('SELECT value FROM workspaces WHERE id=?', (wid,)).fetchone()
    if not row:
        raise KeyError('工作空间不存在')
    return json.loads(row[0])


def _put_workspace(c, w):
    c.execute('INSERT INTO workspaces VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (w['id'], _dump(w)))


def _new_workspace(direction):
    p = dict(brand=direction['workspace_name'], goal=direction['work'], audience=direction['reader'],
             tone='清楚、具体，分清事实与观点', materials=direction['materials'], rules=direction['rules'],
             schedule_enabled=False, schedule_time='18:00', weekly_day=4)
    if direction['id'] == 'finance':
        p.update({k:v for k,v in _core().DEFAULT.items() if k in PROFILE_FIELDS})
    return dict(id='workspace-'+direction['id'], direction_id=direction['id'], domain=direction['domain'],
                name=direction['workspace_name'], profile=p, feedback={}, feedback_records=[])


def initialize(c, legacy=False):
    """Idempotently initialize on the supplied connection, without core.init()."""
    c.execute('CREATE TABLE IF NOT EXISTS platform_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    if _setting(c, 'initialized', False):
        return
    if legacy:
        old = {}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'config' in tables:
            row = c.execute('SELECT value FROM config WHERE id=1').fetchone()
            if row:
                old = json.loads(row[0])
        w = _new_workspace(next(d for d in directions() if d['id']=='finance'))
        w['profile'].update({k:v for k,v in old.items() if k in PROFILE_FIELDS})
        w['name'] = w['profile']['brand']
        c.execute('INSERT OR IGNORE INTO workspaces VALUES (?,?)', (w['id'], _dump(w)))
        if _setting(c, 'active_workspace_id') is None:
            _set(c, 'active_workspace_id', w['id'])
        if 'jobs' in tables:
            for jid, value in c.execute('SELECT id,value FROM jobs').fetchall():
                job = json.loads(value)
                if not job.get('workspace_id'):
                    job['workspace_id']='workspace-finance'
                    job.setdefault('domain', 'finance')
                    c.execute('UPDATE jobs SET value=? WHERE id=?', (_dump(job), jid))
        if old.get('api_key'):
            p = dict(id='provider-legacy',name='旧版模型配置',preset='custom',api_base=old.get('api_base',''),
                     model=old.get('model',''),api_key=old['api_key'],promotion_url='')
            c.execute('INSERT OR IGNORE INTO providers VALUES (?,?)', (p['id'],_dump(p)))
            if _setting(c,'assignments') is None:
                assigned = p['id'] if old.get('provider_mode') == 'openai' else None
                _set(c,'assignments',dict(topic_provider_id=assigned,writing_provider_id=assigned))
    _set(c, 'initialized', True)


def list_workspaces():
    with _core().LOCK, _core().db() as c:
        return [json.loads(r[0]) for r in c.execute('SELECT value FROM workspaces ORDER BY rowid')]


def get_workspace(id):
    with _core().LOCK, _core().db() as c:
        return _workspace(c, id)


def create_workspace(payload):
    _object(payload, {'direction_id'})
    d = next((d for d in directions() if d['id']==payload.get('direction_id')), None)
    if d is None:
        raise ValueError('不支持的方向')
    w = _new_workspace(d)
    with _core().LOCK, _core().db() as c:
        c.execute('INSERT OR IGNORE INTO workspaces VALUES (?,?)', (w['id'],_dump(w)))
        return _workspace(c,w['id'])


def _profile(payload, domain):
    _object(payload, PROFILE_FIELDS)
    if domain != 'finance' and set(payload) & FINANCE_ONLY:
        raise ValueError('该方向不支持财经高级字段')
    result = {}
    for key, value in payload.items():
        if key in PROFILE_TEXT:
            result[key] = _text(value, PROFILE_TEXT[key])
        elif key in ('schedule_enabled','dsa_run_enabled'):
            if type(value) is not bool: raise ValueError('开关必须为布尔值')
            result[key] = value
        elif key == 'weekly_day':
            if type(value) is not int or value not in range(7): raise ValueError('每周日期范围0至6')
            result[key]=value
        elif key == 'dsa_timeout':
            if type(value) is not int or not 30 <= value <= 1800: raise ValueError('DSA超时范围30至1800秒')
            result[key]=value
    if 'schedule_time' in result and not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', result['schedule_time']):
        raise ValueError('定时时间格式为 HH:MM')
    if 'stock_list' in result: _core().codes(result['stock_list'])
    return result


def update_workspace(id, payload):
    _object(payload, {'name','profile'})
    with _core().LOCK, _core().db() as c:
        w = _workspace(c,id)
        changes = _profile(payload.get('profile',{}), w['domain'])
        if 'name' in payload:
            w['name'] = _text(payload['name'],80,False)
        if 'brand' in changes:
            if not changes['brand']: raise ValueError('品牌不能为空')
            w['name']=changes['brand']
        w['profile'].update(changes)
        profile = w['profile']
        if w['domain'] == 'finance' and profile.get('dsa_run_enabled'):
            if not Path(profile.get('dsa_repo','')).joinpath('main.py').is_file():
                raise ValueError('DSA项目目录必须存在main.py')
            if not Path(profile.get('dsa_python','')).is_file():
                raise ValueError('请填写已安装DSA依赖的Python解释器完整路径')
            if profile.get('dsa_report_dir') and Path(profile['dsa_report_dir']).resolve() != (Path(profile['dsa_repo'])/'reports').resolve():
                raise ValueError('启用DSA运行时，报告目录必须为该DSA项目的reports，不能指向其他旧目录')
        _put_workspace(c,w)
        if id == 'workspace-finance':
            row = c.execute('SELECT value FROM config WHERE id=1').fetchone()
            if row:
                old = json.loads(row[0])
                old.update({k:v for k,v in changes.items() if k in _core().DEFAULT and k in PROFILE_FIELDS})
                c.execute('UPDATE config SET value=? WHERE id=1',(_dump(old),))
        return w


def activate_workspace(id):
    with _core().LOCK, _core().db() as c:
        w = _workspace(c,id)
        _set(c,'active_workspace_id',id)
        return w


def feedback(id, payload):
    _object(payload, {'published','difficulty','question','skipped'})
    f = dict(published=payload.get('published','unknown'), difficulty=payload.get('difficulty','unknown'),
             question=_text(payload.get('question',''),500), skipped=payload.get('skipped',False))
    if f['published'] not in ('yes','no','unknown') or f['difficulty'] not in ('easy','hard','unknown') or type(f['skipped']) is not bool:
        raise ValueError('反馈选项无效')
    f.update(saved=True, saved_at=_core().stamp(), workspace_id=id)
    with _core().LOCK, _core().db() as c:
        w = _workspace(c,id)
        w['feedback']=f
        w['feedback_records']=(w.get('feedback_records',[])+[f])[-100:]
        _put_workspace(c,w)
        return w


def _provider(c, pid):
    _id(pid,'provider-')
    row = c.execute('SELECT value FROM providers WHERE id=?',(pid,)).fetchone()
    if not row: raise KeyError('模型服务不存在')
    return json.loads(row[0])


def _public(p):
    return {**{k:v for k,v in p.items() if k != 'api_key'}, 'has_api_key':bool(p.get('api_key')),
            'is_promotion':bool(p.get('promotion_url')), 'billing_notice':'官网直充，非平台钱包；推广链接如有将明确标示',
            'storage_notice':'密钥仅保存在本机SQLite，未宣称加密', 'model_notice':'请填写自己的模型ID；保存不代表连接成功'}


def list_providers():
    with _core().LOCK, _core().db() as c:
        return [_public(json.loads(r[0])) for r in c.execute('SELECT value FROM providers ORDER BY rowid')]


def _url(value, optional=False):
    value = _text(value,2048,optional)
    if optional and not value: return value
    try:
        u = urlsplit(value)
        if u.scheme not in ('http','https') or not u.hostname or u.username is not None or u.password is not None or u.query or u.fragment or any(ch.isspace() for ch in value) or '\\' in value:
            raise ValueError()
        u.port
    except ValueError:
        raise ValueError('地址须为无用户信息、查询参数或片段的HTTP(S) URL') from None
    return value.rstrip('/')


def _disable_legacy_key(c):
    """Explicit deletion/clearing overrides the old operational configuration too."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config'").fetchone():
        return
    row = c.execute('SELECT value FROM config WHERE id=1').fetchone()
    if row:
        cfg = json.loads(row[0])
        cfg.update(api_key='', provider_mode='facts')
        c.execute('UPDATE config SET value=? WHERE id=1', (_dump(cfg),))


def save_provider(payload, id=None):
    _object(payload, {'name','preset','api_base','model','api_key','clear_api_key','promotion_url','use_as_default'})
    if 'clear_api_key' in payload and type(payload['clear_api_key']) is not bool:
        raise ValueError('清除密钥开关必须为布尔值')
    if 'use_as_default' in payload and type(payload['use_as_default']) is not bool:
        raise ValueError('用于创作开关必须为布尔值')
    with _core().LOCK, _core().db() as c:
        p = _provider(c,id) if id is not None else dict(id='provider-'+uuid.uuid4().hex,name='自定义服务',preset='custom',api_base='',model='',api_key='',promotion_url='')
        p.update({k:v for k,v in payload.items() if k not in ('api_key','clear_api_key','use_as_default')})
        p['api_base']=_url(p['api_base'])
        p['name']=_text(p['name'],80,False)
        p['preset']=_text(p['preset'],32,False)
        if p['preset'] not in ('deepseek','aliyun','siliconflow','custom'):
            raise ValueError('不支持的服务商预设')
        p['model']=_text(p['model'],200,False)
        p['promotion_url']=_url(p['promotion_url'],True)
        if 'api_key' in payload:
            key=_text(payload['api_key'],4096)
            if '\r' in key or '\n' in key: raise ValueError('密钥格式无效')
            if key: p['api_key']=key
        if payload.get('clear_api_key'):
            p['api_key']=''
            if p['id'] == 'provider-legacy':
                _disable_legacy_key(c)
        if payload.get('use_as_default') and not p.get('api_key'):
            raise ValueError('要用于创作，请填写Key；如暂不启用，可选择仅保存连接。')
        c.execute('INSERT INTO providers VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(p['id'],_dump(p)))
        if payload.get('use_as_default'):
            _set(c,'assignments',dict(topic_provider_id=p['id'],writing_provider_id=p['id']))
        return _public(p)


def delete_provider(id):
    with _core().LOCK, _core().db() as c:
        _provider(c,id)
        c.execute('DELETE FROM providers WHERE id=?',(id,))
        if id == 'provider-legacy':
            _disable_legacy_key(c)
        a=_setting(c,'assignments',dict(topic_provider_id=None,writing_provider_id=None))
        cleared=[k for k,v in a.items() if v==id]
        for k in cleared: a[k]=None
        _set(c,'assignments',a)
        return dict(deleted=True,id=id,assignments=a,cleared_assignments=cleared)


def save_assignments(payload):
    _object(payload, {'topic_provider_id','writing_provider_id'})
    with _core().LOCK, _core().db() as c:
        a=_setting(c,'assignments',dict(topic_provider_id=None,writing_provider_id=None))
        for key,pid in payload.items():
            if pid is not None: _provider(c,pid)
            a[key]=pid
        _set(c,'assignments',a)
        return a


def state():
    with _core().LOCK, _core().db() as c:
        active=_setting(c,'active_workspace_id')
        a=_setting(c,'assignments',dict(topic_provider_id=None,writing_provider_id=None))
    return dict(brand=BRAND,directions=directions(),workspaces=list_workspaces(),active_workspace_id=active,
                providers=list_providers(),provider_presets=copy.deepcopy(PRESETS),assignments=a)


def effective_config(workspace_id):
    core=_core()
    with core.LOCK:
        cfg=core.config(True)
        with core.db() as c:
            w=_workspace(c,workspace_id)
            cfg.update({k:v for k,v in w['profile'].items() if k in PROFILE_FIELDS})
            if w['domain'] != 'finance':
                for key in FINANCE_ONLY: cfg.pop(key,None)
            a=_setting(c,'assignments',None)
            stages={}
            for stage in ('topic','writing'):
                pid=(a or {}).get(stage+'_provider_id')
                if pid:
                    p=_provider(c,pid)
                    stages[stage]=dict(api_base=p['api_base'],model=p['model'],api_key=p.get('api_key',''),provider_mode='openai' if p.get('model') and p.get('api_key') else 'facts')
                elif a is None:
                    stages[stage]={k:cfg.get(k,'') for k in ('api_base','model','api_key')}
                    stages[stage]['provider_mode']='openai' if cfg.get('provider_mode') == 'openai' and cfg.get('api_key') and cfg.get('model') else 'facts'
                else:
                    stages[stage]=dict(api_base='',model='',api_key='',provider_mode='facts')
            cfg.update(stages['writing'])
            cfg['_topic_config']=stages['topic']
            cfg['_writing_config']=stages['writing']
            cfg['_domain']=w['domain']
            cfg['workspace_id']=workspace_id
            return cfg


def test_provider(id):
    """Only explicit user testing should invoke this; one small request, no retries."""
    with _core().LOCK, _core().db() as c:
        p=_provider(c,id)
    try:
        headers={'Authorization':'Bearer '+p['api_key']} if p.get('api_key') else {}
        base=p['api_base'].rstrip('/')
        if base.endswith('/chat/completions'):
            endpoint=base
        elif base.endswith('/v1') or urlsplit(base).hostname == 'api.deepseek.com':
            endpoint=base+'/chat/completions'
        else:
            endpoint=base+'/v1/chat/completions'
        raw=_core().request(endpoint,
                            dict(model=p['model'],messages=[dict(role='user',content='Reply OK')],max_tokens=8),
                            headers=headers,timeout=15)
        result=json.loads(raw)
        connected=type(result) is dict and bool(result.get('choices')) and not result.get('error')
    except Exception:
        connected=False
    return dict(id=id,connected=bool(connected),error='' if connected else '连接测试失败；请检查地址、模型ID、密钥或本机服务。未回显上游内容。')
