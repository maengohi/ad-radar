#!/usr/bin/env python3
import os, json, time, re, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from google import genai

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'ads.json'
YT_KEY = os.environ.get('YOUTUBE_API_KEY', '').strip()
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.6-flash')
LOOKBACK_HOURS = int(os.environ.get('LOOKBACK_HOURS', '48'))
MAX_PER_QUERY = int(os.environ.get('MAX_PER_QUERY', '8'))
MAX_ANALYZE = int(os.environ.get('MAX_ANALYZE', '0'))  # 0 = no cap

COUNTRIES = {
    'JP': {
        'region': 'JP', 'lang': 'ja',
        'queries': ['CM 新CM 企業 広告', 'Web CM ブランド 広告', '新作 CM キャンペーン']
    },
    'TH': {
        'region': 'TH', 'lang': 'th',
        'queries': ['โฆษณา ใหม่ commercial', 'โฆษณา ไทย brand film', 'Thailand advertising campaign']
    },
    'US': {
        'region': 'US', 'lang': 'en',
        'queries': ['new commercial brand film', 'new advertising campaign film', 'creative commercial']
    },
    'GB': {
        'region': 'GB', 'lang': 'en',
        'queries': ['new UK advert brand film', 'British advertising campaign film', 'creative UK commercial']
    },
}

SYSTEM = '''You are a ruthless senior advertising creative director curating references for a Korean creative director.
Keep ONLY ads that provide reusable creative inspiration. Reject generic product demos, conventional celebrity endorsements, ordinary beauty/fashion montages, plain talking-head brand films, simple product prettiness, standard performance, or work whose only merit is production polish.
Prefer a strong visual device, directing idea, editing structure, art direction, cinematography, VFX/AI technique, scale, strange casting, comedy, storytelling mechanic, human insight, copy twist, linguistic wit, or an idea that can be explained in one sharp sentence.
Country bias: Japan -> scrutinize copy and linguistic/insight value; Thailand -> scrutinize visual idea/directing/comedy; USA & UK -> scrutinize scale/edit/craft/big idea.
All explanatory output must be Korean. Never invent credits or copy. If uncertain use UNKNOWN.'''

PROMPT = '''Watch the attached public YouTube video and decide whether it belongs in AD RADAR.
Return JSON only, exactly this shape:
{
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
radar_score is 0-100 and should be strict. keep should normally require 72+.
Allowed tags: IDEA,COPY,DIRECTION,ART,CINEMATOGRAPHY,EDIT,VFX,AI,COMEDY,STORYTELLING,SCALE,WEIRD.
why_ko: 2-4 concise Korean sentences explaining the reusable creative idea, not a plot summary.
For copy: only include copy that is actually spoken/shown and creatively meaningful. Translate naturally into Korean. copy_note_ko should explain wordplay/insight/twist if present.
Do not infer agency/director unless visible/audible in video or reliably stated in the provided metadata.
Metadata follows:
'''


def yt_search(country, cfg):
    after = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat().replace('+00:00', 'Z')
    out = []
    for q in cfg['queries']:
        p = {
            'part': 'snippet', 'q': q, 'type': 'video', 'order': 'date',
            'maxResults': MAX_PER_QUERY, 'publishedAfter': after,
            'regionCode': cfg['region'], 'relevanceLanguage': cfg['lang'], 'key': YT_KEY
        }
        r = requests.get('https://www.googleapis.com/youtube/v3/search', params=p, timeout=30)
        r.raise_for_status()
        for item in r.json().get('items', []):
            vid = item.get('id', {}).get('videoId')
            sn = item.get('snippet', {})
            if not vid:
                continue
            thumbs = sn.get('thumbnails', {})
            thumb = thumbs.get('high') or thumbs.get('medium') or thumbs.get('default') or {}
            out.append({
                'country': country,
                'video_id': vid,
                'url': f'https://www.youtube.com/watch?v={vid}',
                'video_title': sn.get('title', ''),
                'description': sn.get('description', ''),
                'channel': sn.get('channelTitle', ''),
                'published_at': sn.get('publishedAt', ''),
                'thumbnail': thumb.get('url', '')
            })
    return list({x['video_id']: x for x in out}.values())


def clean_json(text):
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.S)
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        raise ValueError('No JSON object in model response')
    return json.loads(m.group(0))


def analyze(client, c):
    meta = (
        f"country={c['country']}\n"
        f"title={c['video_title']}\n"
        f"channel={c['channel']}\n"
        f"description={c['description'][:1800]}"
    )
    interaction = client.interactions.create(
        model=MODEL,
        input=[
            {'type': 'text', 'text': SYSTEM + '\n\n' + PROMPT + meta},
            {'type': 'video', 'uri': c['url']},
        ],
    )
    return clean_json(interaction.output_text)


def analyze_with_retry(client, c, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            return analyze(client, c)
        except Exception as e:
            last = e
            if attempt < attempts - 1:
                wait = 4 * (attempt + 1)
                print(f"[retry:{c['video_id']}] waiting {wait}s after: {e}")
                time.sleep(wait)
    raise last


def load_existing():
    try:
        return json.loads(DATA.read_text('utf-8'))
    except Exception:
        return []


def main():
    if not YT_KEY or not GEMINI_KEY:
        raise SystemExit('Missing YOUTUBE_API_KEY or GEMINI_API_KEY')

    existing = load_existing()
    known = {a.get('video_id') for a in existing}
    client = genai.Client(api_key=GEMINI_KEY)

    candidates = []
    for code, cfg in COUNTRIES.items():
        try:
            found = yt_search(code, cfg)
            print(f'[search:{code}] {len(found)} candidates')
            candidates.extend(found)
        except Exception as e:
            print(f'[search:{code}] ERROR {e}')

    candidates = [
        c for c in {x['video_id']: x for x in candidates}.values()
        if c['video_id'] not in known
    ]
    if MAX_ANALYZE > 0:
        candidates = candidates[:MAX_ANALYZE]
    print(f'new candidates to analyze: {len(candidates)}')

    accepted = []
    for i, c in enumerate(candidates, 1):
        try:
            a = analyze_with_retry(client, c)
            print(f"[{i}/{len(candidates)}] {c['country']} keep={a.get('keep')} score={a.get('radar_score')} {c['video_title'][:70]}")
            if a.get('keep'):
                rec = {
                    **c, **a,
                    'id': hashlib.sha1(c['video_id'].encode()).hexdigest()[:12],
                    'discovered_at': datetime.now(timezone.utc).isoformat()
                }
                rec['tags'] = [
                    t for t in rec.get('tags', [])
                    if t in {'IDEA','COPY','DIRECTION','ART','CINEMATOGRAPHY','EDIT','VFX','AI','COMEDY','STORYTELLING','SCALE','WEIRD'}
                ]
                accepted.append(rec)
        except Exception as e:
            print(f"[analyze:{c['video_id']}] ERROR {e}")
        time.sleep(1.5)

    merged = accepted + existing
    merged.sort(key=lambda x: x.get('published_at') or x.get('discovered_at') or '', reverse=True)
    DATA.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'accepted: {len(accepted)} / archive total: {len(merged)}')


if __name__ == '__main__':
    main()
