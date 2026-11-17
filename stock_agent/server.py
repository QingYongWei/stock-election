from __future__ import annotations
import argparse, json, mimetypes, os, re, socket, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import __version__, core, platform

STATIC=Path(__file__).parent/'static'

class LocalServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def server_bind(self):
        if os.name=='nt':self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

class Handler(BaseHTTPRequestHandler):
    server_version='StockContentAgent/'+__version__
    def log_message(self, fmt, *args):
        # Do not log query strings, request bodies or provider credentials.
        print(f'[{core.stamp()}] {self.command} {urllib.parse.urlsplit(self.path).path} {str(args[1]) if len(args)>1 else ""}',flush=True)
    def send(self,code,data,ctype='application/json; charset=utf-8',extra=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        elif isinstance(data,str):data=data.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'")
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def verify(self,mutate=False):
        # Local-first only: prevent DNS rebinding and cross-origin browser mutation.
        expected_port=self.server.server_port
        allowed={f'127.0.0.1:{expected_port}',f'localhost:{expected_port}'}
        host=self.headers.get('Host','')
        if host not in allowed:raise PermissionError('只允许通过本机地址访问；不支持直接公网暴露')
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://{x}' for x in allowed}:raise PermissionError('拒绝跨站请求')
        if mutate and not self.headers.get('Content-Type','').startswith('application/json'):raise PermissionError('写操作必须使用application/json')
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>300000:raise ValueError('请求体过大')
        b=json.loads(self.rfile.read(n) or b'{}')
        if not isinstance(b,dict):raise ValueError('请求必须为JSON对象')
        return b
    def do_GET(self):self.route('GET')
    def do_POST(self):self.route('POST')
    def do_PUT(self):self.route('PUT')
    def do_DELETE(self):self.route('DELETE')
    def route(self,method):
        try:
            self.verify(method!='GET')
            parsed=urllib.parse.urlsplit(self.path);path=parsed.path
            query=urllib.parse.parse_qs(parsed.query);workspace_id=query.get('workspace_id',[None])[0]
            if workspace_id:platform.get_workspace(workspace_id)
            if path=='/api/platform' and method=='GET':return self.send(200,{**platform.state(),'version':__version__})
            if path=='/api/directions' and method=='GET':return self.send(200,platform.directions())
            if path=='/api/workspaces':
                if method=='GET':return self.send(200,platform.list_workspaces())
                if method=='POST':return self.send(201,platform.create_workspace(self.body()))
            wm=re.fullmatch(r'/api/workspaces/(workspace-[a-z0-9-]+)(?:/(activate|feedback))?',path)
            if wm:
                wid,action=wm.groups()
                if method=='GET' and not action:return self.send(200,platform.get_workspace(wid))
                if method=='PUT' and not action:return self.send(200,platform.update_workspace(wid,self.body()))
                if method=='POST' and action=='activate':self.body();return self.send(200,platform.activate_workspace(wid))
                if method=='POST' and action=='feedback':return self.send(200,platform.feedback(wid,self.body()))
            if path=='/api/providers' and method=='GET':return self.send(200,platform.list_providers())
            if path=='/api/providers' and method=='POST':return self.send(201,platform.save_provider(self.body()))
            pm=re.fullmatch(r'/api/providers/([a-zA-Z0-9_-]+)(?:/(test))?',path)
            if pm:
                pid,action=pm.groups()
                if method=='PUT' and not action:return self.send(200,platform.save_provider(self.body(),id=pid))
                if method=='DELETE' and not action:self.body();return self.send(200,platform.delete_provider(pid))
                if method=='POST' and action=='test':
                    self.body();result=platform.test_provider(pid);return self.send(200,{**result,'ok':bool(result.get('connected')),'message':result.get('error') or '最小模型请求已成功，不代表后续所有生成必然成功。'})
            if path=='/api/assignments' and method=='PUT':return self.send(200,platform.save_assignments(self.body()))
            if path=='/api/health' and method=='GET':return self.send(200,dict(ok=True,version=__version__,scheduler=core.SCHEDULER_STATE,last_schedule_error=core.SCHEDULER_STATE['last_schedule_error']))
            if path=='/api/config':
                if method=='GET':return self.send(200,core.config())
                if method=='PUT':return self.send(200,core.save_config(self.body()))
            if path=='/api/jobs':
                if method=='GET':return self.send(200,[core.public_job(j) for j in core.jobs(workspace_id)])
                if method=='POST':
                    b=self.body();return self.send(202,core.start_job(b.get('kind','daily'),note=str(b.get('note',''))[:2000],workspace_id=b.get('workspace_id')))
            m=re.fullmatch(r'/api/jobs/([a-f0-9]{16})(?:/(review|regenerate|content|export|preview|publish-html|images))?',path)
            if m:
                jid,action=m.groups()
                if workspace_id and core.get(jid).get('workspace_id','workspace-finance')!=workspace_id:raise KeyError('任务不属于此工作空间')
                if method=='GET' and not action:return self.send(200,core.public_job(core.get(jid)))
                if method=='GET' and action in ('preview','publish-html'):
                    from .export import wechat_html
                    with core.LOCK:
                        job=core.get(jid)
                        if job['status'] in ('running','failed'):raise ValueError('任务尚无可预览内容')
                        if action=='publish-html' and not core.public_job(job)['publication']['ready']:raise ValueError('只有完成AI选题与分析的成稿可以复制发布正文；当前为资料包')
                        html=wechat_html(job['wechat_markdown'],job['wechat_title'],for_clipboard=action=='publish-html')
                    return self.send(200,html,'text/html; charset=utf-8')
                if action=='images' and method=='GET':return self.send(200,core.images_zip(jid),'application/zip',{'Content-Disposition':f'attachment; filename="xiaohongshu-images-{jid}.zip"'})
                if action=='review' and method=='POST':return self.send(200,core.review(jid,self.body()))
                if action=='content' and method=='PUT':return self.send(200,core.update_content(jid,self.body()))
                if action=='regenerate' and method=='POST':
                    old=core.get(jid)
                    if old['status']=='running':raise ValueError('任务仍在运行')
                    b=self.body();return self.send(202,core.start_job(old['kind'],note=str(b.get('note',old.get('review_note',''))),parent=jid,workspace_id=old.get('workspace_id','workspace-finance')))
                if action=='export' and method=='GET':return self.send(200,core.export_zip(jid),'application/zip',{'Content-Disposition':f'attachment; filename="stock-content-{jid}.zip"'})
            if method=='GET' and path.startswith('/artifacts/'):
                m=re.fullmatch(r'/artifacts/([a-f0-9]{16})/(card-[1-9]\d*\.png)',path)
                if not m:raise KeyError('文件不存在')
                with core.LOCK:
                    file=core.DATA/'artifacts'/m[1]/m[2]
                    if not file.is_file():raise KeyError('文件不存在')
                    raw=file.read_bytes()
                return self.send(200,raw,'image/png')
            if method=='GET' and path in ('/','/index.html','/app.js','/style.css','/static/app.js','/static/style.css','/favicon.ico'):
                file=STATIC/('index.html' if path=='/' else path.rsplit('/',1)[-1])
                if not file.is_file():return self.send(404,{'error':'文件不存在'})
                ctype=mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
                return self.send(200,file.read_bytes(),ctype+('; charset=utf-8' if file.suffix in ('.html','.js','.css') else ''))
            return self.send(404,{'error':'路由不存在'})
        except PermissionError as e:self.send(403,{'error':str(e)})
        except KeyError:self.send(404,{'error':'任务或文件不存在'})
        except (ValueError,TypeError) as e:self.send(400,{'error':str(e)[:250]})
        except BrokenPipeError:pass
        except Exception as e:
            print('Request error:',type(e).__name__,flush=True)
            self.send(500,{'error':'内部处理失败，请查看本地日志；未暴露密钥和上游响应。'})

def main():
    p=argparse.ArgumentParser(description='本地多领域内容平台与独立工作空间')
    p.add_argument('--port',type=int,default=8765)
    args=p.parse_args()
    try:httpd=LocalServer(('127.0.0.1',args.port),Handler)
    except OSError:
        p.exit(2,f'无法监听端口 {args.port}：可能已有工作台运行。请先使用现有页面，或在原启动窗口按 Ctrl+C 停止后重启；不会自动关闭其他进程。\n')
    core.init()
    threading.Thread(target=core.scheduler_loop,daemon=True).start()
    print(f'创作罗盘（暂名） v{__version__} http://127.0.0.1:{args.port}  Data: {core.DATA}',flush=True)
    print('Local-only. No public publishing API is enabled. Facts mode uses real network data; AI requires your provider key.',flush=True)
    try:httpd.serve_forever()
    except KeyboardInterrupt:pass
    finally:core.STOP.set();httpd.server_close()

if __name__=='__main__':main()
