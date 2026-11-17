"""Build a clean source delivery archive; excludes runtime data, credentials and vendor."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.3.0'

def main():
    output=ROOT/'dist'/('stock-content-agent-'+VERSION+'.zip')
    output.parent.mkdir(exist_ok=True)
    files=[]
    for name in ['README.md','CHANGELOG.md','THIRD_PARTY_NOTICES.md','v0.3升级验收.md','requirements-agent.txt','start.ps1','启动工作台.cmd','.gitignore','delivery/v0.3-test-results.txt','delivery/v0.3-release-smoke.json','delivery/evidence/v0.3-platform-fixture.png','delivery/evidence/v0.3-finance-fixture.png']:
        p=ROOT/name
        if p.is_file():files.append(p)
    for dirname in ['stock_agent','tools','tests']:
        files.extend(p for p in (ROOT/dirname).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.ps1','.html','.js','.css','.json'))
    manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    prefix='stock-content-agent-'+VERSION+'/'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):z.write(p,prefix+str(p.relative_to(ROOT)).replace('\\','/'))
        z.writestr(prefix+'MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(output) as z:
        bad=z.testzip()
        if bad:raise ValueError('Archive CRC failure: '+bad)
        for name in z.namelist():
            relative=name.removeprefix(prefix)
            if relative.startswith(('data/','vendor/','.git/')) or relative.endswith(('.env','.db','.sqlite','.pyc')):raise ValueError('Unsafe release member: '+name)
    print('Clean source archive:',output)
    print('Files:',len(files),'Bytes:',output.stat().st_size)
    print('SHA256:',hashlib.sha256(output.read_bytes()).hexdigest())

if __name__=='__main__':main()
