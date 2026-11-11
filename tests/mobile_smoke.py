from pathlib import Path
from playwright.sync_api import sync_playwright
import json
root=Path(__file__).resolve().parents[1]
css=(root/'static/app.css').read_text()
html=(root/'static/index.html').read_text().replace('<link rel="stylesheet" href="/app.css">','<style>'+css+'</style>').replace('<script src="/app.js" defer></script>','')
js=(root/'static/app.js').read_text()
names=['Front Driveway','Front Entrance','Garage','Side Gate','Backyard','Rear Gate','Parking Lot','Front Street','Workshop','Storage Room','Office','Server Room']
cams=[dict(id=i+1,name=n,host=f'192.168.1.{101+i}',recording=False,enabled=True,active=False,username='',main_path='/Streaming/Channels/101',sub_path='/Streaming/Channels/102',record_format='mp4',recording_mode='continuous',snapshot_count=3) for i,n in enumerate(names)]
cams[0]['talk_enabled']=True  # Example compatible camera; Talk remains disabled for viewers by default.
out=root/'screenshots';out.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 for width in [360,390,430]:
  for role in ['admin','viewer']:
   page=browser.new_page(viewport={'width':width,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
   errors=[]
   page.on('pageerror',lambda e: errors.append(str(e)))
   page.set_content(html)
   if width==390 and role=='admin':page.screenshot(path=str(out/'mobile-login-new.png'),full_page=True)
   script='''window.fetch=async url=>{let d=String(url).includes('/api/me')?{username:'DemoUser',role:ROLE,must_change:false}:String(url).includes('/api/cameras')?CAMS:String(url).includes('/api/status')?{storage_mounted:false,recorders:0,memory_mb:4096,temperature_c:null,live_relay:false}:String(url).includes('/api/clips')?[]:String(url).includes('/api/snapshots')?[]:String(url).includes('/api/users')?[]:String(url).includes('/api/network')?{port:8080}:String(url).includes('/api/events')?[]:{};return {ok:true,json:async()=>d};}'''.replace('ROLE',json.dumps(role)).replace('CAMS',json.dumps(cams))
   page.evaluate(script)
   page.add_script_tag(content=js)
   page.locator('#console').wait_for(state='visible')
   page.wait_for_timeout(100)
   metrics=page.evaluate('''()=>({body:document.body.scrollWidth,viewport:innerWidth,cols:getComputedStyle(document.querySelector('#cameraGrid')).gridTemplateColumns,nav:getComputedStyle(document.querySelector('#mobileNav')).display,live:document.querySelector('#page-live').classList.contains('active'),buttons:[...document.querySelectorAll('#mobileNav .mobile-tab:not([hidden])')].map(x=>x.textContent.trim())})''')
   assert metrics['body']<=width+2,(width,role,metrics)
   assert len(metrics['cols'].split(' '))==2,metrics
   assert metrics['nav']=='flex',metrics
   assert page.locator('#talkPanel').is_visible() == (role=='admin'), 'Talk panel permission visibility mismatch'
   if role=='viewer': assert len(metrics['buttons'])==2,metrics
   if width==390:
    page.screenshot(path=str(out/f'mobile-{role}-live-new.png'),full_page=True)
    page.locator('[data-mobile-page="playback"]').click()
    assert page.locator('#page-playback').evaluate('(e)=>e.classList.contains("active")')
    page.screenshot(path=str(out/f'mobile-{role}-playback-new.png'),full_page=True)
    if role=='admin':
     page.locator('[data-mobile-page="devices"]').click()
     page.locator('#addCamera').click()
     page.screenshot(path=str(out/'mobile-admin-camera-setup-new.png'),full_page=True)
     page.locator('[data-mobile-page="settings"]').click()
     page.screenshot(path=str(out/'mobile-admin-settings-new.png'),full_page=True)
   print(width,role,metrics,'errors',errors[:3])
   assert not errors,errors
   page.close()
 browser.close()