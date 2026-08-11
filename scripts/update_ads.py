#!/usr/bin/env python3
import os, json, time, re, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from google import genai

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'ads.json'
SEEN = ROOT / 'data' / 'seen.json'
YT_KEY = os.environ.get('YOUTUBE_API_KEY', '').strip()
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.6-flash')
LOOKBACK_HOURS = int(os.environ.get('LOOKBACK_HOURS', '168'))
MAX_PER_QUERY = int(os.environ.get('MAX_PER_QUERY', '10'))
MAX_ANALYZE = int(os.environ.get('MAX_ANALYZE', '24'))
USER_AGENT = 'Mozilla/5.0 (compatible; ADRadar/1.0; +https://github.com/maengohi/ad-radar)'

COUNTRIES = {
    'JP': {'region':'JP','lang':'ja','aotw':'https://www.adsoftheworld.com/countries/japan',
           'queries':['2026 新CM 公式','2026 TVCM 新CM','2026 Web CM ブランド','2026 広告 キャンペーン ムービー']},
    'TH': {'region':'TH','lang':'th','aotw':'https://www.adsoftheworld.com/countries/thailand',
           'queries':['2026 โฆษณา ใหม่ official','2026 โฆษณาไทย แบรนด์','2026 Thailand commercial official','2026 Thailand campaign film']},
    'US': {'region':'US','lang':'en','aotw':'https://www.adsoftheworld.com/countries/united-states',
           'queries':['2026 official commercial brand','2026 new advertising campaign film','2026 brand campaign official film','2026 commercial official ad']},
    'GB': {'region':'GB','lang':'en','aotw':'https://www.adsoftheworld.com/countries/united-kingdom',
           'queries':['2026 UK advert official','2026 British advertising campaign film','2026 new UK commercial official','2026 UK brand campaign film']},
}

REJECT_PATTERNS = [
    r'never\s+skip', r'ads?\s+are\s+(pure\s+)?comedy', r'funny\s+ads?', r'best\s+(thai\s+)?ads?',
    r'top\s*\d+.*ads?', r'commercials?\s+compilation', r'advertisements?\s+compilation', r'compilation',
    r'reaction', r'reacts?\s+to', r'tamil\s+dub', r'dubbed', r'#shorts', r'#viral', r'#memes?', r'meme',
    r'history\s+of', r'explained', r'breakdown', r'analysis', r'tutorial', r'how\s+to\s+(run|make|create).*ads?',
    r'marketing\s+tips?', r'ยิง\s*ads', r'สอน.*ads', r'โค้ช.*ads', r'behind\s+the\s+scenes'
]
REJECT_RE = re.compile('|'.join(REJECT_PATTERNS), re.I)
ALLOWED_TAGS = {'IDEA','COPY','DIRECTION','ART','CINEMATOGRAPHY','EDIT','VFX','AI','COMEDY','STORYTELLING','SCALE','WEIRD'}

SYSTEM = '''You are a ruthless senior advertising creative director curating references for a Korean creative director.
SOURCE INTEGRITY FIRST. Keep professional, genuinely recent advertising work. Reject old-ad reuploads, compilations, reactions, commentary, meme edits, dubbed versions, fan uploads, tutorials, interviews, BTS, trailers, music videos, and work merely discussing advertising. Student/spec work is rejected.
CREATIVE VALUE SECOND. Reject generic product demos, conventional celebrity endorsements, ordinary beauty/fashion montages, generic emotional brand films, plain talking heads, simple product prettiness, or work whose only merit is polish.
Prefer a reusable creative mechanism: visual device, directing idea, edit structure, art direction, cinematography, VFX/AI use, scale, unusual casting, comedy, storytelling mechanic, human insight, copy twist, or linguistic wit.
Country bias: Japan -> copy/language/insight; Thailand -> visual idea/directing/comedy; USA & UK -> scale/edit/craft/big idea.
All explanatory output must be Korean. Never invent credits, dates, claims, visuals or copy. If uncertain use UNKNOWN.'''

OUTPUT_RULES = '''Return JSON only, exactly this shape:
{
  "is_actual_ad": true,
  "source_confidence": 0,
  "freshness_confidence": 0,
  "keep": true,
  "brand": "",
  "campaign": "",
  "radar_score": 0,
  "tags": ["IDEA"],
  "why_ko": "",
  "copy_original": "",
  "copy_ko": "",
  "copy_note_ko": "",
  "agency": "UNKNOWN",
  "director": "UNKNOWN"
}
keep can be true ONLY when is_actual_ad=true, source_confidence>=75, freshness_confidence>=65 and radar_score>=74.
Allowed tags: IDEA,COPY,DIRECTION,ART,CINEMATOGRAPHY,EDIT,VFX,AI,COMEDY,STORYTELLING,SCALE,WEIRD.
why_ko: 2-4 concise Korean sentences explaining the reusable creative mechanism, not a plot summary.
Only include copy actually stated in the source and creatively meaningful. Translate naturally to Korean. Explain wordplay/insight/twist in copy_note_ko. Never infer agency/director.'''


def now_iso(): return datetime.now(timezone.utc).isoformat()
def load_json(path, default):
    try: return json.loads(path.read_text('utf-8'))
    except Exception: return default

def clean_json(text):
    text=(text or '').strip(); text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.S)
    m=re.search(r'\{.*\}',text,re.S)
    if not m: raise ValueError('No JSON object in model response')
    return json.loads(m.group(0))

def valid_accept(a):
    try:
        return a.get('is_actual_ad') is True and a.get('keep') is True and int(a.get('source_confidence',0))>=75 and int(a.get('freshness_confidence',0))>=65 and int(a.get('radar_score',0))>=74
    except Exception: return False

def candidate_key(c):
    return c.get('source_url') or c.get('video_id') or c.get('url')

def obvious_reject(c):
    return bool(REJECT_RE.search(f"{c.get('video_title','')} {c.get('description','')} {c.get('channel','')}"))


def yt_search(country,cfg):
    after=(datetime.now(timezone.utc)-timedelta(hours=LOOKBACK_HOURS)).isoformat().replace('+00:00','Z')
    out=[]
    for q in cfg['queries']:
        p={'part':'snippet','q':q,'type':'video','order':'date','maxResults':MAX_PER_QUERY,'publishedAfter':after,
           'regionCode':cfg['region'],'relevanceLanguage':cfg['lang'],'key':YT_KEY,'safeSearch':'none'}
        r=requests.get('https://www.googleapis.com/youtube/v3/search',params=p,timeout=30); r.raise_for_status()
        for item in r.json().get('items',[]):
            vid=item.get('id',{}).get('videoId'); sn=item.get('snippet',{})
            if not vid: continue
            thumbs=sn.get('thumbnails',{}); thumb=thumbs.get('high') or thumbs.get('medium') or thumbs.get('default') or {}
            c={'source_kind':'youtube','country':country,'video_id':vid,'url':f'https://www.youtube.com/watch?v={vid}',
               'watch_url':f'https://www.youtube.com/watch?v={vid}','video_title':sn.get('title',''),'description':sn.get('description',''),
               'channel':sn.get('channelTitle',''),'published_at':sn.get('publishedAt',''),'thumbnail':thumb.get('url','')}
            if not obvious_reject(c): out.append(c)
    return list({x['video_id']:x for x in out}.values())


def youtube_id_from_html(html):
    patterns=[r'youtube\.com/(?:watch\?v=|embed/)([A-Za-z0-9_-]{11})',r'youtu\.be/([A-Za-z0-9_-]{11})']
    for pat in patterns:
        m=re.search(pat,html,re.I)
        if m:return m.group(1)
    return ''


def aotw_list(country,cfg,limit=8):
    r=requests.get(cfg['aotw'],headers={'User-Agent':USER_AGENT},timeout=30); r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser'); found=[]; seen=set()
    for a in soup.find_all('a',href=True):
        href=a.get('href',''); path=urlparse(href).path
        if not re.fullmatch(r'/campaigns/[^/]+',path): continue
        url=urljoin('https://www.adsoftheworld.com',href.split('?')[0]); title=' '.join(a.stripped_strings).strip()
        if not title or url in seen: continue
        prev=a.find_previous('a'); brand_hint=' '.join(prev.stripped_strings).strip() if prev else ''
        seen.add(url); found.append({'source_kind':'aotw','country':country,'source_url':url,'campaign_hint':title,'brand_hint':brand_hint})
        if len(found)>=limit: break
    return found


def aotw_detail(c):
    r=requests.get(c['source_url'],headers={'User-Agent':USER_AGENT},timeout=30); r.raise_for_status(); html=r.text
    soup=BeautifulSoup(html,'html.parser'); all_text=' '.join(soup.stripped_strings)
    low=all_text.lower()
    if 'student campaign' in low or 'school:' in low: return None
    # We are building a video/film reference archive, not a general campaign gallery.
    has_film=(' film ' in f' {low} ') or ('iframe' in low) or ('<video' in html.lower())
    if not has_film: return None
    desc=''
    m=re.search(r'Description\s+(.*?)\s+(?:Credits|Categories)',all_text,re.S|re.I)
    if m: desc=m.group(1).strip()
    if len(desc)<120: desc=all_text[:9000]
    else: desc=desc[:9000]
    title_tag=soup.title.get_text(' ',strip=True) if soup.title else ''
    brand=c.get('brand_hint',''); campaign=c.get('campaign_hint','')
    mt=re.match(r'(.+?):\s*(.+?)\s*[•|]\s*Ads of the World',title_tag)
    if mt: brand,campaign=mt.group(1).strip(),mt.group(2).strip()
    og=soup.find('meta',attrs={'property':'og:image'}); thumb=og.get('content','') if og else ''
    vid=youtube_id_from_html(html)
    watch=f'https://www.youtube.com/watch?v={vid}' if vid else c['source_url']
    agency='UNKNOWN'; ma=re.search(r'Agency:\s*([^•]{2,100}?)(?:\s+(?:Description|Credits|Categories)|$)',all_text,re.I)
    if ma: agency=ma.group(1).strip()[:100]
    director='UNKNOWN'; md=re.search(r'Directors?:\s*([^•]{2,100}?)(?:\s+[A-Z][A-Za-z ]+:|$)',all_text)
    if md: director=md.group(1).strip()[:100]
    return {**c,'brand_hint':brand,'campaign_hint':campaign,'editorial_text':desc,'thumbnail':thumb,'video_id':vid,
            'url':watch,'watch_url':watch,'video_title':f'{brand} — {campaign}','description':desc,'channel':'Ads of the World',
            'published_at':'','agency_hint':agency,'director_hint':director}


def analyze_video(client,c):
    meta=(f"country={c['country']}\nsource={c.get('source_kind')}\nsource_url={c.get('source_url','')}\n"
          f"title={c.get('video_title','')}\nchannel={c.get('channel','')}\ndescription={c.get('description','')[:3000]}\n"
          f"brand_hint={c.get('brand_hint','')}\ncampaign_hint={c.get('campaign_hint','')}\nagency_hint={c.get('agency_hint','UNKNOWN')}")
    prompt=SYSTEM+'\n\nWatch the attached public YouTube video and inspect the metadata. '+OUTPUT_RULES+'\n\nMetadata:\n'+meta
    interaction=client.interactions.create(model=MODEL,input=[{'type':'text','text':prompt},{'type':'video','uri':c['watch_url']}])
    return clean_json(interaction.output_text)


def analyze_editorial(client,c):
    prompt=(SYSTEM+'\n\nThis candidate comes from Ads of the World. Judge ONLY from the professional campaign description below. '
            'Do not invent visual details that the text does not state. is_actual_ad means the source clearly describes a professional film/video campaign. '
            'source_confidence may be high because the editorial source is known, but freshness must come from the supplied text. '+OUTPUT_RULES+
            f"\n\nCountry: {c['country']}\nBrand hint: {c.get('brand_hint','')}\nCampaign hint: {c.get('campaign_hint','')}\n"
            f"Agency hint: {c.get('agency_hint','UNKNOWN')}\nSource URL: {c.get('source_url','')}\nEditorial description:\n{c.get('editorial_text','')}")
    interaction=client.interactions.create(model=MODEL,input=[{'type':'text','text':prompt}])
    return clean_json(interaction.output_text)


def analyze_with_retry(client,c,attempts=3):
    last=None
    for attempt in range(attempts):
        try:
            if c.get('source_kind')=='aotw' and not c.get('video_id'): return analyze_editorial(client,c)
            return analyze_video(client,c)
        except Exception as e:
            last=e
            if attempt<attempts-1:
                wait=4*(attempt+1); print(f"[retry:{candidate_key(c)}] {wait}s after: {e}"); time.sleep(wait)
    raise last


def balanced_candidates(existing_seen):
    buckets={k:[] for k in COUNTRIES}
    for code,cfg in COUNTRIES.items():
        try:
            raw=aotw_list(code,cfg,limit=8); details=[]
            for item in raw:
                if candidate_key(item) in existing_seen: continue
                try:
                    d=aotw_detail(item)
                    if d: details.append(d)
                except Exception as e: print(f"[aotw-detail:{item['source_url']}] {e}")
            print(f'[aotw:{code}] {len(details)} film candidates')
            buckets[code].extend(details[:4])
        except Exception as e: print(f'[aotw:{code}] ERROR {e}')
        try:
            ys=[x for x in yt_search(code,cfg) if candidate_key(x) not in existing_seen]
            print(f'[youtube:{code}] {len(ys)} prefiltered candidates')
            buckets[code].extend(ys[:2])
        except Exception as e: print(f'[youtube:{code}] ERROR {e}')
    out=[]
    # country balance: up to 6 candidates per country before any overflow
    for code in ('JP','TH','US','GB'): out.extend(buckets[code][:6])
    # de-dupe by canonical key
    uniq=[]; keys=set()
    for c in out:
        k=candidate_key(c)
        if not k or k in keys: continue
        keys.add(k); uniq.append(c)
    return uniq[:MAX_ANALYZE]


def main():
    if not YT_KEY or not GEMINI_KEY: raise SystemExit('Missing YOUTUBE_API_KEY or GEMINI_API_KEY')
    existing=load_json(DATA,[]); seen=load_json(SEEN,{})
    accepted_keys={candidate_key(x) for x in existing if candidate_key(x)}
    known=set(seen.keys())|accepted_keys
    client=genai.Client(api_key=GEMINI_KEY)
    candidates=balanced_candidates(known)
    print(f'new candidates to analyze: {len(candidates)}')
    accepted=[]
    for i,c in enumerate(candidates,1):
        key=candidate_key(c)
        try:
            a=analyze_with_retry(client,c)
            print(f"[{i}/{len(candidates)}] {c['country']} {c.get('source_kind')} actual={a.get('is_actual_ad')} source={a.get('source_confidence')} fresh={a.get('freshness_confidence')} keep={a.get('keep')} score={a.get('radar_score')} {c.get('video_title','')[:55]}")
            if valid_accept(a):
                rec={**c,**a,'id':hashlib.sha1(key.encode()).hexdigest()[:12],'discovered_at':now_iso()}
                rec['brand']=a.get('brand') or c.get('brand_hint') or 'UNKNOWN'; rec['campaign']=a.get('campaign') or c.get('campaign_hint') or c.get('video_title') or 'UNTITLED'
                rec['agency']=a.get('agency') if a.get('agency') not in ('','UNKNOWN',None) else c.get('agency_hint','UNKNOWN')
                rec['director']=a.get('director') if a.get('director') not in ('','UNKNOWN',None) else c.get('director_hint','UNKNOWN')
                rec['tags']=[t for t in rec.get('tags',[]) if t in ALLOWED_TAGS]
                rec.pop('editorial_text',None); accepted.append(rec)
        except Exception as e: print(f'[analyze:{key}] ERROR {e}')
        seen[key]=now_iso(); time.sleep(1.0)
    # trim seen ledger to 60 days
    cutoff=datetime.now(timezone.utc)-timedelta(days=60); trimmed={}
    for k,v in seen.items():
        try:
            if datetime.fromisoformat(v.replace('Z','+00:00'))>=cutoff: trimmed[k]=v
        except Exception: trimmed[k]=v
    merged=accepted+existing
    merged.sort(key=lambda x:x.get('published_at') or x.get('discovered_at') or '',reverse=True)
    DATA.write_text(json.dumps(merged,ensure_ascii=False,indent=2),encoding='utf-8')
    SEEN.write_text(json.dumps(trimmed,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'accepted: {len(accepted)} / archive total: {len(merged)} / seen: {len(trimmed)}')

if __name__=='__main__': main()
