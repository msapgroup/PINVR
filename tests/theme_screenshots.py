"""Rebuild EVERY repository screenshot from the current PiNVR browser interface.

Uses real app HTML/CSS/JS and the real red/blue logo PNGs, with clearly labeled
example camera records. No actual camera footage, NVR hardware or credentials.
Run: python tests/theme_screenshots.py
"""
from pathlib import Path
import base64
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)
html = (ROOT / "static/index.html").read_text()
css = (ROOT / "static/app.css").read_text()
js = (ROOT / "static/app.js").read_text()
html = html.replace('<link rel="stylesheet" href="/app.css">', "<style>" + css + "</style>")
html = html.replace('<script src="/app.js" defer></script>', "")
for color in ("red", "blue"):
    logo = (ROOT / "static" / f"brand-logo-{color}.png").read_bytes()
    html = html.replace(f'src="/brand-logo-{color}.png"',
                        'src="data:image/png;base64,' + base64.b64encode(logo).decode() + '"')

names = ["Front Driveway", "Front Entrance", "Garage", "Side Gate",
         "Backyard", "Rear Gate", "Parking Lot", "Front Street",
         "Workshop", "Storage Room", "Office", "Server Room"]
cameras = [{"id":i+1, "name":name, "host":f"192.168.1.{101+i}",
            "recording":False, "enabled":True, "active":False,
            "username":"", "main_path":"/Streaming/Channels/101",
            "sub_path":"/Streaming/Channels/102", "record_format":"mp4",
            "recording_mode":"motion" if i<4 else "continuous",
            "snapshot_count":3, "motion_detection":i<4,
            "motion_sensitivity":5, "talk_enabled":i==0}
           for i,name in enumerate(names)]

# Mock ONLY the API: the real PiNVR UI still runs, including permissions,
# active navigation, theme persistence, and camera/editing form behaviour.
fixture = """
window.__PINVR_ROLE__ = ROLE;
window.fetch=async (url, options={})=>{
 const path=String(url);
 let data =
 path.includes('/api/me') ? {username:window.__PINVR_ROLE__==='admin'?'Admin':'FamilyViewer',role:window.__PINVR_ROLE__,talk_allowed:window.__PINVR_ROLE__==='admin',must_change:false} :
 path.includes('/api/cameras/test-connection') ? {ok:true, saved:false, tested:true, streams:[
    {stream:'main',ok:true,codec:'h264',width:1920,height:1080,fps:25,audio:['aac'],message:'H.264 detected — recommended for browser viewing.'},
    {stream:'substream',ok:true,codec:'h264',width:640,height:360,fps:15,audio:[],message:'H.264 detected — recommended for browser viewing.'}
 ]} :
 path.includes('/api/cameras') ? CAMERAS :
 path.includes('/api/users') ? [{username:'Admin',name:'Admin',role:'admin',must_change:false,talk_allowed:true},
 {username:'FamilyViewer',name:'FamilyViewer',role:'viewer',must_change:false,talk_allowed:false},
 {username:'FrontDesk',name:'FrontDesk',role:'viewer',must_change:false,talk_allowed:true}] :
 path.includes('/api/alerts/destinations') ? [] :
 path.includes('/api/network') ? {port:8080,managed:true} :
 path.includes('/api/storage/drives') ? {drives:[],current:{mounted:false}} :
 path.includes('/api/status') ? {storage_mounted:false,recorders:0,memory_mb:4096,temperature_c:null,live_relay:false} :
 path.includes('/api/clips') ? [] :
 path.includes('/api/snapshots') ? [] :
 path.includes('/api/events') ? [] : {};
 return {ok:true,status:200,json:async()=>data};
};
""".replace("ROLE",json.dumps("admin")).replace("CAMERAS",json.dumps(cameras))

def screenshot(page, name, full=False):
    path = OUT / name
    opts = dict(path=str(path), full_page=full, animations="disabled")
    if name.endswith(".jpg"):
        opts.update(type="jpeg", quality=82)
    else:
        opts.update(type="png")
    page.screenshot(**opts)
    assert path.stat().st_size > 3000, f"Screenshot unexpectedly empty: {name}"

def theme(page, value):
    page.evaluate("(value)=>document.documentElement.dataset.theme=value", value)
    page.wait_for_timeout(90)
    assert page.locator(f".header .logo-{value}").is_visible() if page.locator("#console").is_visible() else page.locator(f".auth-card .logo-{value}").is_visible()
    other = "blue" if value == "red" else "red"
    assert not (page.locator(f".header .logo-{other}").is_visible() if page.locator("#console").is_visible() else page.locator(f".auth-card .logo-{other}").is_visible())

def boot(browser, role="admin", mobile=False):
    opts = dict(viewport={"width":390, "height":844} if mobile else {"width":1460, "height":920},
                device_scale_factor=1)
    if mobile:
        opts.update(is_mobile=True, has_touch=True)
    page = browser.new_page(**opts)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.set_content(html)
    page.evaluate(fixture.replace('"admin"',json.dumps(role),1))
    page.add_script_tag(content=js)
    page.locator("#console").wait_for(state="visible", timeout=7000)
    page.wait_for_timeout(160)
    assert not errors, f"{role} JS error: {errors}"
    assert page.locator(".header .logo-red").is_visible()
    assert not page.locator(".header .logo-blue").is_visible()
    assert page.locator(".brand-logo.logo-red").first.evaluate("(el)=>el.complete && el.naturalWidth>0")
    return page

def go(page, value, mobile=False):
    selector = f'[data-mobile-page="{value}"]' if mobile else f'[data-page="{value}"]'
    page.locator(selector).click()
    page.wait_for_timeout(160)
    assert page.locator("#page-"+value).evaluate("(x)=>x.classList.contains('active')")
    return page

def scroll_card(page, selector):
    page.locator(selector).scroll_into_view_if_needed()
    page.wait_for_timeout(100)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,args=["--no-sandbox"])
    desktop = browser.new_page(viewport={"width":1460,"height":920},device_scale_factor=1)
    desktop.set_content(html)
    theme(desktop, "red")
    screenshot(desktop,"theme-red-login.png",full=True)
    screenshot(desktop,"login.jpg",full=True)
    theme(desktop,"blue")
    screenshot(desktop,"theme-blue-login.png",full=True)
    desktop.close()

    desktop = boot(browser)
    for color in ("red","blue"):
        theme(desktop,color)
        screenshot(desktop,f"theme-{color}-desktop.png",full=True)
    theme(desktop,"red")
    screenshot(desktop,"desktop-live.jpg",full=True)
    desktop.evaluate("document.querySelector('#passwordReminder').hidden=false")
    screenshot(desktop,"desktop-password-reminder.jpg")
    desktop.evaluate("document.querySelector('#passwordReminder').hidden=true")

    go(desktop,"playback")
    screenshot(desktop,"desktop-playback.jpg",full=True)
    go(desktop,"devices")
    desktop.locator("#addCamera").click()
    desktop.locator("#camName").fill("Front Driveway")
    desktop.locator("#camHost").fill("192.168.1.101")
    screenshot(desktop,"desktop-cameras.jpg",full=True)
    screenshot(desktop,"desktop-recording-options.jpg",full=True)
    desktop.locator("#camMotionDetect").check()
    desktop.locator("#camMode").select_option("motion")
    desktop.locator("#camSnapshots").select_option("7")
    desktop.locator("#camMotionSensitivity").fill("7")
    screenshot(desktop,"desktop-video-motion-detection.jpg",full=True)
    desktop.locator("#testRtsp").click()
    desktop.locator(".rtsp-stream-row").first.wait_for()
    # Include branded header and the device editor, not just an isolated widget.
    screenshot(desktop,"rtsp-connection-test.png",full=True)

    go(desktop,"settings")
    screenshot(desktop,"theme-selector-settings.png",full=True)
    for selector, path in [
        (".network-card","desktop-network.jpg"),
        ("#storageSetupCard","desktop-storage-setup.png"),
        ("#alertSettingsCard","alerts-notifications-settings.png"),
        ("#viewerForm","desktop-users.jpg"),
        ("#viewerForm","desktop-viewer-talk-permissions.jpg")]:
        scroll_card(desktop,selector)
        screenshot(desktop,path)
    desktop.close()

    phone_login=browser.new_page(viewport={"width":390,"height":844},device_scale_factor=1,
                                 is_mobile=True,has_touch=True)
    phone_login.set_content(html)
    theme(phone_login,"red")
    screenshot(phone_login,"mobile-login-new.jpg",full=True)
    phone_login.close()
    for role in ("admin","viewer"):
        phone=boot(browser,role=role,mobile=True)
        for color in ("red","blue"):
            theme(phone,color)
            if role=="admin":
                screenshot(phone,f"theme-{color}-mobile.png",full=True)
        theme(phone,"red")
        screenshot(phone,"mobile-"+role+"-live-new.jpg",full=True)
        if role=="admin":
            screenshot(phone,"mobile-live.jpg",full=True)
            screenshot(phone,"mobile-push-to-talk.jpg",full=True)
        else:
            screenshot(phone,"mobile-viewer.jpg",full=True)
        go(phone,"playback",mobile=True)
        screenshot(phone,"mobile-"+role+"-playback-new.jpg",full=True)
        if role=="viewer":
            screenshot(phone,"mobile-viewer-playback.jpg",full=True)
        else:
            go(phone,"devices",mobile=True)
            phone.locator("#addCamera").click()
            screenshot(phone,"mobile-admin-camera-setup-new.jpg",full=True)
        phone.close()
    browser.close()

expected={
 "desktop-live.jpg","desktop-cameras.jpg","desktop-playback.jpg",
 "desktop-recording-options.jpg","desktop-video-motion-detection.jpg",
 "desktop-users.jpg","mobile-viewer.jpg","mobile-admin-live-new.jpg",
 "mobile-admin-playback-new.jpg","mobile-admin-camera-setup-new.jpg",
 "mobile-viewer-live-new.jpg","mobile-viewer-playback-new.jpg",
 "mobile-login-new.jpg","mobile-live.jpg","desktop-storage-setup.png",
 "mobile-push-to-talk.jpg","desktop-viewer-talk-permissions.jpg",
 "desktop-network.jpg","login.jpg","theme-red-desktop.png",
 "theme-blue-desktop.png","theme-selector-settings.png",
 "theme-red-login.png","theme-blue-login.png","theme-red-mobile.png",
 "theme-blue-mobile.png","rtsp-connection-test.png",
 "alerts-notifications-settings.png","desktop-password-reminder.jpg",
 "mobile-viewer-playback.jpg"
}
missing=[name for name in expected if not (OUT/name).exists()]
assert not missing, f"Missing screenshot files: {missing}"
print(f"Captured {len(expected)} current PiNVR screenshot files with final red/blue logos.")
