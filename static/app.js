const state = { uploadId:null, kind:null, meta:{}, lastFrame:null, lastResultId:null, library:[] };

// ---------- tabs ----------
document.querySelectorAll('.tab-btn').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('section.card').forEach(x=>x.classList.add('hidden'));
  b.classList.add('active');
  document.getElementById('tab-'+b.dataset.tab).classList.remove('hidden');
  if(b.dataset.tab==='results') loadHistory();
}));

// ---------- library / analyte selects ----------
async function loadLibrary(){
  const res = await fetch('/api/library');
  state.library = await res.json();
  const libSel = document.getElementById('analyte-lib');
  const activeSel = document.getElementById('active-analyte');
  libSel.innerHTML=''; activeSel.innerHTML='';
  state.library.forEach(c=>{
    const label = c.analyte.replace(/_/g,' ');
    libSel.appendChild(new Option(label, c.analyte));
    activeSel.appendChild(new Option(label, c.analyte));
  });
  libSel.appendChild(new Option('+ new custom analyte…','__new__'));
  loadCalibrationInto(state.library[0]);
}
document.getElementById('analyte-lib').addEventListener('change', e=>{
  if(e.target.value==='__new__'){
    const name = prompt('Name this new analyte (no spaces):','custom_analyte');
    if(!name) { e.target.value = state.library[0].analyte; return; }
    const opt = new Option(name, name); 
    e.target.insertBefore(opt, e.target.lastChild); e.target.value=name;
    document.querySelector('#cal-table tbody').innerHTML='';
    addRow(); addRow(); addRow();
    document.getElementById('conc-units').value='µM';
    resetStats();
    return;
  }
  const curve = state.library.find(c=>c.analyte===e.target.value);
  loadCalibrationInto(curve);
});

function resetStats(){
  document.getElementById('stat-slope').textContent='—';
  document.getElementById('stat-intercept').textContent='—';
  document.getElementById('stat-r2').textContent='—';
  const c=document.getElementById('cal-canvas'); c.getContext('2d').clearRect(0,0,c.width,c.height);
}

function loadCalibrationInto(curve){
  const tbody=document.querySelector('#cal-table tbody'); tbody.innerHTML='';
  if(!curve){ resetStats(); return; }
  document.getElementById('conc-units').value = curve.units;
  curve.points.forEach(([c,i])=>addRow(c,i));
  if(curve.slope!=null){
    document.getElementById('stat-slope').textContent=curve.slope.toFixed(4);
    document.getElementById('stat-intercept').textContent=curve.intercept.toFixed(3);
    document.getElementById('stat-r2').textContent=curve.r2.toFixed(4);
    drawCalPlot(curve.points, curve.slope, curve.intercept);
  }
}

function addRow(c='',i=''){
  const tbody=document.querySelector('#cal-table tbody');
  const tr=document.createElement('tr');
  tr.innerHTML = `<td><input type="number" class="c-val" value="${c}"></td><td><input type="number" class="i-val" value="${i}"></td><td><button class="btn ghost del">✕</button></td>`;
  tbody.appendChild(tr);
  tr.querySelector('.del').addEventListener('click',()=>tr.remove());
}
document.getElementById('add-row').addEventListener('click',()=>addRow());

document.getElementById('save-cal').addEventListener('click', async ()=>{
  const rows=[...document.querySelectorAll('#cal-table tbody tr')].map(tr=>[
    parseFloat(tr.querySelector('.c-val').value), parseFloat(tr.querySelector('.i-val').value)
  ]).filter(([c,i])=>!isNaN(c)&&!isNaN(i));
  const analyte = document.getElementById('analyte-lib').value;
  const units = document.getElementById('conc-units').value;
  const res = await fetch('/api/calibration', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({analyte, units, points: rows})
  });
  const data = await res.json();
  if(data.error){ alert(data.error); return; }
  document.getElementById('stat-slope').textContent=data.slope.toFixed(4);
  document.getElementById('stat-intercept').textContent=data.intercept.toFixed(3);
  document.getElementById('stat-r2').textContent=data.r2.toFixed(4);
  drawCalPlot(data.points, data.slope, data.intercept);
  loadLibrary(); // refresh so Acquire tab's analyte list includes it
});

function drawCalPlot(points,m,b){
  const c=document.getElementById('cal-canvas'); const dctx=c.getContext('2d');
  c.width=c.clientWidth; dctx.clearRect(0,0,c.width,c.height);
  if(!points.length) return;
  const xs=points.map(p=>p[1]), ys=points.map(p=>p[0]);
  const xMin=Math.min(...xs,0), xMax=Math.max(...xs)*1.1||1;
  const yMin=Math.min(...ys,0), yMax=Math.max(...ys)*1.1||1;
  const pad=30;
  const X=v=>pad+(v-xMin)/(xMax-xMin)*(c.width-pad*1.5);
  const Y=v=>c.height-pad-(v-yMin)/(yMax-yMin)*(c.height-pad*1.5);
  dctx.strokeStyle='#1c2e29'; dctx.lineWidth=1;
  dctx.beginPath();dctx.moveTo(pad,c.height-pad);dctx.lineTo(c.width-6,c.height-pad);dctx.moveTo(pad,c.height-pad);dctx.lineTo(pad,10);dctx.stroke();
  dctx.strokeStyle='#5CFFC7'; dctx.lineWidth=2;
  dctx.beginPath(); dctx.moveTo(X(xMin), Y(m*xMin+b)); dctx.lineTo(X(xMax), Y(m*xMax+b)); dctx.stroke();
  dctx.fillStyle='#dff5ec';
  points.forEach(([cVal,iVal])=>{ dctx.beginPath(); dctx.arc(X(iVal),Y(cVal),4,0,7); dctx.fill(); });
}

// ---------- acquire: upload + scrub ----------
const dz=document.getElementById('dz-file');
dz.addEventListener('click',()=>document.getElementById('file-input').click());
document.getElementById('file-input').addEventListener('change', async e=>{
  const f=e.target.files[0]; if(!f) return;
  const fd=new FormData(); fd.append('file', f);
  const res = await fetch('/api/upload', {method:'POST', body:fd});
  const data = await res.json();
  if(data.error){ alert(data.error); return; }
  state.uploadId=data.upload_id; state.kind=data.kind; state.meta=data;

  const scrub=document.getElementById('scrub-controls');
  const slider=document.getElementById('scrub-slider');
  const label=document.getElementById('scrub-label');
  if(data.kind==='image'){
    scrub.classList.add('hidden');
    await showFrame({});
  } else if(data.kind==='gif'){
    scrub.classList.remove('hidden');
    label.textContent = `Scrub frames (0–${data.frame_count-1})`;
    slider.min=0; slider.max=Math.max(0,data.frame_count-1); slider.step=1; slider.value=0;
    await showFrame({frame:0});
  } else {
    scrub.classList.remove('hidden');
    label.textContent = `Scrub to the peak-emission frame (0–${data.duration.toFixed(1)}s)`;
    slider.min=0; slider.max=data.duration; slider.step=0.05; slider.value=0;
    await showFrame({t:0});
  }
});
document.getElementById('scrub-slider').addEventListener('input', e=>{
  const v=parseFloat(e.target.value);
  if(state.kind==='gif') showFrame({frame:Math.round(v)});
  else showFrame({t:v});
});

async function showFrame(params){
  const qs = new URLSearchParams(params).toString();
  const res = await fetch(`/api/frame/${state.uploadId}${qs?'?'+qs:''}`);
  const data = await res.json();
  if(data.error) return;
  state.lastFrame = data;
  document.getElementById('preview-img').src = data.preview;
  document.getElementById('stat-intensity').textContent = data.mean_intensity.toFixed(2);
  document.getElementById('stat-peak').textContent = Math.round(data.peak);
  document.getElementById('stat-area').textContent = data.hotspot_pct.toFixed(1)+'%';
  document.getElementById('preview-wrap').classList.remove('hidden');
}

document.getElementById('use-frame-btn').addEventListener('click', async ()=>{
  if(!state.lastFrame) return;
  const analyte = document.getElementById('active-analyte').value;
  const res = await fetch('/api/predict', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({analyte, intensity: state.lastFrame.mean_intensity})
  });
  const data = await res.json();
  if(data.error){ alert(data.error); return; }
  state.lastResultId = data.id;
  renderResult(data);
  document.querySelector('[data-tab="results"]').click();
});

function renderResult(r){
  document.getElementById('fit-badge').textContent = r.r2>=0.95 ? 'good fit' : 'check calibration — low R²';
  document.getElementById('result-value').textContent = r.concentration.toFixed(3);
  document.getElementById('result-unit').textContent = `estimated concentration (${r.units})`;
  document.getElementById('r-analyte').textContent = r.analyte;
  document.getElementById('r-intensity').textContent = r.intensity.toFixed(2);
  document.getElementById('r-equation').textContent = `c = ${r.slope.toFixed(4)} · I + ${r.intercept.toFixed(3)}`;
  document.getElementById('r-r2').textContent = r.r2.toFixed(4);
  document.getElementById('r-time').textContent = r.created_at;
}

document.getElementById('send-email-btn').addEventListener('click', async ()=>{
  const to = document.getElementById('email-to').value.trim();
  const status = document.getElementById('email-status');
  if(!to || !state.lastResultId){ status.textContent='Enter a recipient and generate a result first.'; return; }
  status.textContent='Sending…';
  const res = await fetch('/api/email', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({to, result_id: state.lastResultId})
  });
  const data = await res.json();
  status.textContent = data.sent ? `Sent to ${data.to}.` : (data.error || 'Send failed.');
});

async function loadHistory(){
  const res = await fetch('/api/results');
  const rows = await res.json();
  const tbody = document.querySelector('#history-table tbody');
  tbody.innerHTML = rows.map(r=>`<tr><td>${r.created_at}</td><td>${r.analyte}</td><td class="mono">${r.concentration.toFixed(3)} ${r.units}</td></tr>`).join('');
}

loadLibrary();
