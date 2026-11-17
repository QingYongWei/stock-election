"""Fetch the audited, pinned DSA source archive without credentials or git."""
from pathlib import Path
import hashlib
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
COMMIT='c820915ea0119b1b7952652d7fb148575400f2d9'
SHA256='45d5557635bc5a7be9600c4725f5631ab0518da5e1260eb7c96329ffff779f79'
NAME='daily_stock_analysis-'+COMMIT

def main():
    vendor=ROOT/'vendor';vendor.mkdir(exist_ok=True)
    target=vendor/NAME
    if (target/'main.py').is_file():
        print('Existing DSA source preserved:',target);return 0
    archive=vendor/'dsa-source.zip'
    if not archive.exists():
        url='https://codeload.github.com/ZhuLinsen/daily_stock_analysis/zip/'+COMMIT
        print('Downloading pinned upstream source (~67 MB):',url,flush=True)
        with urllib.request.urlopen(url,timeout=90) as r:
            raw=r.read(100*1024*1024+1)
        if len(raw)>100*1024*1024:raise ValueError('Archive exceeds limit')
        if hashlib.sha256(raw).hexdigest()!=SHA256:raise ValueError('Source archive hash mismatch; do not extract')
        archive.write_bytes(raw)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA256:raise ValueError('Existing source archive hash mismatch; preserved, not overwritten')
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist())>600*1024*1024:raise ValueError('Expanded archive exceeds limit')
        regular=[]
        for i in z.infolist():
            p=Path(i.filename)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!=NAME:raise ValueError('Unsafe archive path')
            # Never materialize links. Upstream CLAUDE.md is only an AGENTS.md alias.
            if (i.external_attr>>16)&0o170000==0o120000:continue
            regular.append(i)
        z.extractall(vendor,members=regular)
    print('Source ready:',target)
    print('No dependencies installed and no analysis run. Read upstream LICENSE and requirements before installation.')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        print(type(e).__name__+': '+str(e),file=sys.stderr);raise SystemExit(1)
