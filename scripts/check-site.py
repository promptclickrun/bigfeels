from pathlib import Path
from html.parser import HTMLParser
import json, urllib.request, zipfile, re
root=Path(__file__).resolve().parents[1]/'site'
class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.assets=[];self.ids=[];self.h1=0;self.lang=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'id' in a:self.ids.append(a['id'])
        if tag=='html':self.lang=a.get('lang')
        if tag=='h1':self.h1+=1
        if tag=='a':self.links.append(a.get('href',''))
        if tag in ('img','script') and a.get('src'):self.assets.append(a['src'])
        if tag=='link' and a.get('href'):self.assets.append(a['href'])
p=Page();text=(root/'index.html').read_text();p.feed(text)
assert p.lang=='en' and p.h1==1
assert len(p.ids)==len(set(p.ids)), 'Duplicate IDs'
# Responsive CSS can hide editorial line breaks; words must stay separated.
expected_headings = {
    'evidence-title': 'Keep the source close.',
    'interfaces-title': 'Fits the tools you use.',
    'boundaries-title': 'Know what stays local.',
    'install-title': 'Give your agent something to remember.',
}
for heading_id, expected in expected_headings.items():
    heading = re.search(rf'<h2 id="{heading_id}">(.*?)</h2>', text, re.S)
    assert heading, f'Missing heading: {heading_id}'
    without_breaks = re.sub(r'<[^>]+>', '', heading.group(1))
    assert ' '.join(without_breaks.split()) == expected, f'Joined heading words: {heading_id}'
for link in p.links:
    assert link and link!='#', 'Placeholder link'
    if link.startswith('#'):assert link[1:] in p.ids,link
for link in p.assets+p.links:
    if not link.startswith(('https:','#')):
        assert (root/link).exists(),link
for path in [root/'index.html',root/'app.js',root/'assets/BRAND.md']:
    assert '\u2014' not in path.read_text(),f'Em dash in {path}'
assert '<main id="main">' in text
assert 'Illustrative CLI example' in text and 'It does not save anything.' in text
assert 'Google' not in (root/'styles.css').read_text(), 'Fonts must be self-hosted'
for external in set(x.split('#')[0] for x in p.links if x.startswith('https:')):
    with urllib.request.urlopen(external,timeout=30) as response:
        assert response.status==200,(external,response.status)
print(json.dumps({'local_assets_and_links':'passed','heading_and_ids':'passed','grammar_scan':'passed','external_links':len(set(x.split('#')[0] for x in p.links if x.startswith('https:'))),'site_files':len(list(root.rglob('*')))}))
