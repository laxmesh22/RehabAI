const $ = id => document.getElementById(id);
let session = null, timer = null, fault = 'none', busy = false, finishing = false, source = 'simulation';
const names = {abduction:'Sideways arm raise', flexion:'Forward arm raise'};
async function api(path, body) {
  const response = await fetch('/api/'+path, {signal:AbortSignal.timeout(8000),...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function error(message) { $('error').textContent=message; $('error').hidden=false; }
function clearError() {$('error').hidden=true;}
function setRunning(running) {$('settings').disabled=running;$('start').disabled=running;$('stop').disabled=!running;}
function draw(angle) {
  const r=angle*Math.PI/180, x=340+Math.sin(r)*66, y=112+Math.cos(r)*66;
  const wx=340+Math.sin(r)*126, wy=112+Math.cos(r)*126;
  $('arm').setAttribute('d',`M340 112L${x} ${y}L${wx} ${wy}`);
  for (const [id,cx,cy] of [['elbow',x,y],['wrist',wx,wy]]) {$(id).setAttribute('cx',cx);$(id).setAttribute('cy',cy);}
  $('angle-svg').textContent=Math.round(angle)+'°';
  $('skeleton').style.transform=$('side').value==='left'?'scaleX(-1)':'';
  $('angle-svg').style.visibility=$('side').value==='left'?'hidden':'visible';
}
function render(sample) {
  $('angle').innerHTML=sample.valid?`${sample.angle.toFixed(0)}<small>°</small>`:'—';
  $('peak').innerHTML=`${sample.peak.toFixed(0)}<small>°</small>`;
  $('reps').innerHTML=`${sample.reps}<small> / ${$('goal').value}</small>`;
  $('feedback').textContent=sample.feedback;
  $('subfeedback').textContent=sample.valid?'Follow the selected movement at a comfortable pace.':'Partial repetition discarded. Resume from a lowered arm.';
  $('quality').textContent=`${sample.coverage}% valid samples`;
  $('status').textContent=sample.valid?'In progress':'Reset position';
  draw(sample.angle);
}
async function tick() {
  if (!session || busy) return;
  busy=true;
  try {
    const sample=await api('sessions/sample',{id:session.id,fault});render(sample);
    if(sample.complete) {busy=false;await finish('goal_reached');return;}
  } catch(e) {clearInterval(timer);$('status').textContent='Disconnected';$('feedback').textContent='Connection interrupted. Session paused.';error(e.message);}
  finally {busy=false;}
}
$('setup').addEventListener('submit',async event=>{
  event.preventDefault();clearError();$('start').disabled=true;
  try {
    session=await api('sessions/start',{exercise:$('exercise').value,side:$('side').value,target:Number($('target').value),goal:Number($('goal').value),pain:Number($('pain').value)});
    $('pain-after').value=$('pain').value;setRunning(true);$('peak').textContent='—';$('reps').textContent='0';
    timer=setInterval(tick,150);tick();
  } catch(e) {error(e.message);setRunning(false);}
});
async function finish(reason='user_stop') {
  if(!session || finishing)return;
  finishing=true;
  clearInterval(timer);
  const pain=Number($('pain-after').value);
  if(!Number.isInteger(pain)||pain<0||pain>10){error('Session paused. Enter pain from 0 to 10, then press Stop & save.');finishing=false;return;}
  clearInterval(timer);$('stop').disabled=true;
  // Wait for an in-flight sample so it cannot overwrite the saved state.
  while(busy) await new Promise(resolve=>setTimeout(resolve,25));
  try {
    await api('sessions/finish',{id:session.id,pain,reason});session=null;
    setRunning(false);$('status').textContent='Saved';$('feedback').textContent='Session saved to your journal.';$('subfeedback').textContent='Review your movement summary below.';await history();
  } catch(e) {error(e.message);$('stop').disabled=false;}
  finally {finishing=false;}
}
$('stop').addEventListener('click',()=>finish());
$('pain').addEventListener('input',()=>{$('pain-value').textContent=$('pain').value+' / 10';});
$('exercise').addEventListener('change',()=>{$('movement-name').textContent=names[$('exercise').value];$('position').textContent=$('exercise').value==='abduction'?'Face the camera for this movement':'Turn sideways to the camera for this movement';});
document.querySelectorAll('[data-fault]').forEach(button=>button.addEventListener('click',()=>{
  fault=button.dataset.fault;document.querySelectorAll('[data-fault]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});
}));
async function history() {
  try {
    const records=await api('sessions');$('history').replaceChildren();
    if(!records.length){const p=document.createElement('p');p.className='empty';p.textContent='No sessions yet. Your saved summaries will appear here.';$('history').append(p);}
    for(const record of records){
      const row=document.createElement('div');row.className='history-item';
      const content=document.createElement('div'),title=document.createElement('strong'),details=document.createElement('p');
      title.textContent=`${names[record.exercise]} · ${record.side}`;
      details.textContent=`${record.repetitions} reps · ${record.peak}° peak · ${record.coverage}% valid · pain ${record.pain_before} → ${record.pain_after} / 10`;
      const date=document.createElement('p');date.textContent=new Date(record.created_at).toLocaleString()+' · '+(record.source==='live'?'LIVE CAMERA':'SIMULATED');
      content.append(title,details,date);const download=document.createElement('button');download.textContent='Export';download.setAttribute('aria-label','Export session report');
      download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`rehabai-${record.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
      row.append(content,download);$('history').append(row);
    }
  }catch(e){error('Could not load session history: '+e.message);}
}
$('refresh').addEventListener('click',history);
async function init(){
  $('start').disabled=true;
  try{
    const health=await api('health');source=health.source;
    $('source-mode').textContent=source==='live'?'Live camera · unvalidated':'Simulation mode';
    if(source==='live'){
      document.querySelector('.view-label').textContent='LIVE ANGLE · SCHEMATIC VIEW, NOT CAMERA VIDEO';
      $('skeleton').setAttribute('aria-label','Schematic representation of measured arm angle');
      $('start').textContent='Start live session';
      document.querySelector('.checks .eyebrow').textContent='SESSION COMFORT';
      document.querySelector('.checks h2').textContent='Stay within your comfortable range';
      document.querySelector('.checks > p').textContent='Stop whenever you need to. Enter your pain before saving.';
      document.querySelector('.faults').hidden=true;
    }
    const active=await api('active');
    if(active){session={id:active.id};for(const key of ['exercise','side','target','goal'])$(key).value=active[key];$('exercise').dispatchEvent(new Event('change'));$('pain').value=active.pain_before;$('pain').dispatchEvent(new Event('input'));$('pain-after').value=active.pain_before;setRunning(true);$('status').textContent='Interrupted';$('feedback').textContent='An interrupted session was found.';$('subfeedback').textContent='Stop & save it before starting a new session.';}
    else setRunning(false);
    await history();
  }catch(e){error('Application unavailable: '+e.message);}
}
init();
