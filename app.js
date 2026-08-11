const state={ads:[],country:'ALL',tag:'ALL',query:'',savedOnly:false,saved:new Set(JSON.parse(localStorage.getItem('adRadarSaved')||'[]'))};
const flags={JP:'🇯🇵',TH:'🇹🇭',US:'🇺🇸',GB:'🇬🇧'};const names={JP:'JAPAN',TH:'THAILAND',US:'USA',GB:'UK'};
const $=s=>document.querySelector(s);const grid=$('#grid'),tpl=$('#cardTemplate'),empty=$('#empty');

async function load(){
  try{const r=await fetch(`data/ads.json?v=${Date.now()}`);state.ads=await r.json();}
  catch(e){console.error(e);state.ads=[]}
  updateStats();render();
}
function updateStats(){
  $('#totalCount').textContent=state.ads.length;
  const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'});const today=fmt.format(new Date());
  $('#todayCount').textContent=state.ads.filter(x=>x.discovered_at&&fmt.format(new Date(x.discovered_at))===today).length;
  $('#savedCount').textContent=state.saved.size;
  const latest=state.ads.map(x=>x.discovered_at).filter(Boolean).sort().at(-1);
  if(latest) $('#updateLabel').textContent=`UPDATED ${latest.slice(0,10).replaceAll('-','.')}`;
}
function filtered(){
  const q=state.query.trim().toLowerCase();
  return state.ads.filter(a=>{
    if(state.country!=='ALL'&&a.country!==state.country)return false;
    if(state.tag!=='ALL'&&!(a.tags||[]).includes(state.tag))return false;
    if(state.savedOnly&&!state.saved.has(a.id))return false;
    if(q){const hay=[a.brand,a.campaign,a.why_ko,a.copy_ko,a.copy_note_ko,a.agency,a.director,...(a.tags||[])].filter(Boolean).join(' ').toLowerCase();if(!hay.includes(q))return false}
    return true;
  }).sort((a,b)=>new Date(b.published_at||b.discovered_at)-new Date(a.published_at||a.discovered_at));
}
function render(){
  grid.innerHTML='';const ads=filtered();$('#resultCount').textContent=ads.length;empty.classList.toggle('hidden',ads.length>0);grid.classList.toggle('hidden',ads.length===0);
  const label=state.savedOnly?'MY ARCHIVE':state.country==='ALL'?'ALL COUNTRIES':names[state.country];$('#feedEyebrow').textContent=`LATEST / ${label}`;
  $('#feedTitle').textContent=state.savedOnly?'내가 저장한 광고':'오늘 건질 광고';
  ads.forEach(a=>{const n=tpl.content.cloneNode(true);const card=n.querySelector('.card');
    const link=card.querySelector('.thumb-wrap');link.href=a.url||'#';const img=card.querySelector('.thumb');img.src=a.thumbnail||'';img.alt=`${a.brand||''} ${a.campaign||''}`;
    card.querySelector('.country-badge').textContent=`${flags[a.country]||''} ${names[a.country]||a.country||''}`;
    card.querySelector('.score').textContent=a.radar_score??'—';card.querySelector('.brand-name').textContent=(a.brand||'UNKNOWN').toUpperCase();
    card.querySelector('.date').textContent=(a.published_at||'').slice(0,10).replaceAll('-','.');card.querySelector('.title').textContent=a.campaign||a.video_title||'UNTITLED';
    const tags=card.querySelector('.tags');(a.tags||[]).forEach(t=>{const s=document.createElement('span');s.className='tag';s.textContent=t;tags.appendChild(s)});
    card.querySelector('.why').textContent=a.why_ko||'';
    const copyBox=card.querySelector('.copy-box');if(a.copy_ko||a.copy_original){copyBox.classList.remove('hidden');card.querySelector('.original-copy').textContent=a.copy_original||'';card.querySelector('.ko-copy').textContent=a.copy_ko||'';card.querySelector('.copy-note').textContent=a.copy_note_ko||'';}
    card.querySelector('.agency').textContent=a.agency||'UNKNOWN';card.querySelector('.director').textContent=a.director||'UNKNOWN';const watch=card.querySelector('.watch');watch.href=a.url||'#';
    const btn=card.querySelector('.save-btn');const sync=()=>{const yes=state.saved.has(a.id);btn.textContent=yes?'♥ SAVED':'♡ SAVE';btn.classList.toggle('saved',yes)};sync();btn.onclick=()=>{state.saved.has(a.id)?state.saved.delete(a.id):state.saved.add(a.id);localStorage.setItem('adRadarSaved',JSON.stringify([...state.saved]));sync();updateStats();if(state.savedOnly)render()};
    grid.appendChild(n);
  });
}
$('#countryFilters').addEventListener('click',e=>{const b=e.target.closest('[data-country]');if(!b)return;document.querySelectorAll('[data-country]').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.country=b.dataset.country;render()});
$('#tagFilter').onchange=e=>{state.tag=e.target.value;render()};$('#searchInput').oninput=e=>{state.query=e.target.value;render()};$('#savedToggle').onclick=e=>{state.savedOnly=!state.savedOnly;e.currentTarget.classList.toggle('active',state.savedOnly);render()};
load();