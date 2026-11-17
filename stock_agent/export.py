"""Safe, inline-styled article HTML for visual review and rich clipboard copy."""
from html import escape
import re


def inline(text):
    text=escape(text)
    links=[]
    def link(match):
        label,url=match.groups()
        links.append(f'<a href="{url}" rel="noopener noreferrer" style="color:#176c5c;text-decoration:underline;word-break:break-all">{label}</a>')
        return '\x00LINK'+str(len(links)-1)+'\x00'
    text=re.sub(r'\[([^\]\n]+)\]\((https?://[^\s<>"\)]+)\)',link,text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<strong style="font-weight:700">\1</strong>',text)
    text=re.sub(r'`([^`]+)`',r'<code style="background:#f2f5f4;padding:2px 4px">\1</code>',text)
    for i,value in enumerate(links):text=text.replace('\x00LINK'+str(i)+'\x00',value)
    return text


def article_body(markdown,omit_title=False):
    output=[];rows=[];first=True
    def flush():
        if not rows:return
        output.append('<table style="border-collapse:collapse;width:100%;font-size:13px;margin:16px 0;table-layout:fixed">')
        for index,row in enumerate(rows):
            output.append('<tr>')
            for value in row:
                tag='th' if index==0 else 'td'
                output.append(f'<{tag} style="border:1px solid #dce7e4;padding:8px;text-align:left;overflow-wrap:anywhere">{inline(value)}</{tag}>')
            output.append('</tr>')
        output.append('</table>');rows.clear()
    for line in markdown.splitlines():
        if not line.strip():continue
        if first:
            first=False
            if omit_title and re.match(r'^#\s+',line):continue
        if line.startswith('|'):
            fields=[x.strip() for x in line.strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?',x) for x in fields):rows.append(fields)
            continue
        flush()
        m=re.match(r'^(#{1,3})\s+(.+)$',line)
        if m:
            level=len(m[1]);size={1:24,2:20,3:17}[level]
            output.append(f'<h{level} style="font-size:{size}px;line-height:1.5;color:#176c5c;margin:26px 0 12px;font-weight:700">{inline(m[2])}</h{level}>')
        elif re.fullmatch(r'\s*[-*_]{3,}\s*',line):output.append('<hr style="border:0;border-top:1px solid #e1eae7;margin:24px 0">')
        elif line.startswith('> '):output.append('<blockquote style="margin:16px 0;background:#f2f8f6;color:#516b61;font-size:14px;border-left:3px solid #33af92;padding:12px 16px">'+inline(line[2:])+'</blockquote>')
        elif re.match(r'^\s*[-*]\s+',line):output.append('<p style="line-height:1.9;margin:10px 0;padding-left:12px">• '+inline(re.sub(r'^\s*[-*]\s+','',line))+'</p>')
        else:output.append('<p style="line-height:1.9;margin:14px 0;overflow-wrap:anywhere">'+inline(line)+'</p>')
    flush()
    return ''.join(output)


def wechat_html(markdown,title='',for_clipboard=False):
    body=article_body(markdown,omit_title=for_clipboard)
    style='color:#233b34;font-size:16px;line-height:1.9;font-family:system-ui,Microsoft YaHei,sans-serif;overflow-wrap:anywhere'
    if for_clipboard:
        content='<section style="'+style+'">'+body+'</section>'
    else:
        content='<article style="max-width:760px;margin:24px auto;background:white;padding:28px;'+style+'">'+body+'</article>'
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)+'</title></head><body style="margin:0;background:'+('white' if for_clipboard else '#f6f9f8')+'">'+content+'</body></html>'
