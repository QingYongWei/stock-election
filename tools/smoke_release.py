"""Exercise the built clean archive against real market endpoints, without AI keys or publishing."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]

def main():
    archive=ROOT/'dist'/'stock-content-agent-0.3.0.zip'
    delivery=ROOT/'delivery';delivery.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='release-smoke-',dir=delivery,ignore_cleanup_errors=True) as tmp:
        tmp=Path(tmp)
        with zipfile.ZipFile(archive) as z:z.extractall(tmp)
        app=tmp/'stock-content-agent-0.3.0'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'STOCK_AGENT_DATA':str(tmp/'isolated-data'),'PYTHONIOENCODING':'utf-8'}
        def req(path,method='GET',payload=None,raw=False):
            body=json.dumps(payload).encode() if payload is not None else None
            request=urllib.request.Request(base+path,data=body,method=method,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=15) as r:
                result=r.read();return result if raw else json.loads(result)
        with (delivery/'v0.3-release-smoke-server.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-m','stock_agent.server','--port',str(port)],cwd=app,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+15
                while True:
                    try:
                        health=req('/api/health');break
                    except (OSError,urllib.error.URLError):
                        if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Clean release server did not start')
                        time.sleep(.2)
                assert health['ok']
                assert not req('/api/config')['has_api_key']
                assert req('/api/jobs')==[]
                platform=req('/api/platform');assert len(platform['directions'])==6 and platform['workspaces']==[]
                work=req('/api/workspaces','POST',{'direction_id':'work'})
                req('/api/workspaces/'+work['id']+'/activate','POST',{})
                assert req('/api/platform')['active_workspace_id']==work['id']
                try:req('/api/jobs','POST',{'workspace_id':work['id']})
                except urllib.error.HTTPError as e:assert e.code==400
                else:raise AssertionError('Unconfigured generic model must not produce fake content')
                j=req('/api/jobs','POST',{'kind':'daily'});jid=j['id']
                deadline=time.monotonic()+150
                while j['status']=='running':
                    if time.monotonic()>deadline:raise TimeoutError('Live task timeout')
                    time.sleep(.5);j=req('/api/jobs/'+jid)
                assert j['status']=='materials_ready',j.get('error')
                assert not j['publication']['ready'] and not j['content_ready']
                assert j['workspace_id']=='workspace-finance' and req('/api/jobs?workspace_id=workspace-work')==[]
                assert j['engine_mode']=='facts' and len(j['cards'])==4
                html=req('/api/jobs/'+jid+'/preview',raw=True).decode()
                assert '<table' in html and '<script' not in html
                for path,method,payload in [('/api/jobs/'+jid+'/review','POST',{'action':'approve','confirm_cards':True}),('/api/jobs/'+jid+'/publish-html','GET',None)]:
                    try:req(path,method,payload)
                    except urllib.error.HTTPError as e:
                        assert e.code==400
                        assert '资料' in json.loads(e.read())['error']
                    else:raise AssertionError('Materials-only job was exposed as publication-ready')
                images=req('/api/jobs/'+jid+'/images',raw=True)
                with zipfile.ZipFile(io.BytesIO(images)) as z:assert all(f'{i:02d}.png' in z.namelist() for i in range(1,5))
                exported=req('/api/jobs/'+jid+'/export',raw=True)
                with zipfile.ZipFile(io.BytesIO(exported)) as z:
                    assert z.testzip() is None
                    assert 'wechat.html' in z.namelist()
                    for i in range(1,5):
                        with Image.open(io.BytesIO(z.read(f'cards/card-{i}.png'))) as im:assert im.size==(1080,1440)
                changed=req('/api/jobs/'+jid+'/content','PUT',{'wechat_title':j['wechat_title']})
                assert changed['status']=='materials_ready' and not changed['publication']['ready']
                result=dict(ok=True,version='0.3.0',clean_archive=True,real_network=True,market_date=j['market_date'],quote_count=len(j['facts']['quotes']),announcement_count=len(j['facts']['announcements']),cards=4,card_size=[1080,1440],engine='facts',unconfigured_ai_not_publishable=True,rich_copy_and_approval_blocked=True,images_zip=True,export_crc=True,real_model_tested=False,external_publish=False,multi_workspace=True,generic_unconfigured_blocked=True,server_shutdown_in_finally=True)
                # Write the final receipt only after server shutdown and temporary cleanup.
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    result['test_server_closed']=process.poll() is not None
    result['temporary_cleanup_complete']=not tmp.exists()
    (delivery/'v0.3-release-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
