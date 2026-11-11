from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parent.parent
out=root/'screenshots'
css=(root/'static/app.css').read_text()
js=(root/'static/app.js').read_text()
html=(root/'static/index.html').read_text().replace('<link rel="stylesheet" href="/app.css">','<style>'+css+'</style>').replace('<script src="/app.js" defer></script>','')
names=['Front Driveway','Front Entrance','Garage','Side Gate','Backyard','Rear Gate','Parking Lot','Front Street','Workshop','Storage Room','Office','Server Room']
import json
cams=[{'id':i+1,'name':n,'host':f'192.168.1.{i+101}','recording':False,'enabled':True,'active':False,'username':'','main_path':'/Streaming/Channels/101','sub_path':'/Streaming/Channels/102'} for i,n in enumerate(names)]
mock='''window.fetch=async (url)=>{let data=url.includes('/api/me')?{username:'Admin',must_change:false}:url.includes('/api/cameras')?CAMERA_DATA:url.includes('/api/status')?{storage_mounted:false,recorders:0,memory_mb:4096,temperature_c:null}:url.includes('/api/clips')?[]:{};return {ok:true,json:async()=>data};};'''.replace('CAMERA_DATA',json.dumps(cams))
with sync_playwright() as p:
 b=p.chromium.launch(headless=True, executable_path='/usr/bin/chromium',args=['--no-sandbox','--no-proxy-server'])
 page=b.new_page(viewport={'width':1560,'height':950},device_scale_factor=1)
 page.set_content(html);page.screenshot(path=str(out/'login.png'),full_page=True)
 page.evaluate('(()=>{'+mock+'return true})()');page.add_script_tag(content=js)
 page.locator('#console').wait_for(state='visible')
 page.screenshot(path=str(out/'desktop-live.png'),full_page=True)
 page.locator('[data-page="devices"]').click();page.locator('#addCamera').click();page.screenshot(path=str(out/'desktop-cameras.png'),full_page=True)
 page.locator('[data-page="playback"]').click();page.screenshot(path=str(out/'desktop-playback.png'),full_page=True)
 mobile=b.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
 mobile.set_content(html);mobile.evaluate('(()=>{'+mock+'return true})()');mobile.add_script_tag(content=js)
 mobile.locator('#console').wait_for(state='visible');mobile.screenshot(path=str(out/'mobile-live.png'),full_page=True)
 b.close()
