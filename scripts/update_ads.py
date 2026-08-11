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
LOOKBACK_HOURS = int(os.environ.get('LOOKBACK_HOURS', '168'))
MAX_PER_QUERY = int(os.environ.get('MAX_PER_QUERY', '10'))
MAX_ANALYZE = int(os.environ.get('MAX_ANALYZE', '16'))

# Search broadly, curate brutally. Daily de-duplication means a 7-day window is safe and
# catches campaigns that YouTube search indexes late.
COUNTRIES = {
    'JP': {
        'region': 'JP', 'lang': 'ja',
        'queries': [
            '2026 新CM 公式', '2026 TVCM 新CM', '2026 Web CM ブランド',
            '2026 広告 キャンペーン ムービー'
        ]
    },
    'TH': {
        'region': 'TH', 'lang': 'th',
        'queries': [
            '2026 โฆษณา ใหม่ official', '2026 โฆษณาไทย แบรนด์',
            '2026 Thailand commercial official', '2026 Thailand campaign film'
        ]
    },
    'US': {
        'region': 'US', 'lang': 'en',
        'queries': [
            '2026 official commercial brand', '2026 new advertising campaign film',
            '2026 brand campaign official film', '2026 commercial official ad'
        ]
    },
    'GB': {
        'region': 'GB', 'lang': 'en',
        'queries': [
            '2026 UK advert official', '2026 British advertising campaign film',
            '2026 new UK commercial official', '2026 UK brand campaign film'
        ]
    },
}

# Obvious non-source uploads should never consume Gemini video analysis quota.
REJECT_PATTERNS = [
    r'never\s+skip', r'ads?\s+are\s+(pure\s+)?comedy', r'funny\s+ads?',
    r'best\s+(thai\s+)?ads?', r'top\s*\d+.*ads?', r'commercials?\s+compilation',
    r'advertisements?\s+compilation', r'compilation', r'reaction', r'reacts?\s+to',
    r'tamil\s+dub', r'dubbed', r'#shorts', r'#viral', r'#memes?', r'meme',
    r'history\s+of', r'explained', r'breakdown', r'analysis', r'tutorial',
    r'how\s+to\s+(run|make|create).*ads?', r'marketing\s+tips?',
    r'ยิง\s*ads', r'สอน.*ads', r'โค้ช.*ads', r'เบื้องหลัง', r'behind\s+the\s+scenes',
]
REJECT_RE = re.compile('|'.join(REJECT_PATTERNS), re.I)

ALLOWED_TAGS = {
    'IDEA','COPY','DIRECTION','ART','CINEMATOGRAPHY','EDIT','VFX','AI',
    'COMEDY','STORYTELLING','SCALE','WEIRD'
}

SYSTEM = '''You are a ruthless senior advertising creative director curating references for a Korean creative director.
Your first job is SOURCE INTEGRITY. The item must be a newly published, single, actual advertisement/campaign film, not somebody reposting an old ad for entertainment.
Reject compilations, reactions, commentary, meme edits, dubbed versions, old-ad reuploads, fan uploads, tutorials, marketing education, award/case-study explainers, behind-the-scenes, interviews, trailers, music videos, news clips, and videos merely discussing advertising.
Prefer uploads from the brand, agency, production company, director, or a credible advertising-industry publisher carrying the complete original campaign film. If the metadata/source makes freshness or authenticity doubtful, reject it.

Only after source integrity passes, judge creative quality. Reject generic product demos, conventional celebrity endorsements, ordinary beauty/fashion montages, plain talking-head brand films, simple product prettiness, generic emotional brand films, or work whose only merit is production polish.
Prefer a strong visual device, directing idea, editing structure, art direction, cinematography, VFX/AI technique, scale, strange casting, comedy, storytelling mechanic, human insight, copy twist, linguistic wit, or an idea that can be explained in one sharp sentence.
Country bias: Japan -> scrutinize copy/linguistic insight; Thailand -> visual idea/directing/comedy; USA & UK -> scale/edit/craft/big idea.
All explanatory output must be Korean. Never invent credits, dates, claims, or copy. If uncertain use UNKNOWN.'''

PROMPT = '''Watch the attached public YouTube video and inspect its supplied metadata. Return JSON only, exactly this shape:
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

Rules:
- is_actual_ad=true ONLY for a complete single advertisement/campaign film. Reuploads/compilations/commentary/memes/dubs are false.
- source_confidence 0-100 = confidence this upload is a legitimate source of this campaign film, not random content farming.
- freshness_confidence 0-100 = confidence the campaign itself is genuinely recent, rather than an old famous ad reuploaded recently.
- keep can be true ONLY when is_actual_ad=true, source_confidence>=75, freshness_confidence>=65 and radar_score>=74.
- radar_score 0-100 is strict creative-reference value, not production quality alone.
- Allowed tags: IDEA,COPY,DIRECTION,ART,CINEMATOGRAPHY,EDIT,VFX,AI,COMEDY,STORYTELLING,SCALE,WEIRD.
- why_ko: 2-4 concise Korean sentences explaining the reusable creative mechanism, not a plot summary or praise.
- Only include copy actually spoken/shown and creatively meaningful. Translate it naturally into Korean.
- copy_note_ko should explain insight, wordplay, reversal or rhetorical mechanism. If none, use empty string.
- Never infer agency/director. UNKNOWN when not reliably stated in the video/metadata.

Metadata follows:
'''


def obvious_reject(c):
    hay = f"{c.get('video_title','')} {c.get('description','')} {c.get('channel','')}"
    return bool(REJECT_RE.search(hay))


def yt_search(country, cfg):
    after = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat().replace('+00:00', 'Z')
    out = []
    for q in cfg['queries']:
        p = {
            'part': 'snippet', 'q': q, 'type': 'video', 'order': 'date',
            'maxResults': MAX_PER_QUERY, 'publishedAfter': after,
            'regionCode': cfg['region'], 'relevanceLanguage': cfg['lang'], 'key': YT_KEY,
            'safeSearch': 'none'
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
            c = {
                'country': country,
                'video_id': vid,
                'url': f'https://www.youtube.com/watch?v={vid}',
                'video_title': sn.get('title', ''),
                'description': sn.get('description', ''),
                'channel': sn.get('channelTitle', ''),
                'published_at': sn.get('publishedAt', ''),
                'thumbnail': thumb.get('url', '')
            }
            if not obvious_reject(c):
                out.append(c)
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
        f"youtube_upload_date={c['published_at']}\n"
        f"title={c['video_title']}\n"
        f"channel={c['channel']}\n"
        f"description={c['description'][:2400]}"
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


def valid_accept(a):
    try:
        return (
            a.get('is_actual_ad') is True and
            int(a.get('source_confidence', 0)) >= 75 and
            int(a.get('freshness_confidence', 0)) >= 65 and
            int(a.get('radar_score', 0)) >= 74 and
            a.get('keep') is True
        )
    except Exception:
        return False


def candidate_priority(c):
    text = f"{c.get('video_title','')} {c.get('channel','')} {c.get('description','')}".lower()
    score = 0
    for token in ('official', 'tvc', 'tvcm', 'campaign', 'commercial', 'advert', 'cm', 'ブランド', '公式', 'โฆษณา'):
        if token in text:
            score += 1
    return score


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
            print(f'[search:{code}] {len(found)} prefiltered candidates')
            candidates.extend(found)
        except Exception as e:
            print(f'[search:{code}] ERROR {e}')

    candidates = [
        c for c in {x['video_id']: x for x in candidates}.values()
        if c['video_id'] not in known
    ]
    candidates.sort(key=lambda c: (candidate_priority(c), c.get('published_at','')), reverse=True)
    if MAX_ANALYZE > 0:
        candidates = candidates[:MAX_ANALYZE]
    print(f'new candidates to analyze: {len(candidates)}')

    accepted = []
    for i, c in enumerate(candidates, 1):
        try:
            a = analyze_with_retry(client, c)
            print(
                f"[{i}/{len(candidates)}] {c['country']} actual={a.get('is_actual_ad')} "
                f"source={a.get('source_confidence')} fresh={a.get('freshness_confidence')} "
                f"keep={a.get('keep')} score={a.get('radar_score')} {c['video_title'][:60]}"
            )
            if valid_accept(a):
                rec = {
                    **c, **a,
                    'id': hashlib.sha1(c['video_id'].encode()).hexdigest()[:12],
                    'discovered_at': datetime.now(timezone.utc).isoformat()
                }
                rec['tags'] = [t for t in rec.get('tags', []) if t in ALLOWED_TAGS]
                accepted.append(rec)
        except Exception as e:
            print(f"[analyze:{c['video_id']}] ERROR {e}")
        time.sleep(1.2)

    merged = accepted + existing
    merged.sort(key=lambda x: x.get('published_at') or x.get('discovered_at') or '', reverse=True)
    DATA.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'accepted: {len(accepted)} / archive total: {len(merged)}')


if __name__ == '__main__':
    main()
