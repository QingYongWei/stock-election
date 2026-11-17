from __future__ import annotations
import copy, datetime as dt, hashlib, io, json, math, os, re, sqlite3, threading, urllib.request, urllib.parse, uuid, zipfile
from contextlib import contextmanager, closing
from threading import Thread as ProducerThread
import subprocess, sys
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('STOCK_AGENT_DATA', str(ROOT / 'data' / 'stock_agent'))).resolve()
DEFAULT = dict(brand='财经笔记', goal='从公开资料出发解释财经问题，区分事实、观点与不确定性', audience='没有时间逐条整理盘后信息的读者', tone='克制、清楚、事实与解释分开', stock_list='600519,300750,002594', schedule_time='18:00', schedule_enabled=False, weekly_day=4, provider_mode='facts', api_base='https://api.deepseek.com', model='', api_key='', dsa_report_dir='', dsa_run_enabled=False, dsa_repo='', dsa_python='', dsa_timeout=600, timezone='Asia/Shanghai')
LOCK = threading.RLock()
RUN_LOCK = threading.Lock()

def now(): return dt.datetime.now(TZ)
def stamp(): return now().isoformat(timespec='seconds')
def dumps(v): return json.dumps(v, ensure_ascii=False)
@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATA / 'agent.db', timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def _backup_legacy_database():
    source=DATA/'agent.db'; target=DATA/'agent.pre-platform-v030.backup'
    if not source.is_file() or target.exists():return
    with closing(sqlite3.connect(source,timeout=10)) as original:
        if original.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_settings'").fetchone():return
        with closing(sqlite3.connect(target)) as backup:original.backup(backup)
    try:os.chmod(target,0o600)
    except OSError:pass

def init():
    _backup_legacy_database()
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS schedules (key TEXT PRIMARY KEY, job_id TEXT, created_at TEXT)')
        had_config=bool(c.execute('SELECT 1 FROM config WHERE id=1').fetchone())
        c.execute('INSERT OR IGNORE INTO config VALUES (1,?)', (dumps(DEFAULT),))
        from . import platform
        platform.initialize(c,legacy=had_config)
        for row in c.execute('SELECT id,value FROM jobs').fetchall():
            previous=json.loads(row['value'])
            if previous.get('status')=='running':
                previous.update(status='failed',error='服务重启中断了此任务，请重新生成；未伪造成功。',quality_summary='任务中断，可手动重试')
                c.execute('UPDATE jobs SET value=? WHERE id=?',(dumps(previous),row['id']))
    try: os.chmod(DATA, 0o700)
    except OSError: pass

def config(secret=False):
    with db() as c: row = c.execute('SELECT value FROM config WHERE id=1').fetchone()
    v = {**DEFAULT, **json.loads(row[0])}
    if not secret:
        v['has_api_key'] = bool(v.pop('api_key', ''))
    return v

def save_config(payload):
    with LOCK:return _save_config_locked(payload)

def _save_config_locked(payload):
    if not isinstance(payload, dict): raise ValueError('配置必须为JSON对象')
    v = config(True)
    for k in DEFAULT:
        if k in payload and k != 'api_key': v[k] = payload[k]
    if payload.get('clear_api_key'): v['api_key'] = ''; v['provider_mode'] = 'facts'
    elif payload.get('api_key'): v['api_key'] = str(payload['api_key']).strip()
    for k in ('brand','goal','audience','tone','stock_list','schedule_time','provider_mode','api_base','model','dsa_report_dir','dsa_repo','dsa_python'):
        if not isinstance(v[k], str) or len(v[k]) > 4000: raise ValueError('配置字段类型或长度无效: '+k)
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',v['schedule_time']): raise ValueError('定时时间格式为 HH:MM')
    if type(v['schedule_enabled']) is not bool: raise ValueError('定时开关必须为布尔值')
    if type(v['dsa_run_enabled']) is not bool: raise ValueError('DSA运行开关必须为布尔值')
    if type(v['dsa_timeout']) is not int or not 30<=v['dsa_timeout']<=1800: raise ValueError('DSA超时范围30至1800秒')
    if v['dsa_run_enabled']:
        if not Path(v['dsa_repo']).joinpath('main.py').is_file(): raise ValueError('DSA项目目录必须存在main.py')
        if not Path(v['dsa_python']).is_file(): raise ValueError('请填写已安装DSA依赖的Python解释器完整路径')
        if v['dsa_report_dir'] and Path(v['dsa_report_dir']).resolve()!=(Path(v['dsa_repo'])/'reports').resolve():raise ValueError('启用DSA运行时，报告目录必须为该DSA项目的reports，不能指向其他旧目录')
    if type(v['weekly_day']) is not int or v['weekly_day'] not in range(7): raise ValueError('每周日期范围0至6')
    if v['provider_mode'] not in ('facts','openai'): raise ValueError('不支持的生成模式')
    codes(v['stock_list'])
    if v['provider_mode']=='openai':
        u=urllib.parse.urlparse(v['api_base'])
        if u.scheme not in ('http','https') or not u.netloc or u.username or u.password or u.query or u.fragment: raise ValueError('模型地址必须是有效HTTP(S) Base URL，不包含密钥或查询参数')
        if not v['api_key'] or not v['model'].strip(): raise ValueError('AI模式需要模型名称和API密钥')
    from . import platform
    fields=('brand','goal','audience','tone','stock_list','schedule_time','schedule_enabled','weekly_day','dsa_report_dir','dsa_run_enabled','dsa_repo','dsa_python','dsa_timeout')
    with db() as c:
        row=c.execute("SELECT value FROM workspaces WHERE id='workspace-finance'").fetchone()
        if row:
            changes=platform._profile({k:v[k] for k in fields},'finance')
            if not changes['brand']:raise ValueError('品牌不能为空')
            w=json.loads(row[0]);w['profile'].update(changes);w['name']=changes['brand']
            c.execute("UPDATE workspaces SET value=? WHERE id='workspace-finance'",(dumps(w),))
        c.execute('UPDATE config SET value=? WHERE id=1',(dumps(v),))
        if any(k in payload for k in ('api_key','clear_api_key','api_base','model','provider_mode')):
            migrated=c.execute("SELECT value FROM providers WHERE id='provider-legacy'").fetchone()
            if migrated:
                provider=json.loads(migrated[0]);provider.update(api_base=v['api_base'],model=v['model'],api_key=v['api_key'] if v['provider_mode']=='openai' else '')
                c.execute("UPDATE providers SET value=? WHERE id='provider-legacy'",(dumps(provider),))
    return config()

def codes(raw):
    result=[]
    for x in re.split(r'[,，\s]+', raw.strip()):
        if not x: continue
        if re.fullmatch(r'\d{6}',x): x=('sh' if x[0] in '569' else 'sz')+x
        if not re.fullmatch(r'(sh|sz|bj)\d{6}',x): raise ValueError('首版支持A股6位代码或sh/sz/bj前缀: '+x)
        if x not in result: result.append(x)
    if len(result)>20: raise ValueError('首版最多20个观察对象')
    return result

def put(job):
    with LOCK, db() as c: c.execute('INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(job['id'],dumps(job)))

def get(jid):
    if not re.fullmatch(r'[a-f0-9]{16}',jid): raise KeyError('任务不存在')
    with db() as c: row=c.execute('SELECT value FROM jobs WHERE id=?',(jid,)).fetchone()
    if not row: raise KeyError('任务不存在')
    return json.loads(row[0])

def jobs(workspace_id=None):
    with db() as c:
        if workspace_id is None:rows=c.execute("SELECT value FROM jobs ORDER BY json_extract(value,'$.created_at') DESC, rowid DESC LIMIT 200").fetchall()
        else:rows=c.execute("SELECT value FROM jobs WHERE COALESCE(json_extract(value,'$.workspace_id'),'workspace-finance')=? ORDER BY json_extract(value,'$.created_at') DESC,rowid DESC LIMIT 200",(workspace_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]

def public_job(j):
    result={k:v for k,v in j.items() if k not in ('config_snapshot',)}
    result.setdefault('workspace_id','workspace-finance');result.setdefault('domain','finance')
    ready=bool(j.get('status') not in ('running','failed') and j.get('content_ready') and j.get('engine_mode')=='ai_editorial' and j.get('editorial',{}).get('version')==2)
    result['content_ready']=ready
    result['publication']=dict(ready=ready,reason='' if ready else '尚未生成新版AI选题与分析；这是资料包或旧版记录，不是新版可发布成稿。请配置模型后重新生成。')
    if not ready and j.get('status') in ('review_required','approved'):
        result['legacy_status']=j['status'];result['status']='materials_ready'
    return result

def request(url, payload=None, headers=None, timeout=22, encoding='utf-8'):
    hs={'User-Agent':'StockContentAgent/0.1 (personal editorial research)', **(headers or {})}
    data=None if payload is None else dumps(payload).encode()
    if data is not None: hs['Content-Type']='application/json'
    req=urllib.request.Request(url,data=data,headers=hs)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(4*1024*1024+1)
    if len(raw)>4*1024*1024: raise ValueError('上游响应过大')
    return raw.decode(encoding,errors='replace')

def quote_data(stock_list):
    targets=['sh000001','sz399001','sz399006']+codes(stock_list)
    targets=list(dict.fromkeys(targets))
    url='https://qt.gtimg.cn/q='+','.join(targets)
    text=request(url,encoding='gb18030')
    rows=[]
    for token,body in re.findall(r'v_(\w+)="([^"]*)";',text):
        p=body.split('~')
        if len(p)<38 or not p[30]: continue
        try:
            n=dt.datetime.strptime(p[30],'%Y%m%d%H%M%S').replace(tzinfo=TZ)
            if n>now()+dt.timedelta(minutes=5): continue
            values=[float(p[i]) for i in (3,31,32,33,34)]
            if not all(math.isfinite(x) for x in values) or values[0]<=0:continue
            rows.append(dict(code=token,name=p[1],price=values[0],change=values[1],pct=values[2],high=values[3],low=values[4],timestamp=n.isoformat(timespec='seconds'),date=n.date().isoformat(),is_index=token in targets[:3]))
        except (ValueError,IndexError): continue
    if not rows: raise ValueError('未取得有效真实行情；未使用模拟数据代替')
    return rows,dict(title='腾讯行情接口：本次请求快照',url=url,published_at=stamp(),kind='market')

def announcements():
    url='https://np-anotice-stock.eastmoney.com/api/security/ann?'+urllib.parse.urlencode(dict(sr=-1,page_size=30,page_index=1,ann_type='A',client_source='web',f_node=0,s_node=0))
    raw=json.loads(request(url))
    out=[]
    for r in raw.get('data',{}).get('list',[]):
        ds=r.get('display_time','')[:19]
        try: published=dt.datetime.strptime(ds,'%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
        except ValueError: continue
        if published>now() or (now()-published).days>5: continue
        cs=r.get('codes',[])
        if not cs: continue
        code=cs[0].get('stock_code',''); art=r.get('art_code','')
        if not re.fullmatch(r'\d{6}',code) or not re.fullmatch(r'AN\d+',art): continue
        out.append(dict(title=r.get('title','')[:250],url=f'https://data.eastmoney.com/notices/detail/{code}/{art}.html',published_at=published.isoformat(),code=code,kind='announcement',note='仅采集公告索引标题，正文尚未逐条核验，不据标题推断业绩或交易结论'))
    return out

def dsa_reports(cfg):
    directory=cfg.get('dsa_report_dir','').strip()
    if not directory: return [], []
    root=Path(directory).expanduser().resolve()
    if not root.is_dir(): return [],[dict(level='warning',message='DSA报告目录不存在，继续使用真实行情事实底稿。')]
    bound=cfg.get('_dsa_current_reports')
    if bound is not None:
        found=[]
        for item in bound:
            p=Path(item['path'])
            if p.is_symlink() or p.resolve().parent!=root or p.suffix.lower()!='.md':raise ValueError('DSA产物清单包含非预期报告路径')
            s=p.stat()
            if (s.st_mtime_ns,s.st_size)!=(item['mtime_ns'],item['size']):raise ValueError('DSA报告在本次运行后发生变动，拒绝读取不一致版本')
            found.append(p)
    else:found=sorted([p for p in root.glob('*.md') if p.is_file() and not p.is_symlink()],key=lambda p:p.stat().st_mtime,reverse=True)[:3]
    reports=[]
    for p in found:
        if p.stat().st_size>500000: continue
        before=p.stat()
        text=p.read_text(encoding='utf-8-sig',errors='replace')[:18000]
        after=p.stat()
        if bound is not None and (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size):raise ValueError('DSA报告读取中发生变化，请串行运行')
        reports.append(dict(name=p.name,modified_at=dt.datetime.fromtimestamp(p.stat().st_mtime,TZ).isoformat(),text=text))
    if bound is not None and not reports:raise ValueError('DSA本次产物不可读取或超出大小限制')
    warnings=[dict(level='warning',message='DSA报告仅作为内部线索，可能包含过期数据或建议；原始买卖字段不直接公开。')]
    if not reports: warnings.append(dict(level='warning',message='DSA目录未找到Markdown报告。'))
    return reports,warnings

def run_dsa_bridge(cfg):
    if not cfg.get('dsa_run_enabled'):return []
    bridge=ROOT/'tools'/'run_dsa.py'
    if not bridge.is_file():raise ValueError('DSA运行桥接脚本缺失')
    root=(Path(cfg['dsa_repo'])/'reports').resolve()
    if cfg.get('dsa_report_dir') and Path(cfg['dsa_report_dir']).resolve()!=root:raise ValueError('DSA运行目录与导入报告目录不一致')
    manifests=DATA/'dsa-runs';manifests.mkdir(parents=True,exist_ok=True)
    manifest=manifests/(uuid.uuid4().hex+'.json')
    command=[sys.executable,str(bridge),'--repo',cfg['dsa_repo'],'--python',cfg['dsa_python'],'--stocks',','.join(x[2:] for x in codes(cfg['stock_list'])),'--timeout',str(cfg['dsa_timeout']),'--manifest-out',str(manifest)]
    env=os.environ.copy()
    if cfg.get('provider_mode')=='openai' and cfg.get('api_key'):
        env.update(OPENAI_API_KEY=cfg['api_key'],OPENAI_BASE_URL=cfg['api_base'],LITELLM_MODEL='openai/'+cfg['model'],GENERATION_BACKEND='litellm',MAX_WORKERS='1')
    try:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=cfg['dsa_timeout']+20,check=False)
        if result.returncode:
            raise ValueError('DSA运行失败（退出码 '+str(result.returncode)+'）；未使用旧报告冒充本次成功。请用命令行桥接诊断依赖和模型配置。')
    except subprocess.TimeoutExpired:
        raise ValueError('DSA运行超时；任务未被标记成功，请检查DSA日志和进程。')
    if not manifest.is_file():raise ValueError('DSA未返回本次产物清单，不能使用旧报告')
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if Path(data.get('repo','')).resolve()!=Path(cfg['dsa_repo']).resolve() or not data.get('reports'):raise ValueError('DSA产物清单不匹配本次项目')
    cfg['_dsa_current_reports']=data['reports']
    return [dict(level='info',message='已执行DSA，并绑定本次产物清单；原生报告作为内部研究线索，公开数字仍以事实底稿核对。')]

def collect(cfg,kind):
    checks=run_dsa_bridge(cfg)
    if cfg.get('dsa_run_enabled') and not cfg.get('dsa_report_dir'):
        cfg={**cfg,'dsa_report_dir':str(Path(cfg['dsa_repo'])/'reports')}
    quotes,source=quote_data(cfg['stock_list'])
    try: news=announcements()
    except Exception:
        news=[]; checks.append(dict(level='warning',message='公告索引暂不可用，本稿不补造事件。'))
    reports,dschecks=dsa_reports(cfg); checks+=dschecks
    market_date=max(q['date'] for q in quotes)
    if market_date!=now().date().isoformat(): checks.append(dict(level='warning',message='行情为最近可获取日期 '+market_date+'，不是今日；已在稿件中标注，不冒充当天行情。'))
    if any(q['date']!=market_date for q in quotes): checks.append(dict(level='warning',message='观察对象行情日期不完全一致，逐项显示日期。'))
    if now().hour<15 and market_date==now().date().isoformat():checks.append(dict(level='warning',message='当前尚未收盘，本次不是最终盘后稿。'))
    if any(q.get('timestamp','')[11:16]<'15:00' for q in quotes): checks.append(dict(level='warning',message='至少一项行情时间早于15:00，是盘中/旧快照，不可当作最终收盘数据；稿件逐项显示时间。'))
    expected=set(['sh000001','sz399001','sz399006']+codes(cfg['stock_list']))
    missing=expected-{q['code'] for q in quotes}
    if missing:checks.append(dict(level='warning',message='未取得以下对象行情，未补造：'+','.join(sorted(missing))))
    checks.append(dict(level='info',message='行情来自真实网络请求；公告仅为索引标题，需人工核对原文；来源使用和商业展示权限需自行确认。'))
    history=[]
    if kind=='weekly':
        monday=now().date()-dt.timedelta(days=now().weekday())
        seen=set()
        candidates=sorted(jobs(cfg.get('_workspace_id','workspace-finance')),key=lambda j:(min((q.get('timestamp','') for q in j.get('facts',{}).get('quotes',[]) if q.get('is_index')),default=''),j.get('facts',{}).get('collected_at',''),j.get('created_at','')),reverse=True)
        for j in candidates:
            date=j.get('market_date','');items=j.get('facts',{}).get('quotes',[])
            closed=items and all(q.get('timestamp','')[11:16]>='15:00' for q in items if q.get('is_index'))
            if j.get('kind')=='daily' and j.get('status') not in ('failed','running') and monday.isoformat()<=date<=now().date().isoformat() and date not in seen and closed:
                history.append(dict(date=date,quotes=items));seen.add(date)
        history.sort(key=lambda h:h['date'])
        checks.append(dict(level='warning',message=f'本周已积累{len(history)}个日期的日报快照；周报仅使用已有材料，不计算缺失周行情或虚构周涨跌。'))
    return dict(collected_at=stamp(),market_date=market_date,quotes=quotes,announcements=news[:5],dsa_reports=reports,history=history,recent_topics=[dict(date=j.get('market_date'),title=j['editorial'].get('selected_topic',{}).get('title',''),thesis=j['editorial'].get('analysis',{}).get('thesis','')) for j in jobs(cfg.get('_workspace_id','workspace-finance')) if j.get('content_ready') and j.get('editorial')][:8],sources=[source]+news[:5]),checks

def signed(x): return f'{x:+.2f}'
def compose_facts(cfg,facts,kind):
    date=facts['market_date']; brand=cfg['brand']; weekly=kind=='weekly'
    postclose=all(q.get('timestamp','')[11:16]>='15:00' for q in facts['quotes'])
    title=f'{date}｜'+('本周资料复盘：已有事实与待核验事项' if weekly else ('盘后市场速览：指数表现与公告线索' if postclose else '盘中/旧时点行情快照：尚非最终收盘'))
    qs=facts['quotes']; indices=[q for q in qs if q['is_index']]
    # Deterministic editorial selection uses only acquired observations.
    focus=max(indices,key=lambda q:abs(q['pct']),default=None)
    summary=(f'本次快照中，已取得指数里{focus["name"]}的涨跌幅绝对值最大，为{signed(focus["pct"])}%。这只是行情描述，不代表未来走势。' if focus else '本次未取得有效指数数据，只保留已取得的观察对象资料，不据此总结大盘走势。')
    body=[f'# {title}',f'> {brand} · 数据采集于 {facts["collected_at"]}。行情日期：{date}。本稿为真实数据驱动的规则事实稿，未调用大模型。', '', '## 今日主线',summary,'', '## 市场与观察对象快照','| 对象 | 代码 | 价格/点位 | 涨跌幅 | 行情时点 |','| --- | --- | --- | --- | --- |']
    for q in qs: body.append(f'| {q["name"]} | {q["code"]} | {q["price"]:.2f} | {signed(q["pct"])}% | {q.get("timestamp",q["date"])} |')
    body+=['','以上为接口快照，不以其推断资金意图、买卖时机或收益。指数与个股口径不同，不合并计算。']
    if weekly:
        body+=['','## 本周材料覆盖',f'当前归档包含本周{len(facts["history"])}个日期的日报快照。缺失日期不补造；当前价格与当日涨跌幅不能当成整周涨跌。']
        for h in facts['history']: body.append('- '+h['date']+'：'+ '；'.join(f'{q["name"]} {signed(q["pct"])}%' for q in h['quotes'] if q['is_index']))
    body+=['','## 公告检索线索（标题级，待核验原文）']
    if facts['announcements']:
        for a in facts['announcements']: body += [f'- {a["title"]}（索引展示时间：{a["published_at"]}）',f'  原文入口：{a["url"]}']
    else: body+=['本次没有取得有效公告索引，不据此声称当天没有公告。']
    body+=['','## 审核与后续核验','- 公告索引只说明存在该标题，不能替代阅读公告正文。','- 核对数字、时点与适用市场；晚于截止时间的消息不混入当时结论。','- 本文不提供具体证券交易指令或保证收益。','', '## 来源']
    for i,s in enumerate(facts['sources'],1):body.append(f'{i}. {s["title"]} — {s["url"]}')
    body+=['',f'资料整理：{brand}。规则自动生成，发布前须人工审核；如经AI改写应按平台要求声明。']
    xhs=f'{date} 市场快照｜先看事实，再做判断\n\n'+'\n'.join(f'• {q["name"]}：{q["price"]:.2f}，{signed(q["pct"])}%（{q.get("timestamp",q["date"])}）' for q in indices)+'\n\n公告标题只是线索，不代表正文已核验。不要把当日涨跌理解为未来预测。\n行情日期：'+date+'\n真实行情规则生成，非AI荐股；发布前人工审核。\n\n#市场复盘 #公开信息 #数据记录'
    return dict(wechat_title=title,wechat_markdown='\n'.join(body),xhs_title=f'{date}｜'+('本周资料复盘' if weekly else ('盘后市场事实速览' if postclose else '盘中/旧时点行情快照')),xhs_text=xhs)

def _routed_request(cfg):
    stage=[0]
    def routed(url,payload=None,headers=None,timeout=22,**kwargs):
        provider=cfg.get('_topic_config' if stage[0]==0 else '_writing_config');stage[0]+=1
        if not provider:return request(url,payload,headers=headers,timeout=timeout,**kwargs)
        base=provider['api_base'].rstrip('/')
        if base.endswith('/chat/completions'):endpoint=base
        elif base.endswith('/v1') or urllib.parse.urlsplit(base).hostname=='api.deepseek.com':endpoint=base+'/chat/completions'
        else:endpoint=base+'/v1/chat/completions'
        data={**(payload or {}),'model':provider['model']}
        hs={**(headers or {}),'Authorization':'Bearer '+provider['api_key']}
        return request(endpoint,data,headers=hs,timeout=timeout,**kwargs)
    return routed

def llm_compose(cfg,facts,baseline=None,note=''):
    from .editorial import generate
    return generate(cfg,facts,'weekly' if facts.get('kind')=='weekly' else 'daily',note=note,request_fn=_routed_request(cfg))

def validate_content(content,facts,card_plan=None):
    if facts.get('domain','finance')!='finance':
        from .generic import validate_content as validate_generic
        return validate_generic(content,facts,card_plan)
    checks=[]
    txt='\n'.join(content.values())
    if card_plan:txt+='\n'+'\n'.join(str(c.get('title',''))+'\n'+str(c.get('body','')) for c in card_plan)
    if facts['market_date'] not in txt: checks.append(dict(level='error',message='稿件缺少实际行情日期'))
    if any(q.get('timestamp','')[11:16]<'15:00' for q in facts['quotes']) and ('盘后' in content['wechat_title'] or '盘后' in content['xhs_title']):
        checks.append(dict(level='error',message='行情包含早于15:00的快照，标题不得标成最终盘后稿；请重新采集或明确盘中时点。'))
    screened = txt
    for disclaimer in ('本文不提供具体证券交易指令或保证收益','不保证收益','不承诺收益','不提供保证收益','不提供内幕消息','不提供买卖点、目标价、仓位或收益承诺'):
        screened = screened.replace(disclaimer, '')
    if re.search(r'(保证收益|稳赚|必涨|内幕消息|建议买入|建议卖出|目标价|止损价|仓位\d)',screened): checks.append(dict(level='error',message='发现交易指令或确定收益类表述，需修改后重新校验'))
    if 'http' not in content['wechat_markdown']:checks.append(dict(level='warning',message='公众号稿未包含来源链接，请人工补齐'))
    checks.append(dict(level='info',message='程序检查不等于事实或合规审查，公开稿仍须人工确认。'))
    return checks

def render_cards(job):
    from PIL import Image, ImageDraw, ImageFont
    folder=DATA/'artifacts'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    fonts=[os.environ.get('STOCK_AGENT_FONT',''),r'C:\Windows\Fonts\msyh.ttc','/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    fontpath=next((p for p in fonts if p and Path(p).is_file()),None)
    if not fontpath: raise ValueError('缺少可用字体，请设置 STOCK_AGENT_FONT')
    def font(size):return ImageFont.truetype(fontpath,size)
    plan=job.get('editorial',{}).get('card_plan') if job.get('content_ready') else None
    if plan:
        sections=[(c['title'],c['body']) for c in plan]
    else:
        indices=[q for q in job['facts']['quotes'] if q['is_index']]
        sections=[('资料预览，不是AI成稿','还没有完成每日选题与观点分析。\n\n请在账号与AI设置中配置模型，然后重新生成。'),('已采集行情','\n\n'.join(f'{q["name"]} {q["price"]:.2f}\n{signed(q["pct"])}% · {q.get("timestamp",q["date"])[11:19]}' for q in indices)),('已收集公告线索','\n\n'.join(a['title'][:65]+('…' if len(a['title'])>65 else '') for a in job['facts']['announcements'][:3]) or '本次未取得公告索引，不补造事件。'),('下一步：启用AI创作','配置模型 → AI推荐选题 → 自动选择主推 → 生成有证据的文章与观点图卡。\n\n资料包不能直接批准为成稿。')]
    def fit_rows(text,f,width=850):
        lines=[]
        for paragraph in re.sub(r'\n{3,}','\n\n',text).split('\n'):
            line=''
            for ch in paragraph:
                if line and f.getlength(line+ch)>width:lines.append(line);line=''
                line+=ch
            lines.append(line)
        return lines
    rendered=[]
    for i,(title,body) in enumerate(sections,1):
        im=Image.new('RGB',(1080,1440),'#0c1c2a');d=ImageDraw.Draw(im)
        d.rounded_rectangle((60,65,1020,1375),radius=28,fill='#122d3c',outline='#245360',width=2)
        brand=job['config_snapshot']['brand'];brand=brand[:24]+('…' if len(brand)>24 else '')
        d.text((105,110),brand+' / '+('观点笔记' if plan else '仅资料'),font=font(25),fill='#5ed5bb')
        y=200
        for line in fit_rows(title,font(52)):
            d.text((105,y),line,font=font(52),fill='#ffffff');y+=76
        d.line((105,y+15,965,y+15),fill='#315c65',width=3);y+=66
        for size in (38,36,34,32,30,28,26,24):
            f=font(size);lines=fit_rows(body,f);step=int(size*1.5)
            if y+len(lines)*step<=1210:break
        else:raise ValueError('图卡文字超出画布，请精简正文或换行；未截断内容')
        for line in lines:d.text((105,y),line,font=f,fill='#d3e1e6');y+=step
        d.text((105,1265),f'{job["market_date"]}    {i:02d} / 04',font=font(27),fill='#78a8b0')
        d.text((105,1320),'AI辅助 · 论点与证据需人工审核' if plan else '资料包 · 尚未生成AI选题与分析',font=font(24),fill='#78a8b0')
        buffer=io.BytesIO();im.save(buffer,format='PNG');rendered.append((i,buffer.getvalue()))
    cards=[]
    for i,raw in rendered:
        path=folder/f'card-{i}.png';temporary=folder/f'card-{i}.new';temporary.write_bytes(raw);temporary.replace(path)
        cards.append(f'/artifacts/{job["id"]}/card-{i}.png')
    return cards

def start_job(kind='daily',note='',parent=None,scheduled_key=None,workspace_id=None):
    if kind not in ('daily','weekly'):raise ValueError('任务类型必须是daily或weekly')
    if not RUN_LOCK.acquire(blocking=False):raise ValueError('已有任务运行，请完成后再试')
    try:
        from . import platform
        if parent:
            original=get(parent);owner=original.get('workspace_id','workspace-finance')
            if workspace_id and workspace_id!=owner:raise ValueError('不能将原任务重生成到另一个工作空间')
            workspace_id=owner
        if workspace_id:
            workspace=platform.get_workspace(workspace_id);cfg=platform.effective_config(workspace_id)
            cfg['_workspace_id']=workspace_id;cfg['_domain']=workspace['domain'];cfg['profile']={k:v for k,v in workspace['profile'].items() if k in platform.PROFILE_FIELDS}
            cfg['_direction']=next(({'id':d['id'],'name':d['name'],'reader':d.get('reader',''),'work':d.get('work',''),'rules':d.get('rules','')} for d in platform.directions() if d['id']==workspace['direction_id']),{})
        else:
            cfg=config(True);workspace_id='workspace-finance';cfg['_workspace_id']=workspace_id;cfg['_domain']='finance'
            try:platform.get_workspace(workspace_id)
            except KeyError:
                platform.create_workspace({'direction_id':'finance'})
                platform.update_workspace(workspace_id,{'profile':{k:cfg[k] for k in ('brand','goal','audience','tone','stock_list','schedule_time','schedule_enabled','weekly_day','dsa_report_dir','dsa_run_enabled','dsa_repo','dsa_python','dsa_timeout')}})
        if cfg['_domain']=='finance':
            cfg=platform.effective_config(workspace_id);cfg['_workspace_id']=workspace_id;cfg['_domain']='finance'
        complete_model=cfg.get('provider_mode')=='openai' and bool(cfg.get('api_key')) and bool(cfg.get('model')) and all(stage.get('provider_mode')=='openai' and stage.get('api_key') and stage.get('model') for stage in (cfg.get('_topic_config',cfg),cfg.get('_writing_config',cfg)))
        if cfg['_domain']!='finance' and not complete_model:raise ValueError('此方向需要完整配置并分配选题与写作模型；预置样稿仅供预览，不会冒充实际生成。')
        if not complete_model:cfg['provider_mode']='facts'
        jid=uuid.uuid4().hex[:16]
        j=dict(id=jid,kind=kind,workspace_id=workspace_id,domain=cfg['_domain'],created_at=stamp(),status='running',title='正在采集真实数据…' if cfg['_domain']=='finance' else '正在推荐选题与创作…',quality_summary='采集 → 选题 → 写稿 → 制图 → 校验',error='',config_snapshot={k:v for k,v in cfg.items() if k!='api_key' and not k.startswith('_')},review_note=note[:2000],parent_id=parent,versions=[],cards=[],sources=[],checks=[],publish_url='',engine_mode=cfg['provider_mode'])
        put(j)
        if scheduled_key:
            with db() as c:c.execute('INSERT INTO schedules VALUES (?,?,?)',(scheduled_key,jid,stamp()))
        ProducerThread(target=produce,args=(jid,cfg),daemon=True).start()
        return public_job(j)
    except Exception:
        RUN_LOCK.release();raise

def produce(jid,cfg):
    j=get(jid)
    def stage(name,label):
        j.update(stage=name,stage_label=label);put(j)
    try:
        if cfg.get('_domain','finance')!='finance':
            from .generic import generate as generate_generic
            stage('editorial','AI推荐常青选题并创作；未进行实时外网检索')
            facts=dict(domain=cfg['_domain'],kind=j['kind'],market_date=now().date().isoformat(),collected_at=stamp(),quotes=[],announcements=[],sources=[],materials=cfg.get('materials',''))
            content,editorial,usage=generate_generic(cfg,facts,j['kind'],note=j['review_note'],request_fn=_routed_request(cfg))
            j.update(content);j.update(facts=facts,market_date=facts['market_date'],sources=editorial.get('source_catalog',[]),editorial=editorial,usage=usage,engine_mode='ai_editorial',content_ready=True,data_origin='AI通用知识与工作空间简报；非实时检索，例子与材料需人工核验')
            j['checks']=[{**x,'scope':'content'} for x in validate_content(content,facts,editorial.get('card_plan'))]
            if any(x['level']=='error' for x in j['checks']):raise ValueError('模型成稿未通过结构或领域检查，未交付可发布成稿')
            stage('cards','生成本领域观点图卡与发布预览');j['cards']=render_cards(j)
            j.update(status='review_required',title=j['wechat_title'],quality_summary=str(len(editorial['topics']))+'个AI选题 / 两平台稿件 / 4张图卡',stage='complete',stage_label='成稿待人工审核',versions=[dict(at=stamp(),action='generated',engine='ai_editorial')]);put(j);return
        stage('collect','采集真实行情、公告及可选DSA材料')
        facts,checks=collect(cfg,j['kind']);facts['kind']=j['kind']
        ai=cfg['provider_mode']=='openai' and bool(cfg.get('api_key')) and bool(cfg.get('model'))
        if ai:
            stage('research','补充已收盘多日数据与公告正文片段，建立证据目录')
            from .research import enrich
            facts,research_checks=enrich(facts,request);checks+=research_checks
        j.update(facts=facts,market_date=facts['market_date'],sources=facts['sources'],data_origin='Tencent live snapshot'+(' + historical daily prices + announcement excerpts when available' if ai else ' + Eastmoney announcement index')+(' + DSA Markdown clues' if facts['dsa_reports'] else ''),checks=checks,content_ready=False)
        content=compose_facts(cfg,facts,j['kind']);j.update(content)
        terminal_status='review_required' if ai else 'materials_ready'
        if ai:
            stage('editorial','AI推荐选题 → 自动选择主推题 → 形成论点与两平台内容')
            try:
                content,editorial,usage=llm_compose(cfg,facts,content,j['review_note'])
                j.update(content);j.update(editorial=editorial,usage=usage,engine_mode='ai_editorial',content_ready=True)
            except Exception:
                # No provider response, credential or unvalidated draft is exposed.
                terminal_status='needs_ai'
                j.update(engine_mode='facts',error='AI选题或分析生成未完成。资料已保留，但没有交付AI成稿；请检查模型配置、额度或按提示重试。')
                j['checks'].append(dict(level='error',scope='editorial',message=j['error']))
        else:
            j.update(engine_mode='facts')
            j['checks'].append(dict(level='warning',scope='editorial',message='尚未配置AI，当前只有采集资料，没有自动选题、观点分析或可发布成稿。'))
        j['checks'] += [{**x,'scope':'content'} for x in validate_content({k:j[k] for k in ('wechat_title','wechat_markdown','xhs_title','xhs_text')},facts,j.get('editorial',{}).get('card_plan'))]
        stage('cards','生成观点图卡与平台预览' if j['content_ready'] else '整理资料预览（非AI成稿）')
        j['cards']=render_cards(j)
        j['title']=j['wechat_title'] if j['content_ready'] else facts['market_date']+'｜资料已采集，待配置AI生成选题与分析'
        j['status']=terminal_status
        j['quality_summary']=(str(len(j['editorial']['topics']))+'个AI选题 / 已自动选定主推 / 4张观点图卡') if j['content_ready'] else '仅资料包，尚未完成AI创作'
        j.update(stage='complete',stage_label='成稿待审核' if j['content_ready'] else '资料已保存，AI成稿未完成')
        j['versions']=[dict(at=stamp(),action='generated' if j['content_ready'] else 'materials_collected',engine=j['engine_mode'])]
        put(j)
    except Exception as e:
        j.update(status='failed',content_ready=False,stage='failed',stage_label='任务失败，未交付成稿',title='生成失败：未交付伪造内容',error=f'{type(e).__name__}: '+safe_error(e),quality_summary='请检查网络、数据源或运行日志后重试')
        put(j)
    finally:RUN_LOCK.release()

def safe_error(e):
    if isinstance(e,ValueError):return str(e)[:180]
    return '上游请求或本地生成失败；敏感响应未记录'

def update_content(jid,payload):
    with LOCK:
        j=get(jid)
        if j['status'] in ('running','failed','published'):raise ValueError('当前任务不能编辑，请先生成或新建版本')
        old={k:j[k] for k in ('wechat_title','wechat_markdown','xhs_title','xhs_text')}
        new={}
        for k in old:
            v=payload.get(k,old[k])
            if not isinstance(v,str) or not v.strip() or len(v)>30000:raise ValueError('稿件字段不能为空或超长')
            new[k]=v
        if j.get('content_ready') and (len(new['xhs_title'])>20 or len(new['xhs_text'])>1000):raise ValueError('小红书标题最多20字，正文最多1000字，请精简后保存')
        previous_cards=copy.deepcopy(j.get('editorial',{}).get('card_plan',[]))
        if 'card_plan' in payload:
            plan=payload['card_plan'];known={s['id'] for s in j.get('editorial',{}).get('source_catalog',[])}
            if not j.get('content_ready') or not isinstance(plan,list) or len(plan)!=4:raise ValueError('只有AI成稿可修改4张观点图卡')
            for card in plan:
                if not isinstance(card,dict) or any(not isinstance(card.get(k),str) or not card[k].strip() or len(card[k])>limit for k,limit in [('title',28),('body',240)]):raise ValueError('图卡标题最多28字，正文最多240字且不能为空')
                refs=card.get('source_ids')
                if not isinstance(refs,list) or not refs or any(not isinstance(x,str) or x not in known for x in refs):raise ValueError('图卡来源必须来自本任务证据目录')
            j['editorial']['card_plan']=copy.deepcopy(plan)
        j['versions'].append(dict(at=stamp(),action='edited',previous=old,previous_cards=previous_cards))
        j.update(new);j['title']=j['wechat_title'];j['status']='review_required' if j.get('content_ready') else 'materials_ready'
        if j.get('editorial') and (new!=old or ('card_plan' in payload and payload['card_plan']!=previous_cards)):
            j['editorial']['analysis_stale']=True
            j['editorial']['manual_revision_at']=stamp()
        # Card plan is an independently editable publication artifact; saving revokes approval.
        j['checks']=[x for x in j['checks'] if x.get('scope')!='content']
        j['checks'] += [{**x,'scope':'content'} for x in validate_content(new,j['facts'],j.get('editorial',{}).get('card_plan'))]
        j['cards']=render_cards(j)
        put(j);return public_job(j)

def approval_digest(j):
    images=[]
    for url in j.get('cards',[]):
        name=url.rsplit('/',1)[-1]
        path=DATA/'artifacts'/j['id']/name
        images.append((name,hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(dumps(dict(content={k:j[k] for k in ('wechat_title','wechat_markdown','xhs_title','xhs_text')},images=images)).encode()).hexdigest()

def review(jid,payload):
    action=payload.get('action');note=str(payload.get('note',''))[:2000]
    with LOCK:
        j=get(jid)
        if j['status'] in ('failed','running'):raise ValueError('任务未完成，不能审核')
        if action=='approve':
            if not public_job(j)['publication']['ready']:raise ValueError('当前仅为采集资料，尚未生成AI选题与分析，不能批准为发布成稿。请配置模型后重新生成。')
            if j['status']=='published':raise ValueError('已发布任务不能重复批准')
            errors=validate_content({k:j[k] for k in ('wechat_title','wechat_markdown','xhs_title','xhs_text')},j['facts'],j.get('editorial',{}).get('card_plan'))
            if any(x['level']=='error' for x in errors):raise ValueError('稿件存在阻断项，请修改后再审核')
            if payload.get('confirm_cards') is not True:raise ValueError('请明确确认已核对全部图片；文字修改不会改变卡片中的行情数字与公告底稿。')
            j['status']='approved';j['approved_at']=stamp()
            j['approved_hash']=approval_digest(j)
            j['cards_confirmed_at']=stamp()
        elif action=='reject':
            if j['status']=='published':raise ValueError('已发布任务不能退回，请新建更正版本')
            j['status']='rejected'
        elif action=='publish':
            if not public_job(j)['publication']['ready']:raise ValueError('当前为资料包或旧版记录，未完成新版AI选题与分析，不能标记为已发布成稿')
            if j['status']!='approved':raise ValueError('必须先审核通过，才可记录已发布')
            if j.get('approved_hash')!=approval_digest(j):raise ValueError('正文或图片与批准版本不一致，请重新审核')
            url=str(payload.get('publish_url','')).strip()
            u=urllib.parse.urlparse(url)
            if u.scheme not in ('http','https') or not u.netloc:raise ValueError('请填写手工发布后的有效文章URL')
            j.update(status='published',publish_url=url,published_at=stamp(),publish_method='manual_record')
        else:raise ValueError('无效审核动作')
        j['review_note']=note;j['versions'].append(dict(at=stamp(),action=action,note=note));put(j)
        return public_job(j)

def images_zip(jid):
    with LOCK:
        j=get(jid)
        if j['status'] in ('running','failed') or len(j.get('cards',[]))!=4:raise ValueError('任务尚无完整四张图片')
        if j['status'] in ('approved','published') and j.get('approved_hash')!=approval_digest(j):raise ValueError('图片或文字与批准版本不一致，请重新审核')
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for i in range(1,5):z.write(DATA/'artifacts'/jid/f'card-{i}.png',f'{i:02d}.png')
            z.writestr('上传顺序.txt','按01到04顺序上传。图片'+('为AI辅助成稿，请核验论点与来源。' if j.get('content_ready') else '仅为资料预览，未生成AI观点，不能当作成稿发布。')+'本工具不会替你发布到平台。')
        return out.getvalue()

def export_zip(jid):
    with LOCK:return _export_zip_locked(jid)

def _export_zip_locked(jid):
    j=get(jid)
    if j['status'] in ('running','failed'):raise ValueError('任务尚无可导出内容')
    if j['status'] in ('approved','published') and j.get('approved_hash')!=approval_digest(j):raise ValueError('正文或图片与批准版本不一致，请重新审核')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        from .export import wechat_html
        z.writestr('wechat.md',j['wechat_markdown'])
        z.writestr('wechat.html',wechat_html(j['wechat_markdown'],j['wechat_title']))
        z.writestr('wechat-copy.html',wechat_html(j['wechat_markdown'],j['wechat_title'],for_clipboard=True))
        z.writestr('editorial.json',dumps(j.get('editorial',{})))
        z.writestr('发布步骤.md','# 发布步骤\n\n'+('这是AI辅助成稿，仍须核对事实、论点与全部图片。' if j.get('content_ready') else '**当前只是资料包，未生成AI选题或分析，不要直接作为成稿发布。**')+'\n\n## 公众号\n1. 在工作台公众号页复制标题，填写公众号后台标题。\n2. 点击复制排版正文，粘贴进公众号正文编辑器；若浏览器不支持富文本复制，用排版预览手动选择复制。\n3. 在公众号后台补封面并预览，确认排版后由你发布。复制不等于已经发布。\n\n## 小红书\n1. 按card-1到card-4顺序上传PNG。\n2. 在工作台分别复制小红书标题和正文，粘贴到发布页。\n3. 检查图片、文字、平台要求后由你发布。\n\n平台可能过滤粘贴样式，必须以平台最终预览为准。')
        z.writestr('xiaohongshu.txt',j['xhs_title']+'\n\n'+j['xhs_text'])
        z.writestr('sources.json',dumps(j['sources']))
        z.writestr('review.json',dumps(dict(status=j['status'],checks=j['checks'],engine_mode=j['engine_mode'],data_origin=j['data_origin'],market_date=j['market_date'],review_note=j['review_note'],content_ready=bool(j.get('content_ready')),publication=public_job(j)['publication'])))
        z.writestr('READ-ME.txt','这是内容包，不是已完成的平台发布。状态：'+j['status']+'。发布前审核文字和全部图片；来源授权、事实和适用领域边界需自行核实。财经资料采集与非财经常青创作是不同路径。')
        for p in (DATA/'artifacts'/jid).glob('card-*.png'):z.write(p,'cards/'+p.name)
    return out.getvalue()

SCHEDULER_STATE=dict(enabled=False,last_tick='',last_schedule_error='')
STOP=threading.Event()
def workspace_scheduler_tick(t):
    from . import platform
    for w in platform.list_workspaces():
        if w['domain']=='finance':continue
        profile=w['profile']
        if not profile.get('schedule_enabled') or t.strftime('%H:%M')<profile.get('schedule_time','18:00'):continue
        SCHEDULER_STATE['enabled']=True
        kinds=['daily']+(['weekly'] if t.weekday()==profile.get('weekly_day',4) else [])
        for kind in kinds:
            key=w['id']+':'+t.date().isoformat()+':'+kind
            with db() as c:exists=c.execute('SELECT 1 FROM schedules WHERE key=?',(key,)).fetchone()
            if exists:continue
            if RUN_LOCK.locked():return
            try:start_job(kind,scheduled_key=key,workspace_id=w['id'])
            except ValueError:
                SCHEDULER_STATE['last_schedule_error']='工作空间 '+w['name']+' 未启动：请检查模型配置或运行中任务。'
                return

def scheduler_tick(at=None):
    cfg=config(True);t=at or now();SCHEDULER_STATE.update(enabled=cfg['schedule_enabled'],last_tick=stamp(),last_schedule_error='')
    workspace_scheduler_tick(t)
    if not cfg['schedule_enabled'] or t.strftime('%H:%M')<cfg['schedule_time']:return
    # Weekday guard is not an exchange holiday calendar. Real quote freshness is checked in output.
    kinds=[]
    if t.weekday()<5:kinds.append('daily')
    if t.weekday()==cfg['weekly_day']:kinds.append('weekly')
    for kind in kinds:
        key=t.date().isoformat()+':'+kind
        with db() as c:exists=c.execute('SELECT 1 FROM schedules WHERE key=?',(key,)).fetchone()
        if exists:continue
        if RUN_LOCK.locked():break
        try:start_job(kind,scheduled_key=key)
        except ValueError:break

def scheduler_loop():
    while not STOP.wait(20):
        try:scheduler_tick()
        except Exception as e:SCHEDULER_STATE['last_schedule_error']=type(e).__name__+'：请检查配置与任务记录'
