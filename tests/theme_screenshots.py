"""Capture real PiNVR HTML/CSS screenshots for both themes and new features.

Only illustrative demo camera data is used. No camera credentials, network or
actual NVR instance are required. Run from the repository root with Playwright.
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'screenshots'
OUT.mkdir(exist_ok=True)
html=(ROOT/'static/index.html').read_text()
css=(ROOT/'static/app.css').read_text()
js=(ROOT/'static/app.js').read_text()
html=html.replace('<link rel="stylesheet" href="/app.css">','<style>'+css+'</style>')
html=html.replace('<script src="/app.js" defer></script>','')
names=['Driveway','Front Door','Garage','Side Gate','Backyard','Rear Gate','Parking Lot','Front Street','Workshop','Storage Room','Office','Server Room']
cams=[{'id':i+1,'name':name,'host':f'192.168.1.{101+i}',
       'recording':False,'enabled':True,'active':False,'username':'',
       'main_path':'/Streaming/Channels/101','sub_path':'/Streaming/Channels/102',
       'record_format':'mp4','recording_mode':'motion','snapshot_count':3,
       'motion_detection':i<4,'motion_sensitivity':5,'talk_enabled':i==0}
      for i,name in enumerate(names)]
mock=r"""window.fetch=async url=>{const q=String(url);let data=
q.includes('/api/me')?{username:'Admin',role:'admin',must_change:false,talk_allowed:true}:
q.includes('/api/cameras/test-connection')?{ok:true,saved:false,tested:true,streams:[
 {stream:'main',ok:true,codec:'h264',width:1920,height:1080,fps:25,audio:['aac'],message:'H.264 detected — recommended for PiNVR browser viewing.'},
 {stream:'substream',ok:true,codec:'h264',width:640,height:360,fps:15,audio:[],message:'H.264 detected — recommended for PiNVR browser viewing.'}]}:
q.includes('/api/cameras')?CAMS:
q.includes('/api/alerts/destinations')?[]:
q.includes('/api/users')?[]:
q.includes('/api/network')?{port:8080,managed:false}:
q.includes('/api/status')?{storage_mounted:false,recorders:0,memory_mb:4096,temperature_c:null,live_relay:false}:
q.includes('/api/storage/drives')?{drives:[],current:{mounted:false}}:
q.includes('/api/clips')?[]:
q.includes('/api/snapshots')?[]:
q.includes('/api/events/motion')?[]:{};
return {ok:true,status:200,json:async()=>data};};"""
mock=mock.replace('CAMS',json.dumps(cams))

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'])
    desktop=browser.new_page(viewport={'width':1460,'height':890},device_scale_factor=1)
    desktop.set_content(html)
    desktop.evaluate("document.documentElement.dataset.theme='red'")
    desktop.screenshot(path=str(OUT/'theme-red-login.png'),full_page=True)
    desktop.evaluate("document.documentElement.dataset.theme='blue'")
    desktop.screenshot(path=str(OUT/'theme-blue-login.png'),full_page=True)
    desktop.evaluate(mock)
    desktop.add_script_tag(content=js)
    desktop.locator('#console').wait_for(state='visible')
    desktop.wait_for_timeout(300)
    for theme in ['red','blue']:
        desktop.evaluate("(theme)=>document.documentElement.dataset.theme=theme",theme)
        desktop.screenshot(path=str(OUT/f'theme-{theme}-desktop.png'),full_page=True)
    desktop.locator('[data-page="settings"]').click()
    desktop.locator('#appearanceSettingsCard').screenshot(path=str(OUT/'theme-selector-settings.png'))
    desktop.locator('#alertSettingsCard').screenshot(path=str(OUT/'alerts-notifications-settings.png'))
    desktop.locator('[data-page="devices"]').click()
    desktop.locator('#addCamera').click()
    desktop.locator('#camName').fill('Front Door')
    desktop.locator('#camHost').fill('192.168.1.108')
    desktop.locator('#testRtsp').click()
    desktop.locator('#rtspTestResults .rtsp-stream-row').first.wait_for()
    desktop.locator('#rtspTestResults').screenshot(path=str(OUT/'rtsp-connection-test.png'))
    phone=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
    phone.set_content(html)
    phone.evaluate(mock)
    phone.add_script_tag(content=js)
    phone.locator('#console').wait_for(state='visible')
    phone.evaluate("document.documentElement.dataset.theme='blue'")
    phone.screenshot(path=str(OUT/'theme-blue-mobile.png'),full_page=True)
    phone.evaluate("document.documentElement.dataset.theme='red'")
    phone.screenshot(path=str(OUT/'theme-red-mobile.png'),full_page=True)
    browser.close()
print('Generated login, desktop, phone, settings, alert and RTSP screenshots from actual PiNVR UI.')
