(()=>{'use strict';
const $=s=>document.querySelector(s),$$=s=>Array.from(document.querySelectorAll(s));

const THEME_KEY='pinvr-color-theme';
function chooseTheme(value){
 const theme=value==='blue'?'blue':'red';
 document.documentElement.dataset.theme=theme;
 const favicon=document.querySelector('link[rel="icon"]');
 if(favicon){favicon.href='/brand-icon-'+theme+'.png';favicon.type='image/png';}
 const manifest=document.querySelector('link[rel="manifest"]');
 if(manifest)manifest.href='/manifest-'+theme+'.webmanifest';
 for(const select of ['#loginTheme','#settingsTheme']){
  const el=$(select);if(el)el.value=theme;
 }
 $$('[data-theme-choice]').forEach(btn=>btn.setAttribute('aria-pressed',String(btn.dataset.themeChoice===theme)));
 try{localStorage.setItem(THEME_KEY,theme)}catch(e){/* Private browsing or storage policy: theme works for this page. */}
}
let initialTheme='red';
try{initialTheme=localStorage.getItem(THEME_KEY)||'red'}catch(e){}
chooseTheme(initialTheme);
$('#loginTheme').addEventListener('change',e=>chooseTheme(e.target.value));
$('#settingsTheme').addEventListener('change',e=>chooseTheme(e.target.value));
$$('[data-theme-choice]').forEach(btn=>btn.addEventListener('click',()=>chooseTheme(btn.dataset.themeChoice)));

const phoneLayout=()=>window.matchMedia('(max-width:680px)').matches;
let cameras=[],selected=null,layout=phoneLayout()?4:12,group='all',focused=false,user=null,changing=false,liveRelay=false;let peers=[];let talkPeer=null,talkStream=null,talkAttempt=0,talkHeld=false,talkCameraId=null,listenEnabled=false,listenCameraId=null;
const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opts={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opts.headers||{})},...opts});let body=await r.json().catch(()=>({}));if(!r.ok)throw Error(body.detail||`HTTP ${r.status}`);return body;}
let toastTimer;function toast(s){$('#toast').textContent=s;$('#toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').style.display='none',3500)}
function showAuth(change=false){$('#auth').hidden=false;$('#console').hidden=true;changing=change;$('#loginFields').hidden=change;$('#changeFields').hidden=!change;$('#authTitle').textContent=change?'Secure your new recorder':'Sign in to your recorder';$('#authSubmit').textContent=change?'Update password':'Sign in';$('#authHint').textContent=change?'Change your password (optional but recommended)':'Native Raspberry Pi NVR · Android & iPhone ready';$('#authError').textContent='';}
async function start(){try{user=await api('/api/me');await enter()}catch(e){showAuth()}}
async function enter(){$('#auth').hidden=true;$('#console').hidden=false;const admin=user.role==='admin';$$('.admin-only').forEach(x=>x.hidden=!admin);$('#viewerNote').hidden=admin;$('#signedInName').textContent=user.username;$('#passwordReminder').hidden=!user.must_change;if(admin)await Promise.all([getCameras(),loadStatus()]);else{liveRelay=true;await getCameras();$('#liveNote').textContent='Viewer access: live cameras and recording playback. Camera settings and administration are restricted.';}showPage('live')}
$('#loginForm').addEventListener('submit',async e=>{e.preventDefault();$('#authError').textContent='';try{if(changing){if($('#newPassword').value!==$('#confirmPassword').value)throw Error('New passwords do not match');await api('/api/password',{method:'POST',body:JSON.stringify({current:$('#oldPassword').value,new:$('#newPassword').value})});$('#oldPassword').value=$('#newPassword').value=$('#confirmPassword').value='';user=await api('/api/me');await enter();toast('Password updated successfully')}else{user=await api('/api/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});$('#password').value='';await enter()}}catch(err){$('#authError').textContent=err.message}});
$('#reminderLater').onclick=()=>{$('#passwordReminder').hidden=true};
$('#reminderChange').onclick=$('#accountPassword').onclick=()=>showAuth(true);
$('#cancelPasswordChange').onclick=()=>enter();
$('#logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'})}catch(e){}showAuth()});
function showPage(p){stopTalking();if(p!=='live')setListening(false);if(user?.role==='viewer'&&!['live','playback'].includes(p))return;if(p!=='live')closePeers();$$('.page').forEach(x=>x.classList.toggle('active',x.id==='page-'+p));$$('.tab').forEach(x=>x.classList.toggle('active',x.dataset.page===p));$$('[data-mobile-page]').forEach(x=>{const active=x.dataset.mobilePage===p;x.classList.toggle('active',active);if(active)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current')});if(p==='playback'){loadClips();loadSnapshots()}if(p==='events')loadMotionEvents();if(p==='settings'){loadStatus();loadNetwork();loadUsers();loadStorageDrives();loadAlerts()}}
$$('.tab').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.page)));
$$('[data-mobile-page]').forEach(b=>b.addEventListener('click',()=>{showPage(b.dataset.mobilePage);window.scrollTo({top:0,behavior:'instant'})}));
if(phoneLayout()){$$('[data-layout]').forEach(x=>x.classList.toggle('active',Number(x.dataset.layout)===4));}
async function getCameras(){try{cameras=await api('/api/cameras');if(!cameras.some(c=>c.id===selected))selected=cameras[0]?.id||null;render()}catch(e){toast(e.message)}}
function render(){const find=cameras.find(c=>c.id===selected);if(talkCameraId!==null&&talkCameraId!==selected)stopTalking();if(listenEnabled&&listenCameraId!==selected)setListening(false);const canTalk=!!find?.talk_enabled&&(user?.role==='admin'||!!user?.talk_allowed);$('#talkPanel').hidden=!canTalk;if(!canTalk&&listenEnabled)setListening(false);if(canTalk)$('#talkHint').textContent='Hold to speak through '+find.name+'. Compatible camera and HTTPS required.';$('#selectedName').textContent=find?.name||'No camera selected';$('#selectedIP').textContent=user?.role==='viewer'?'Hidden':find?.host||'—';$('#selectedState').textContent=find?(find.enabled?'Configured':'Disabled'):'—';$('#selectedRecording').textContent=user?.role==='viewer'?'Hidden':find?(find.active?'Active':find.recording?'Waiting':'Off'):'—';$('#totalCount').textContent=`(${cameras.length})`;$('#wallCount').textContent=`${cameras.length} camera profiles`;
const q=$('#search').value.trim().toLowerCase();$('#tree').innerHTML=cameras.filter(c=>c.name.toLowerCase().includes(q)).map(c=>`<button class="resource child ${c.id===selected?'active':''}" data-select="${c.id}"><i class="dot"></i> ${escapeHTML(c.name)}</button>`).join('');
const visible=cameras.filter(c=>group==='all'||c.group===group).slice(0,layout);let list=focused?cameras.filter(c=>c.id===selected):visible;const cols=focused?1:phoneLayout()?(layout===1?1:2):(layout===1?1:layout===4?2:layout===9?3:4);$('#cameraGrid').style.gridTemplateColumns=`repeat(${cols},minmax(0,1fr))`;$('#cameraGrid').classList.toggle('single-camera',focused||layout===1);$('#cameraGrid').innerHTML=list.length?list.map(c=>`<button class="camera ${c.id===selected?'selected':''}" data-select="${c.id}" ${focused?'style="aspect-ratio:16/8;max-height:460px"':''}><span class="camicon">▣</span><video class="livevideo" data-feed="${c.id}" muted autoplay playsinline></video><span class="name"><span><i class="dot"></i> ${escapeHTML(c.name)}</span><span class="rec">${user?.role==='viewer'?'LIVE':c.active?'● REC':'RTSP'}</span></span><span class="camfoot"><span>CH ${c.id}</span><span>No live preview</span></span></button>`).join(''):'<div class="card"><h3>No cameras configured</h3><p>Add an RTSP camera under Devices to start continuous recording.</p></div>';
if(liveRelay && $('#page-live').classList.contains('active'))startLiveFeeds();
$('#deviceList').innerHTML=user?.role==='viewer'?'':cameras.map(c=>`<div class="devicerow"><div><b>${escapeHTML(c.name)}</b><small>${escapeHTML(c.host)}</small></div><span class="tag">${c.active?'● Recording':c.recording?'Waiting':'Off'}</span><div><button data-edit="${c.id}">Edit</button><button data-delete="${c.id}">Delete</button></div></div>`).join('')||'<p class="muted">No cameras configured yet.</p>';
{const oldSelected=$('#clipCamera').value;$('#clipCamera').innerHTML='<option value="">All cameras</option>'+cameras.map(c=>`<option value="${c.id}">${escapeHTML(c.name)}</option>`).join('');if([...$('#clipCamera').options].some(x=>x.value===oldSelected))$('#clipCamera').value=oldSelected}}
$('#search').addEventListener('input',render);document.addEventListener('click',e=>{const b=e.target.closest('[data-select]');if(b){selected=Number(b.dataset.select);render()}});
$$('[data-layout]').forEach(b=>b.addEventListener('click',()=>{layout=Number(b.dataset.layout);focused=false;$$('[data-layout]').forEach(x=>x.classList.toggle('active',x===b));render()}));
$('#focus').onclick=()=>{focused=!focused;render()};$('#showAll').onclick=()=>{focused=false;render()};$('#refresh').onclick=getCameras;$('#cameraRefresh').onclick=getCameras;
function openEditor(id=null){const c=cameras.find(x=>x.id===id);$('#rtspTestResults').textContent='Enter camera details, then select Test RTSP Connection. No settings will be saved.';$('#editor').hidden=false;$('#cameraId').value=c?.id||'';$('#editHeading').textContent=c?'Edit '+c.name:'Add camera';$('#camName').value=c?.name||'';$('#camHost').value=c?.host||'';$('#camUser').value=c?.username||'';$('#camPassword').value='';$('#camMain').value=c?.main_path||'/Streaming/Channels/101';$('#camSub').value=c?.sub_path||'/Streaming/Channels/102';$('#camRecord').checked=c?.recording||false;$('#camTalkEnabled').checked=!!c?.talk_enabled;$('#camMode').value=c?.recording_mode||'continuous';$('#camFormat').value=c?.record_format||'mp4';$('#camSnapshots').value=String(c?.snapshot_count||1);$('#camEnabled').checked=c?.enabled??true;showPage('devices');$('#editor').scrollIntoView({behavior:'smooth',block:'nearest'})}
$('#addSide').onclick=()=>openEditor();$('#addCamera').onclick=()=>openEditor();$('#closeEditor').onclick=$('#cancelEdit').onclick=()=>{$('#editor').hidden=true};$('#openCameraEdit').onclick=()=>selected?openEditor(selected):toast('Select a camera first');
$('#deviceList').onclick=async e=>{const edit=e.target.closest('[data-edit]'),del=e.target.closest('[data-delete]');if(edit)openEditor(Number(edit.dataset.edit));if(del&&confirm('Delete camera profile? Existing recordings will be kept.')){try{await api('/api/cameras/'+del.dataset.delete,{method:'DELETE'});toast('Camera removed');await getCameras()}catch(e){toast(e.message)}}};
$('#cameraForm').onsubmit=async e=>{e.preventDefault();const id=$('#cameraId').value;const cam={name:$('#camName').value.trim(),host:$('#camHost').value.trim(),username:$('#camUser').value.trim(),password:$('#camPassword').value,main_path:$('#camMain').value.trim(),sub_path:$('#camSub').value.trim(),recording:$('#camRecord').checked,recording_mode:$('#camMode').value,record_format:$('#camFormat').value,snapshot_count:Number($('#camSnapshots').value),talk_enabled:$('#camTalkEnabled').checked,enabled:$('#camEnabled').checked};try{await api('/api/cameras'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(cam)});$('#editor').hidden=true;await getCameras();toast('Camera saved')}catch(e){toast(e.message)}};

/* Validate the current form without saving or changing any camera settings. */
$('#testRtsp').onclick=async()=>{
 const panel=$('#rtspTestResults'),button=$('#testRtsp');
 const name=$('#camName').value.trim(),host=$('#camHost').value.trim();
 const main=$('#camMain').value.trim(),sub=$('#camSub').value.trim();
 if(!name||!host||!main||!sub){
   panel.textContent='Enter the camera name, IP address and both stream paths first.';
   return;
 }
 const camera={name,host,username:$('#camUser').value.trim(),password:$('#camPassword').value,
   main_path:main,sub_path:sub,enabled:$('#camEnabled').checked};
 const cameraId=Number($('#cameraId').value)||null;
 button.disabled=true;button.textContent='⏳ Testing main and substreams…';
 panel.textContent='Connecting to the camera from the Raspberry Pi. This may take up to 20 seconds. No settings will be saved.';
 try{
   const data=await api('/api/cameras/test-connection',{method:'POST',
     body:JSON.stringify({camera,camera_id:cameraId})});
   const results=data.streams||[];
   panel.innerHTML='<strong class="'+(data.ok?'rtsp-success':'rtsp-warning')+'">'+
      (data.ok?'✓ Both RTSP streams are reachable':'⚠ One or more streams need attention')+'</strong>'+
      results.map(r=>'<div class="rtsp-stream-row"><div><strong>'+escapeHTML(r.stream==='main'?'Main Stream':'Substream')+
       '</strong><span class="'+(r.ok?'rtsp-success':'rtsp-warning')+'">'+(r.ok?'✓ Connected':'✕ Not connected')+'</span></div>'+
       (r.ok?'<small>'+escapeHTML(String(r.codec||'unknown').toUpperCase())+' · '+
         escapeHTML(r.width&&r.height?r.width+' × '+r.height:'Unknown resolution')+
         (r.fps?' · '+escapeHTML(r.fps)+' FPS':'')+
         (r.audio?.length?' · audio: '+escapeHTML(r.audio.join(', ')):'')+
         '</small>':'')+
       '<p>'+escapeHTML(r.message||'')+'</p></div>').join('')+
       '<p class="rtsp-help">This is a connection test only. No camera was saved and no recording was started.</p>';
 }catch(err){panel.textContent='Test could not finish: '+err.message;}
 finally{button.disabled=false;button.textContent='⌕ Test RTSP Connection'}
};
$('#discover').onclick=async()=>{toast('Searching local network for ONVIF cameras…');try{const info=await api('/api/onvif/discover');if(!info.devices.length){toast('No ONVIF cameras replied. You can add RTSP cameras manually.');return}alert(info.devices.map(d=>d.ip+'\n'+d.service_urls.join('\n')).join('\n\n'));}catch(e){toast(e.message)}};$('#ptzInfo').onclick=()=>toast('PTZ controls require ONVIF integration.');
async function loadStatus(){try{let s=await api('/api/status');const changed=liveRelay!==!!s.live_relay;liveRelay=!!s.live_relay;if(changed)render();$('#ramValue').textContent=s.memory_mb?Math.round(s.memory_mb/1024*10)/10+' GB':'—';$('#tempValue').textContent=s.temperature_c===null?'—':s.temperature_c+' °C';$('#workersValue').textContent=s.recorders;$('#storageValue').textContent=s.storage_mounted?Math.round(s.storage_free/1e9)+' GB':'Not mounted';$('#mountWarning').hidden=s.storage_mounted;$('#connection').innerHTML='<i class="dot"></i> Connected';$('#liveNote').textContent=liveRelay?'WebRTC active. The first four visible streams use substreams. Select Focus for a full-resolution stream; remaining feeds load only when requested.':'go2rtc live relay is not running. Verify the native binary is installed and restart PiNVR.';}catch(e){toast(e.message)}}
async function loadClips(){try{const id=$('#clipCamera').value;let clips=await api('/api/clips'+(id?'?camera_id='+id:''));$('#clipList').innerHTML=clips.length?clips.map(c=>{const name=cameras.find(x=>x.id===c.camera_id)?.name||'Camera '+c.camera_id;return `<button class="clip" data-clip="${c.camera_id}/${encodeURIComponent(c.name)}"><span>${escapeHTML(name)} — ${escapeHTML(c.name)}</span><span>${(c.size/1e6).toFixed(1)} MB ▷</span></button>`}).join(''):'<div class="card"><p>No recordings found yet. Enable recording on a camera and verify your USB drive is mounted.</p></div>'}catch(e){toast(e.message)}}
async function loadSnapshots(){try{const id=$('#clipCamera').value;const shots=await api('/api/snapshots'+(id?'?camera_id='+id:''));$('#snapshotList').innerHTML=shots.length?shots.map(s=>`<a target="_blank" rel="noopener" href="/api/snapshots/${s.camera_id}/${encodeURIComponent(s.name)}"><img loading="lazy" alt="JPG snapshot" src="/api/snapshots/${s.camera_id}/${encodeURIComponent(s.name)}"><small>${escapeHTML(cameras.find(c=>c.id===s.camera_id)?.name||'Camera '+s.camera_id)} · ${escapeHTML(s.name)}</small></a>`).join(''):'<p class="muted">No JPG snapshots found.</p>';}catch(e){toast(e.message)}}
async function loadMotionEvents(){try{let data=await api('/api/events/motion');$('#eventList').innerHTML=data.length?data.map(x=>`<div class="clip">${escapeHTML(cameras.find(c=>c.id===x.camera_id)?.name||'Camera '+x.camera_id)} · ${new Date(x.created_at*1000).toLocaleString()} · ${x.snapshot_count} JPG(s)</div>`).join(''):'<p class="muted">No events captured yet. Use the Test Motion button or an authenticated event bridge.</p>'}catch(e){toast(e.message)}}
$('#refreshEvents').onclick=loadMotionEvents;
$('#reloadSnapshots').onclick=loadSnapshots;
$('#testMotion').onclick=async()=>{const cid=$('#cameraId').value;if(!cid){toast('Save the camera before testing motion');return}try{const r=await api('/api/cameras/'+cid+'/test-motion',{method:'POST'});toast('Motion test started. It may take several seconds to save JPGs and a clip.');}catch(e){toast(e.message)}};
$('#eventKeyButton').onclick=async()=>{const cid=$('#cameraId').value;if(!cid){toast('Save the camera first');return}if(!confirm('Generate a new motion webhook key? Any old key will stop working.'))return;try{const r=await api('/api/cameras/'+cid+'/motion-token',{method:'POST'});prompt('Save this secret somewhere secure. It is shown once. Send it in X-PiNVR-Event-Key to POST /api/events/motion/'+cid,r.token);}catch(e){toast(e.message)}};
$('#clipList').onclick=e=>{const b=e.target.closest('[data-clip]');if(!b)return;const url='/api/clips/'+b.dataset.clip;if(b.dataset.clip.endsWith('.mkv')){toast('MKV is a download format; open it in VLC or another compatible player.');window.open(url,'_blank','noopener');return}$('#player').src=url;$('#emptyPlayback').hidden=true;$('#player').play().catch(()=>{})};$('#reloadClips').onclick=loadClips;$('#clipCamera').onchange=()=>{loadClips();loadSnapshots()};$('#openRecordings').onclick=$('#footerPlayback').onclick=()=>showPage('playback');

function closePeers(){peers.forEach(p=>{try{p.close()}catch(e){}});peers=[];}
async function startLiveFeeds(){
 closePeers();
 const items=Array.from(document.querySelectorAll('#cameraGrid video[data-feed]')).slice(0,focused?1:4);
 for(const video of items){
   const cid=Number(video.dataset.feed);const pc=new RTCPeerConnection({iceServers:[]});peers.push(pc);
   pc.addTransceiver('video',{direction:'recvonly'});
   const playAudio=listenEnabled&&focused&&cid===selected;
   if(playAudio)pc.addTransceiver('audio',{direction:'recvonly'});
   video.muted=!playAudio;
   pc.ontrack=e=>{const stream=video.srcObject instanceof MediaStream?video.srcObject:new MediaStream();if(!stream.getTracks().some(t=>t.id===e.track.id))stream.addTrack(e.track);video.srcObject=stream;video.play().catch(()=>{if(playAudio)toast('Tap Listen again to allow audio playback.');});};
   try{
     await pc.setLocalDescription(await pc.createOffer());
     if(pc.iceGatheringState!=='complete') await new Promise(resolve=>{const timer=setTimeout(resolve,1800);pc.addEventListener('icegatheringstatechange',()=>{if(pc.iceGatheringState==='complete'){clearTimeout(timer);resolve();}})});
     const res=await fetch('/api/live/'+cid+'/offer?profile='+(focused?'main':'sub'),{method:'POST',headers:{'Content-Type':'application/sdp'},body:pc.localDescription.sdp});
     if(!res.ok)throw Error('WebRTC offer failed');
     const sdp=await res.text();await pc.setRemoteDescription({type:'answer',sdp});
   }catch(e){pc.close();video.title='Live stream unavailable. Check H.264 camera profile, relay and network.';}
 }
}



const alertHelp={
 ntfy:'ntfy://YOUR_PRIVATE_TOPIC (subscribe to the topic in the ntfy phone app; use an unguessable topic or authenticated server).',
 pushover:'pover://USER_KEY@APP_TOKEN (get both values in your Pushover account).',
 telegram:'tgram://BOT_TOKEN/CHAT_ID (create a Telegram bot and get your chat ID).',
 email:'mailtos://USERNAME:PASSWORD@SMTP_HOST?to=recipient@example.com (SMTP service settings vary).',
 discord:'discord://WEBHOOK_ID/WEBHOOK_TOKEN (copy the Discord channel webhook credentials).',
 slack:'slack://TOKEN_A/TOKEN_B/TOKEN_C (or use a compatible Microsoft Teams Apprise URL).',
 webhook:'jsons://your-server.example.com/alert (HTTPS JSON webhook; avoid localhost and internal addresses).'
};
function resetAlertEditor(){
 $('#alertEditId').value='';$('#alertForm').reset();
 $('#alertURL').required=true;$('#alertURL').value='';
 $('#alertURL').placeholder='Example: ntfy://your-private-topic';
 $('#saveAlertDestination').textContent='Save Alert Destination';
 $('#alertProviderHelp').textContent=alertHelp[$('#alertProvider').value];
}
$('#alertProvider').onchange=()=>{$('#alertProviderHelp').textContent=alertHelp[$('#alertProvider').value]||''};
$('#resetAlertForm').onclick=resetAlertEditor;
async function loadAlerts(){
 if(user?.role!=='admin')return;
 try{
  const saved=await api('/api/alerts/destinations');
  $('#alertDestinationList').innerHTML=saved.length?saved.map(item=>
    '<div class="alert-destination"><div><strong>'+escapeHTML(item.label)+'</strong>'+
    '<small>'+escapeHTML(item.provider.toUpperCase())+' · '+(item.enabled?'Enabled':'Disabled')+
    ' · '+(item.motion?'Motion ':'')+(item.storage?'Storage ':'')+
    ' · '+escapeHTML(item.cooldown_seconds)+'s cooldown</small></div>'+
    '<div class="actions"><button class="outline" data-alert-test="'+item.id+'">Send Test</button>'+
    '<button class="outline" data-alert-edit="'+item.id+'">Edit</button>'+
    '<button class="outline" data-alert-delete="'+item.id+'">Remove</button></div></div>'
  ).join(''):'<p class="muted">No destinations configured yet. Add one below, then use Send Test to verify delivery.</p>';
  window.__pinvrAlertDestinations=saved;
 }catch(err){$('#alertDestinationList').textContent='Could not load alert destinations: '+err.message;}
}
$('#alertDestinationList').addEventListener('click',async e=>{
 const btn=e.target.closest('button');if(!btn)return;
 const id=btn.dataset.alertTest||btn.dataset.alertEdit||btn.dataset.alertDelete;
 const item=(window.__pinvrAlertDestinations||[]).find(x=>String(x.id)===String(id));if(!item)return;
 if(btn.hasAttribute('data-alert-edit')){
   $('#alertEditId').value=String(item.id);
   $('#alertName').value=item.label;
   $('#alertProvider').value=({ntfy:'ntfy',ntfys:'ntfy',pover:'pushover',tgram:'telegram',
       mailto:'email',mailtos:'email',discord:'discord',discords:'discord',slack:'slack',msteams:'slack',
       jsons:'webhook'})[item.provider]||'webhook';
   $('#alertProviderHelp').textContent=alertHelp[$('#alertProvider').value];
   $('#alertURL').value='';$('#alertURL').required=false;
   $('#alertURL').placeholder='Leave blank to keep saved credentials';
   $('#alertMotion').checked=item.motion;$('#alertStorage').checked=item.storage;
   $('#alertCooldown').value=String(item.cooldown_seconds);
   if($('#alertCooldown').value==='')$('#alertCooldown').value='120';
   $('#alertEnabled').checked=item.enabled;
   $('#saveAlertDestination').textContent='Save Changes';
   $('#alertForm').scrollIntoView({behavior:'smooth',block:'nearest'});
   return;
 }
 if(btn.hasAttribute('data-alert-delete')){
   if(!confirm('Remove alert destination '+item.label+'?'))return;
   try{await api('/api/alerts/destinations/'+id,{method:'DELETE'});toast('Alert destination removed');await loadAlerts();}catch(err){toast(err.message)}
   return;
 }
 btn.disabled=true;
 try{
   const result=await api('/api/alerts/destinations/'+id+'/test',{method:'POST'});
   toast(result.message||'Test notification sent. Check your device.');
 }catch(err){toast('Test failed: '+err.message)}
 finally{btn.disabled=false}
});
$('#alertForm').onsubmit=async e=>{
 e.preventDefault();
 if(!$('#alertMotion').checked&&!$('#alertStorage').checked){toast('Choose at least one event type.');return}
 const id=$('#alertEditId').value;
 const payload={label:$('#alertName').value.trim(),url:$('#alertURL').value.trim()||(id?'KEEP_EXISTING':''),
 enabled:$('#alertEnabled').checked,motion:$('#alertMotion').checked,storage:$('#alertStorage').checked,
 cooldown_seconds:Number($('#alertCooldown').value)};
 try{
   await api('/api/alerts/destinations'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(payload)});
   toast(id?'Alert destination updated':'Alert destination added');
   resetAlertEditor();await loadAlerts();
 }catch(err){toast('Unable to save alert destination: '+err.message)}
};

async function loadNetwork(){try{const info=await api('/api/network');$('#webPort').value=info.port;$('#networkNotice').textContent=info.managed?`Current PiNVR port: ${info.port}. Your LAN/VPN address uses :${info.port}.`:'Preview/development mode: install as a systemd service to change ports from here.';$('#saveWebPort').disabled=!info.managed;}catch(e){$('#networkNotice').textContent='Could not read network settings: '+e.message}}
$('#networkForm').addEventListener('submit',async e=>{e.preventDefault();const port=Number($('#webPort').value);const old=Number(location.port)||(location.protocol==='https:'?443:80);if(!Number.isInteger(port)||port<1024||port>65535){toast('Choose a port between 1024 and 65535');return}if(!confirm(`Change PiNVR web access to port ${port}? The server will restart, and you must reconnect. VPN and router rules may also need updating.`))return;try{const r=await api('/api/network',{method:'POST',body:JSON.stringify({port,current_password:$('#portPassword').value})});$('#portPassword').value='';if(r.restarting){const newUrl=new URL(location.href);newUrl.port=String(r.port);newUrl.pathname='/';newUrl.search='';newUrl.hash='';$('#networkNotice').innerHTML='Port change saved. PiNVR is restarting. <a id="newNetworkLink" href="#">Open new PiNVR address</a>. If it fails to reconnect, check the system service and firewall.';$('#newNetworkLink').href=newUrl.href;$('#newNetworkLink').textContent=newUrl.href;toast('Port changed to '+r.port+'. Open the new URL.');}else toast('Port unchanged');}catch(err){toast(err.message);$('#networkNotice').textContent=err.message}});

async function loadUsers(){if(user?.role!=='admin')return;try{const members=await api('/api/users');$('#userList').innerHTML=members.map(m=>`<div class="user-row"><div><b>${escapeHTML(m.username)}</b><small>${m.role==='admin'?'Admin · Full Control':'Viewer · Live Cameras & Playback'}${m.must_change?' · Password change recommended':''}</small></div>${m.role==='viewer'?`<div class="actions"><button class="outline" data-talk-user="${escapeHTML(m.username)}" data-talk-allowed="${m.talk_allowed?1:0}">${m.talk_allowed?'Disable Talk':'Allow Talk'}</button><button class="outline" data-user-reset="${escapeHTML(m.username)}">Reset Password</button><button class="outline" data-user-delete="${escapeHTML(m.username)}">Remove</button></div>`:'<span class="tag">Protected</span>'}</div>`).join('')}catch(e){toast(e.message)}}
$('#viewerForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/users',{method:'POST',body:JSON.stringify({username:$('#viewerUsername').value.trim(),password:$('#viewerPassword').value,talk_allowed:$('#viewerTalkAllowed').checked})});$('#viewerForm').reset();toast('Viewer created; password-change recommendation will appear at login');await loadUsers()}catch(err){toast(err.message)}};
$('#userList').onclick=async e=>{const reset=e.target.closest('[data-user-reset]'),del=e.target.closest('[data-user-delete]'),talk=e.target.closest('[data-talk-user]');if(talk){const name=talk.dataset.talkUser;const allow=talk.dataset.talkAllowed!=='1';if(!confirm((allow?'Allow ':'Disable ')+'Talk for '+name+'?'))return;try{await api('/api/users/'+encodeURIComponent(name)+'/talk',{method:'PUT',body:JSON.stringify({talk_allowed:allow})});toast('Viewer Talk permission changed');await loadUsers()}catch(err){toast(err.message)}return}if(reset){const name=reset.dataset.userReset;const pw=prompt('New temporary password for '+name+' (12+ characters):');if(pw===null)return;try{await api('/api/users/'+encodeURIComponent(name)+'/password',{method:'PUT',body:JSON.stringify({password:pw})});toast('Password reset; viewer sessions revoked');await loadUsers()}catch(err){toast(err.message)}}if(del){const name=del.dataset.userDelete;if(!confirm('Remove viewer '+name+' and revoke their sessions?'))return;try{await api('/api/users/'+encodeURIComponent(name),{method:'DELETE'});toast('Viewer removed');await loadUsers()}catch(err){toast(err.message)}}};
async function loadStorageDrives(){
 const select=$('#usbDrive');
 $('#driveSetupStatus').textContent='Checking connected USB drives…';
 try{
  const data=await api('/api/storage/drives');
  select.innerHTML='<option value="">Select a USB drive</option>'+data.drives.map(d=>`<option value="${escapeHTML(d.uuid)}">${escapeHTML(d.model||'USB Drive')} · ${(d.size_bytes/1e9).toFixed(0)} GB · ${escapeHTML(d.filesystem)} · ${escapeHTML(d.device)}${d.mounted?' (already mounted)':''}</option>`).join('');
  $('#driveSetupStatus').textContent=data.current.mounted?'Recording drive mounted. '+data.current.details:data.drives.length?data.drives.length+' available drive(s)':'No supported free USB drives found';
 }catch(e){select.innerHTML='<option value="">Storage scan unavailable</option>';$('#driveSetupStatus').textContent=e.message;}
}
$('#scanDrives').onclick=loadStorageDrives;
$('#driveSetupForm').onsubmit=async e=>{
 e.preventDefault();const selectedOption=$('#usbDrive').selectedOptions[0];
 if(!$('#usbDrive').value)return toast('Select a USB drive first');
 if(!confirm('Set up '+selectedOption.textContent+' as the PiNVR recording drive? No files will be formatted or erased.'))return;
 const btn=$('#driveSetupForm button[type="submit"]');btn.disabled=true;
 try{
  const res=await api('/api/storage/configure',{method:'POST',body:JSON.stringify({uuid:$('#usbDrive').value,current_password:$('#driveAdminPassword').value})});
  $('#driveAdminPassword').value='';$('#driveConfirm').checked=false;toast(res.message);await loadStorageDrives();await loadStatus();
 }catch(err){toast('USB setup: '+err.message)}finally{btn.disabled=false}
};

function setListening(enabled){
  listenEnabled=enabled;
  listenCameraId=enabled?selected:null;
  $('#listenButton').textContent=enabled?'◖ Stop listening':'◖ Listen';
  $('#listenButton').setAttribute('aria-pressed',String(enabled));
  if(enabled){focused=true;render();}
  else if($('#page-live').classList.contains('active'))startLiveFeeds();
}
$('#listenButton').addEventListener('click',()=>setListening(!listenEnabled));

/* Microphone is used only while the button is pressed. */
function talkStatus(message){$('#talkState').textContent=message;$('#talkButton').classList.toggle('transmitting',message==='Transmitting…')}
function stopTalking(){
  talkHeld=false;talkAttempt++;
  if(talkPeer){try{talkPeer.close()}catch(e){}talkPeer=null;}
  if(talkStream){talkStream.getTracks().forEach(track=>track.stop());talkStream=null;}
  talkCameraId=null;talkStatus('Microphone off');
}
async function startTalking(e){
  if(e.button!==undefined&&e.button!==0)return;
  if(talkHeld)return;
  const cam=cameras.find(c=>c.id===selected);
  if(!cam?.talk_enabled||!(user?.role==='admin'||user?.talk_allowed))return;
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){toast('Talk requires HTTPS, even when connected over VPN.');return;}
  e.preventDefault();talkHeld=true;const attempt=++talkAttempt;talkCameraId=cam.id;
  talkStatus('Opening microphone…');
  try{
    const media=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if(!talkHeld||attempt!==talkAttempt){media.getTracks().forEach(x=>x.stop());return;}
    talkStream=media;
    const pc=new RTCPeerConnection({iceServers:[]});talkPeer=pc;
    pc.addTransceiver(media.getAudioTracks()[0],{direction:'sendonly',streams:[media]});
    pc.onconnectionstatechange=()=>{
      if(pc.connectionState==='connected'&&talkHeld)talkStatus('Transmitting…');
      else if(['failed','disconnected','closed'].includes(pc.connectionState)&&talkHeld){stopTalking();toast('Talk connection lost. Check camera compatibility.')}
    };
    await pc.setLocalDescription(await pc.createOffer());
    if(pc.iceGatheringState!=='complete')await new Promise(resolve=>{
      const timer=setTimeout(resolve,1400);
      pc.addEventListener('icegatheringstatechange',()=>{if(pc.iceGatheringState==='complete'){clearTimeout(timer);resolve();}});
    });
    if(!talkHeld||attempt!==talkAttempt)return;
    const res=await fetch('/api/talk/'+cam.id+'/offer',{method:'POST',headers:{'Content-Type':'application/sdp'},body:pc.localDescription.sdp});
    if(!res.ok){let err=await res.json().catch(()=>({}));throw Error(err.detail||'Camera audio backchannel unavailable');}
    if(!talkHeld||attempt!==talkAttempt)return;
    await pc.setRemoteDescription({type:'answer',sdp:await res.text()});
    if(talkHeld&&attempt===talkAttempt)talkStatus('Connecting to speaker…');
  }catch(err){if(attempt===talkAttempt){stopTalking();toast('Talk unavailable: '+err.message)}}
}
const talkButton=$('#talkButton');
talkButton.addEventListener('pointerdown',startTalking);
for(const type of ['pointerup','pointercancel','lostpointercapture'])talkButton.addEventListener(type,stopTalking);
document.addEventListener('pointerup',()=>{if(talkHeld)stopTalking()});
document.addEventListener('pointercancel',()=>{if(talkHeld)stopTalking()});
window.addEventListener('blur',stopTalking);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTalking()});
talkButton.addEventListener('keydown',e=>{if((e.key===' '||e.key==='Enter')&&!e.repeat){e.preventDefault();startTalking({button:0,preventDefault:()=>{}})}});
talkButton.addEventListener('keyup',e=>{if(e.key===' '||e.key==='Enter')stopTalking()});
talkButton.addEventListener('contextmenu',e=>e.preventDefault());

$('#changeButton').onclick=()=>showAuth(true);
start();
})();
