"""Bounded factual research enrichment for evidence-led editorial generation.

External text is untrusted source material. No model, keys, platform posting or DSA
processes are invoked here. Derived signals are descriptive, not trading advice.
"""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from html import unescape
from html.parser import HTMLParser
import json
import math
import re
import urllib.parse

class TextOnly(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
        elif tag in ('p','br','div','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)

def plain_text(raw):
    p=TextOnly();p.feed(raw)
    return re.sub(r'[ \t\r\f\v]+',' ',unescape(''.join(p.parts))).strip()

def historical_signal(quote,request_fn,cutoff):
    code=quote['code']
    url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urllib.parse.urlencode({'param':f'{code},day,,,45,qfq'})
    data=json.loads(request_fn(url,timeout=12)).get('data',{}).get(code,{})
    rows=data.get('qfqday') or data.get('day') or []
    clean={}
    # Never call an unfinished current-day candle a closing observation.
    quote_day=quote['date']
    limit=min(cutoff,quote_day)
    if quote.get('timestamp','')[11:16]<'15:00':limit=(dt.date.fromisoformat(limit)-dt.timedelta(days=1)).isoformat()
    for row in rows:
        try:
            date=dt.date.fromisoformat(str(row[0])).isoformat();close=float(row[2])
            if date<=limit and math.isfinite(close) and close>0:clean[date]=close
        except (IndexError,TypeError,ValueError):continue
    ordered=sorted(clean.items())
    if len(ordered)<6:raise ValueError('历史日线不足6个有效交易日')
    prices=[p for _,p in ordered];last=prices[-1]
    return dict(code=code,name=quote['name'],sessions=len(prices),first_date=ordered[0][0],last_date=ordered[-1][0],latest_close=round(last,4),return_5d=round((last/prices[-6]-1)*100,2),return_20d=round((last/prices[-21]-1)*100,2) if len(prices)>=21 else None,ma20=round(sum(prices[-20:])/20,4) if len(prices)>=20 else None,adjustment='qfq；指数以接口day口径为准',scope='descriptive_history'),dict(title=quote['name']+' 历史日线（前复权/指数原口径）',url=url,published_at=quote['timestamp'],scope='historical_prices',note='只使用已结束交易日；5/20交易日区间变化，不是整周收益，也不与未复权价格直接比较。')

def announcement_body(item,request_fn):
    match=re.search(r'/(AN\d+)\.html$',item['url'])
    if not match:return None
    url='https://np-cnotice-stock.eastmoney.com/api/content/ann?'+urllib.parse.urlencode(dict(art_code=match[1],client_source='web',page_index=1))
    obj=json.loads(request_fn(url,timeout=12,headers={'Referer':'https://data.eastmoney.com/','User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'})).get('data') or {}
    if obj.get('art_code') and obj['art_code']!=match[1]:return None
    if str(obj.get('is_ai_summary','')).lower() in ('1','true'):return None
    content=obj.get('notice_content') or obj.get('content') or ''
    if not isinstance(content,str):return None
    text=plain_text(content)
    if len(text)<80:return None
    # This is an extracted excerpt, not a claim to have read all PDF pages.
    return dict(title=item['title'],url=item['url'],published_at=item['published_at'],scope='announcement_excerpt',text=text[:12000],note='公开公告内容接口提取片段；可能截断或不含全部页/附件，不等于完整原文已核验。')

def enrich(facts,request_fn):
    enriched=dict(facts);warnings=[]
    base=[]
    for s in facts['sources']:
        base.append({**s,'scope':'market_snapshot' if s.get('kind')=='market' else 'title_only'})
    # Up to 3 indexes and 3 configured objects per run: bounded API use, not all-market coverage.
    targets=facts['quotes'][:6]
    def history(q):
        try:return historical_signal(q,request_fn,facts['market_date'])
        except Exception:return None
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(history,targets))
    signals=[]
    for q,result in zip(targets,results):
        if result:
            signal,source=result;source['id']='S'+str(len(base)+1);signal['source_id']=source['id'];base.append(source);signals.append(signal)
        else:warnings.append(dict(level='warning',message=q['name']+'历史日线未取得，不补造趋势或多日结论。'))
    def body(item):
        try:return announcement_body(item,request_fn)
        except Exception:return None
    with ThreadPoolExecutor(max_workers=3) as pool:details=list(pool.map(body,facts['announcements'][:5]))
    for item,detail in zip(facts['announcements'],details):
        if detail:
            existing=next((s for s in base if s['url']==item['url']),None)
            if existing:existing.update(detail)
    for i,s in enumerate(base,1):s['id']='S'+str(i)
    if not any(s['scope']=='announcement_excerpt' for s in base):warnings.append(dict(level='warning',message='未取得可读公告正文片段；标题只能作为线索，AI不得据此解释事件影响。'))
    if len(facts['quotes'])>6:warnings.append(dict(level='info',message='本次多日研究覆盖前三项指数与前三个观察对象，其余对象只有实时快照。'))
    enriched.update(signals=signals,article_sources=base,sources=base)
    return enriched,warnings
