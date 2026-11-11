"""PiNVR: native RTSP recorder and responsive management UI.

Recording copies the original compressed stream into segments. Browser playback is
provided through compatible MP4 clips; live multi-camera playback requires go2rtc.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import uuid
import xml.etree.ElementTree as ET
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Depends, HTTPException, Request, Response
import httpx
from pinvr import relay, motion, storage, rtsp_test, notifications
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

DATA = Path(os.environ.get('PINVR_DATA', '/var/lib/pinvr'))
RECORDINGS = Path(os.environ.get('PINVR_RECORDINGS', '/mnt/pinvr-recordings'))
STATIC = Path(__file__).resolve().parent.parent / 'static'
DB = DATA / 'pinvr.db'
COOKIE = 'pinvr_session'
SESSION_HOURS = 12
PROCS = {}
LOCK = threading.RLock()
STOP = threading.Event()


def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    return db


def init_db():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    DATA.chmod(0o700)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY COLLATE NOCASE, hash TEXT NOT NULL, must_change INTEGER NOT NULL)')
        cols={x['name'] for x in db.execute('PRAGMA table_info(users)')}
        if 'role' not in cols: db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        if 'talk_allowed' not in cols: db.execute("ALTER TABLE users ADD COLUMN talk_allowed INTEGER NOT NULL DEFAULT 0")
        db.execute('CREATE TABLE IF NOT EXISTS cameras (id INTEGER PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, main_path TEXT NOT NULL, sub_path TEXT NOT NULL, username TEXT, password TEXT, recording INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, created_at TEXT)')
        camera_cols={x['name'] for x in db.execute('PRAGMA table_info(cameras)')}
        if 'record_format' not in camera_cols: db.execute("ALTER TABLE cameras ADD COLUMN record_format TEXT NOT NULL DEFAULT 'mp4'")
        if 'snapshot_count' not in camera_cols: db.execute("ALTER TABLE cameras ADD COLUMN snapshot_count INTEGER NOT NULL DEFAULT 1")
        if 'recording_mode' not in [c['name'] for c in db.execute('PRAGMA table_info(cameras)')]:
            db.execute("ALTER TABLE cameras ADD COLUMN recording_mode TEXT NOT NULL DEFAULT 'continuous'")
        if 'motion_detection' not in [c['name'] for c in db.execute('PRAGMA table_info(cameras)')]:
            db.execute("ALTER TABLE cameras ADD COLUMN motion_detection INTEGER NOT NULL DEFAULT 0")
        if 'talk_enabled' not in camera_cols: db.execute("ALTER TABLE cameras ADD COLUMN talk_enabled INTEGER NOT NULL DEFAULT 0")
        if 'motion_sensitivity' not in [c['name'] for c in db.execute('PRAGMA table_info(cameras)')]:
            db.execute("ALTER TABLE cameras ADD COLUMN motion_sensitivity INTEGER NOT NULL DEFAULT 5")
        db.execute('CREATE TABLE IF NOT EXISTS motion_tokens (camera_id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS motion_events (id INTEGER PRIMARY KEY, camera_id INTEGER NOT NULL, created_at INTEGER NOT NULL, clip_name TEXT, snapshot_count INTEGER DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS alert_destinations (id INTEGER PRIMARY KEY, label TEXT NOT NULL, url TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, events TEXT NOT NULL DEFAULT \'motion\', cooldown_seconds INTEGER NOT NULL DEFAULT 120)')
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('web_port','8080')")
        db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT, expires INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY, camera_id INTEGER, filename TEXT UNIQUE NOT NULL, started_at INTEGER NOT NULL, bytes INTEGER DEFAULT 0)')
        if not db.execute('SELECT 1 FROM users').fetchone():
            db.execute('INSERT INTO users(name,hash,must_change,role) VALUES (?,?,1,?)', ('Admin', password_hash('raspberry'),'admin'))


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 390000)
    return 'pbkdf2$390000$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(digest).decode()


def verify(password, encoded):
    try:
        _, n, salt, digest = encoded.split('$')
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), int(n))
        return hmac.compare_digest(check, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def require_user(request: Request):
    token = request.cookies.get(COOKIE, '')
    if not token:
        raise HTTPException(401, 'Login required')
    with connect() as db:
        r = db.execute('SELECT u.name,u.must_change,u.role,u.talk_allowed FROM sessions s JOIN users u ON s.username=u.name WHERE s.token_hash=? AND s.expires>?', (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    if not r:
        raise HTTPException(401, 'Session expired')
    return dict(r)


def require_admin(u=Depends(require_user)):
    if u['role']!='admin': raise HTTPException(403, 'Administrator access required')
    return u


def configured_port():
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='web_port'").fetchone()
    try:
        port = int(row['value']) if row else 8080
        return port if 1024 <= port <= 65535 and port not in (1984, 8555) else 8080
    except (TypeError, ValueError):
        return 8080


def schedule_restart():
    # systemd Restart=always starts the launcher on the requested port.
    timer = threading.Timer(2.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.daemon = True
    timer.start()


class AlertDestination(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=8, max_length=2048)
    enabled: bool = True
    motion: bool = True
    storage: bool = False
    cooldown_seconds: int = Field(default=120, ge=30, le=3600)


def public_alert_row(row):
    return {
        'id': row['id'],
        'label': row['label'],
        'provider': notifications.redact(row['url']),
        'enabled': bool(row['enabled']),
        'motion': 'motion' in row['events'].split(','),
        'storage': 'storage' in row['events'].split(','),
        'cooldown_seconds': row['cooldown_seconds'],
        'configured': True,
    }


def alert_destinations(event_type, camera_name=None):
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM alert_destinations WHERE enabled=1')]
    notifications.dispatch(rows, event_type, camera_name)


class NetworkPort(BaseModel):
    port: int = Field(ge=1024, le=65535)
    current_password: str



class NewViewer(BaseModel):
    username: str = Field(min_length=3,max_length=48,pattern=r'^[A-Za-z][A-Za-z0-9_.-]*$')
    password: str = Field(min_length=12,max_length=128)
    talk_allowed: bool = False


class ViewerTalkPermission(BaseModel):
    talk_allowed: bool = False


class ViewerPassword(BaseModel):
    password: str = Field(min_length=12,max_length=128)


class Login(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    current: str
    new: str = Field(min_length=12, max_length=128)


class Camera(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=255)
    main_path: str = Field(default='/Streaming/Channels/101', max_length=512)
    sub_path: str = Field(default='/Streaming/Channels/102', max_length=512)
    username: str = Field(default='', max_length=128)
    password: str = Field(default='', max_length=256)
    recording: bool = False
    record_format: str = Field(default="mp4", pattern="^(mp4|mkv)$")
    snapshot_count: int = Field(default=1, ge=1, le=7)
    recording_mode: str = Field(default='continuous', pattern='^(continuous|motion|off)$')
    motion_detection: bool = False
    talk_enabled: bool = False
    motion_sensitivity: int = Field(default=5, ge=1, le=10)
    enabled: bool = True


class CameraConnectionTest(BaseModel):
    camera: Camera
    camera_id: int | None = Field(default=None, ge=1)


def validate_cam(camera):
    # Restrict camera destination to host/IP with optional port. No URL schemes or shell interpolation.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,254}', camera.host) or '/' in camera.host:
        raise HTTPException(422, 'Host must be an IP address or hostname, with optional port')
    for path in (camera.main_path, camera.sub_path):
        if not path.startswith('/') or '\\' in path or '\n' in path or '\r' in path or '?' in path:
            raise HTTPException(422, 'Stream path must start with / and contain no query string')


def camera_row(row):
    d = dict(row)
    d.pop('password', None)
    d['has_password'] = bool(row['password'])
    d['recording'] = bool(row['recording'])
    d['record_format'] = row['record_format']
    d['snapshot_count'] = row['snapshot_count']
    d['recording_mode'] = row['recording_mode']
    d['motion_detection'] = bool(row['motion_detection'])
    d['talk_enabled'] = bool(row['talk_enabled'])
    d['motion_sensitivity'] = row['motion_sensitivity']
    d['enabled'] = bool(row['enabled'])
    d['active'] = row['id'] in PROCS and PROCS[row['id']].poll() is None
    return d


def rtsp_url(camera, sub=False):
    user = quote(camera['username'] or '', safe='')
    pw = quote(camera['password'] or '', safe='')
    auth = f'{user}:{pw}@' if user else ''
    return f'rtsp://{auth}{camera["host"]}{camera["sub_path"] if sub else camera["main_path"]}'


def is_recording_mounted():
    # Fail closed on missing/unmounted storage to protect the Pi's system disk.
    return RECORDINGS.exists() and (os.path.ismount(RECORDINGS) or os.environ.get('PINVR_ALLOW_LOCAL_RECORDINGS') == '1')


def run_recorder(camera):
    if not is_recording_mounted():
        return False
    target = RECORDINGS / str(camera['id'])
    target.mkdir(parents=True, exist_ok=True)
    fmt = camera.get('record_format', 'mp4')
    if fmt not in ('mp4', 'mkv'): return False
    cmd = ['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-rtsp_transport','tcp',
           '-i',rtsp_url(camera),'-map','0:v:0','-an','-c:v','copy','-f','segment',
           '-segment_time','300','-reset_timestamps','1','-strftime','1']
    if fmt == 'mp4':
        cmd.extend(['-segment_format_options','movflags=frag_keyframe+empty_moov+default_base_moof'])
    cmd.append(str(target/f'%Y%m%d-%H%M%S.{fmt}'))
    try:
        p=subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL, start_new_session=True)
        PROCS[camera['id']]=p
        return True
    except OSError:
        return False


def stop_recorder(cid):
    p=PROCS.pop(cid,None)
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=4)
        except subprocess.TimeoutExpired: p.kill()


def reconcile():
    with LOCK:
        with connect() as db:
            cams = [dict(x) for x in db.execute('SELECT * FROM cameras')]
        active_ids=set()
        for cam in cams:
            if cam['recording'] and cam['enabled'] and cam['recording_mode']=='continuous':
                active_ids.add(cam['id'])
                p=PROCS.get(cam['id'])
                if not p or p.poll() is not None:
                    if p: PROCS.pop(cam['id'],None)
                    run_recorder(cam)
        for cid in list(PROCS):
            if cid not in active_ids: stop_recorder(cid)
    motion.configure(cams, rtsp_url, trigger_motion)


def recording_worker():
    last_storage_warning = 0.0
    while not STOP.wait(15):
        try:
            reconcile()
            with connect() as db:
                any_recording = db.execute('SELECT 1 FROM cameras WHERE recording=1 AND enabled=1 LIMIT 1').fetchone()
            if any_recording and not is_recording_mounted() and time.monotonic()-last_storage_warning > 3600:
                last_storage_warning=time.monotonic()
                alert_destinations('storage')
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app):
    init_db()
    STOP.clear()
    motion.STOP.clear()
    worker=threading.Thread(target=recording_worker, daemon=True)
    worker.start()
    reconcile()
    with connect() as db: relay.restart_relay([dict(x) for x in db.execute('SELECT * FROM cameras')])
    yield
    STOP.set()
    motion.shutdown()
    relay.stop_relay()
    with LOCK:
        for cid in list(PROCS):stop_recorder(cid)


app=FastAPI(title='PiNVR',docs_url=None,redoc_url=None,lifespan=lifespan)


@app.middleware('http')
async def secure_headers(req, call_next):
    if req.method in ('POST','PUT','PATCH','DELETE') and req.url.path!='/api/events/motion':
        origin=req.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            origin_url=urlsplit(origin)
            if origin_url.netloc.lower()!=req.headers.get('host','').lower() or origin_url.scheme not in ('http','https'):
                return Response('Cross-site request blocked',status_code=403)
    resp=await call_next(req)
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Referrer-Policy']='no-referrer'
    resp.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; media-src 'self' blob:"
    resp.headers['Cache-Control']='no-store'
    return resp


@app.get('/')
def index():
    return FileResponse(STATIC/'index.html')


@app.get('/app.css')
def css(): return FileResponse(STATIC/'app.css',media_type='text/css')


@app.get('/app.js')
def js(): return FileResponse(STATIC/'app.js',media_type='text/javascript')


@app.get('/manifest.webmanifest')
def manifest(): return FileResponse(STATIC/'manifest.webmanifest',media_type='application/manifest+json')


@app.get('/icon.svg')
def icon(): return FileResponse(STATIC/'icon.svg',media_type='image/svg+xml')


@app.get('/api/me')
def me(u=Depends(require_user)):
    return {'username':u['name'],'role':u['role'],'talk_allowed':u['role']=='admin' or bool(u['talk_allowed']),'must_change':bool(u['must_change'])}


@app.post('/api/login')
def login(body:Login,response:Response,request:Request):
    with connect() as db:
        user=db.execute('SELECT * FROM users WHERE name=?',(body.username,)).fetchone()
        # Equalize verify work when user absent.
        valid=verify(body.password,user['hash'] if user else password_hash('random_not_valid'))
        if not user or not valid: raise HTTPException(401,'Invalid credentials')
        token=secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['name'],int(time.time()+SESSION_HOURS*3600)))
        db.execute('DELETE FROM sessions WHERE expires<?',(int(time.time()),))
    response.set_cookie(COOKIE,token,max_age=SESSION_HOURS*3600,httponly=True,samesite='strict',secure=(request.url.scheme=='https'),path='/')
    return {'username':user['name'],'role':user['role'],'talk_allowed':user['role']=='admin' or bool(user['talk_allowed']),'must_change':bool(user['must_change'])}


@app.post('/api/password')
def change_password(body:ChangePassword, request:Request,u=Depends(require_user)):
    if body.new.lower()=='raspberry' or body.new==body.current or len(set(body.new))<5:
        raise HTTPException(422,'Choose a stronger, unique password')
    with connect() as db:
        user=db.execute('SELECT hash FROM users WHERE name=?',(u['name'],)).fetchone()
        if not verify(body.current,user['hash']):raise HTTPException(401,'Current password incorrect')
        db.execute('UPDATE users SET hash=?,must_change=0 WHERE name=?',(password_hash(body.new),u['name']))
        token=request.cookies.get(COOKIE,'')
        db.execute('DELETE FROM sessions WHERE username=? AND token_hash<>?', (u['name'],hashlib.sha256(token.encode()).hexdigest()))
    return {'ok':True}


@app.post('/api/logout')
def logout(request:Request,response:Response,u=Depends(require_user)):
    with connect() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@app.get('/api/cameras')
def cameras(u=Depends(require_user)):
    with connect() as db:
        rows=[camera_row(x) for x in db.execute('SELECT * FROM cameras ORDER BY id')]
    if u['role']=='viewer':
        return [{'id':c['id'],'name':c['name'],'enabled':c['enabled'],'talk_enabled':bool(c['talk_enabled'] and u['talk_allowed'])} for c in rows if c['enabled']]
    return rows


@app.post('/api/cameras/test-connection')
def test_rtsp_connection(body:CameraConnectionTest, u=Depends(require_admin)):
    """Check camera credentials and stream metadata without saving any settings."""
    cam = body.camera
    validate_cam(cam)
    details = cam.model_dump()
    if body.camera_id is not None and not cam.password:
        # The edit form intentionally leaves an existing password blank.
        # Test with the existing secret only for that same camera ID.
        with connect() as db:
            prior = db.execute('SELECT password FROM cameras WHERE id=?', (body.camera_id,)).fetchone()
        if not prior:
            raise HTTPException(404, 'Saved camera no longer exists')
        details['password'] = prior['password'] or ''
    return rtsp_test.inspect_camera(details, rtsp_url)


@app.post('/api/cameras',status_code=201)
def add_camera(cam:Camera,u=Depends(require_admin)):
    validate_cam(cam)
    with connect() as db:
        cur=db.execute('INSERT INTO cameras(name,host,main_path,sub_path,username,password,recording,enabled,created_at,record_format,snapshot_count,recording_mode,motion_detection,motion_sensitivity,talk_enabled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(cam.name,cam.host,cam.main_path,cam.sub_path,cam.username,cam.password,int(cam.recording),int(cam.enabled),datetime.now(timezone.utc).isoformat(),cam.record_format,cam.snapshot_count,cam.recording_mode,int(cam.motion_detection),cam.motion_sensitivity,int(cam.talk_enabled)))
        cid=cur.lastrowid
    reconcile()
    refresh_relay()
    return {'id':cid}


@app.put('/api/cameras/{cid}')
def edit_camera(cid:int,cam:Camera,u=Depends(require_admin)):
    validate_cam(cam)
    with LOCK:
        with connect() as db:
            old=db.execute('SELECT password FROM cameras WHERE id=?',(cid,)).fetchone()
            if not old: raise HTTPException(404,'Camera not found')
            stop_recorder(cid)
            db.execute('UPDATE cameras SET name=?,host=?,main_path=?,sub_path=?,username=?,password=?,recording=?,enabled=?,record_format=?,snapshot_count=?,recording_mode=?,motion_detection=?,motion_sensitivity=?,talk_enabled=? WHERE id=?',(cam.name,cam.host,cam.main_path,cam.sub_path,cam.username,cam.password or old['password'],int(cam.recording),int(cam.enabled),cam.record_format,cam.snapshot_count,cam.recording_mode,int(cam.motion_detection),cam.motion_sensitivity,int(cam.talk_enabled),cid))
        reconcile()
        refresh_relay()
    return {'id':cid}


@app.delete('/api/cameras/{cid}')
def delete_camera(cid:int,u=Depends(require_admin)):
    with LOCK:
        stop_recorder(cid)
        with connect() as db:
            if not db.execute('DELETE FROM cameras WHERE id=?',(cid,)).rowcount:raise HTTPException(404,'Camera not found')
    with connect() as db:
        db.execute('DELETE FROM motion_tokens WHERE camera_id=?',(cid,))
    reconcile()
    refresh_relay()
    return {'ok':True,'note':'Recorded clips are preserved on disk'}


@app.get('/api/onvif/discover')
def discover_onvif(u=Depends(require_admin)):
    """Discover ONVIF-compatible devices on the local network, without credential guesses."""
    msgid=str(uuid.uuid4())
    probe=(f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
           f'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
           f'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
           f'<s:Header><a:MessageID>uuid:{msgid}</a:MessageID>'
           '<a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>'
           '<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>'
           '</s:Header><s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>'
           '</d:Probe></s:Body></s:Envelope>')
    # Different vendor implementations may require a Types namespace;
    # an empty Probe is accepted more broadly by compatible implementations.
    probe=probe.replace('<d:Types>dn:NetworkVideoTransmitter</d:Types>','')
    devices={}
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP) as sock:
            sock.settimeout(0.3)
            sock.bind(('',0))
            sock.sendto(probe.encode(),('239.255.255.250',3702))
            deadline=time.monotonic()+2.2
            while time.monotonic()<deadline and len(devices)<64:
                try:data,addr=sock.recvfrom(32768)
                except socket.timeout:continue
                if len(data)>32768:continue
                try:
                    doc=ET.fromstring(data)
                    addrs=[]
                    for node in doc.iter():
                        if node.tag.endswith('}XAddrs') and node.text:
                            addrs.extend(node.text.split())
                    devices[addr[0]]={'ip':addr[0],'service_urls':[url for url in addrs if url.startswith(('http://','https://'))][:5]}
                except ET.ParseError:continue
    except OSError as e:
        raise HTTPException(503,'ONVIF discovery could not use UDP multicast') from e
    return {'devices':list(devices.values()),'note':'Discovery does not authenticate cameras or automatically configure RTSP paths.'}


@app.get('/api/users')
def list_users(u=Depends(require_admin)):
    with connect() as db:
        return [{'username':x['name'],'role':x['role'],'must_change':bool(x['must_change']),'talk_allowed':bool(x['talk_allowed'])} for x in db.execute('SELECT name,role,must_change,talk_allowed FROM users ORDER BY name')]


@app.post('/api/users',status_code=201)
def add_viewer(body:NewViewer,u=Depends(require_admin)):
    if body.username.lower()=='admin' or body.password.lower()=='raspberry':
        raise HTTPException(422,'Choose a different username and a stronger password')
    try:
        with connect() as db:
            db.execute('INSERT INTO users(name,hash,must_change,role,talk_allowed) VALUES (?,?,1,?,?)',
                       (body.username,password_hash(body.password),'viewer',int(body.talk_allowed)))
    except sqlite3.IntegrityError:
        raise HTTPException(409,'Username already exists') from None
    return {'username':body.username,'role':'viewer','must_change':True,'talk_allowed':body.talk_allowed}


@app.put('/api/users/{username}/talk')
def update_viewer_talk(username:str, body:ViewerTalkPermission, u=Depends(require_admin)):
    # Account permission is enforced during *every* microphone signaling request.
    with connect() as db:
        row=db.execute('SELECT name,role FROM users WHERE name=?',(username,)).fetchone()
        if not row or row['role']!='viewer': raise HTTPException(404,'Viewer not found')
        db.execute('UPDATE users SET talk_allowed=? WHERE name=?',(int(body.talk_allowed),row['name']))
    return {'username':row['name'],'talk_allowed':body.talk_allowed}


@app.put('/api/users/{username}/password')
def reset_viewer_password(username:str,body:ViewerPassword,u=Depends(require_admin)):
    if body.password.lower()=='raspberry':
        raise HTTPException(422,'Choose a stronger password')
    with connect() as db:
        row=db.execute('SELECT name,role FROM users WHERE name=?',(username,)).fetchone()
        if not row or row['role']!='viewer': raise HTTPException(404,'Viewer not found')
        db.execute('UPDATE users SET hash=?,must_change=1 WHERE name=?', (password_hash(body.password),row['name']))
        db.execute('DELETE FROM sessions WHERE username=?',(row['name'],))
    return {'ok':True,'must_change':True}


@app.delete('/api/users/{username}')
def delete_viewer(username:str,u=Depends(require_admin)):
    with connect() as db:
        row=db.execute('SELECT name,role FROM users WHERE name=?',(username,)).fetchone()
        if not row or row['role']!='viewer': raise HTTPException(404,'Viewer not found')
        db.execute('DELETE FROM sessions WHERE username=?',(row['name'],))
        db.execute('DELETE FROM users WHERE name=?',(row['name'],))
    return {'ok':True}


@app.get('/api/alerts/destinations')
def list_alert_destinations(u=Depends(require_admin)):
    with connect() as db:
        return [public_alert_row(r) for r in db.execute('SELECT * FROM alert_destinations ORDER BY id')]


@app.post('/api/alerts/destinations',status_code=201)
def add_alert_destination(body:AlertDestination,u=Depends(require_admin)):
    try:
        notifications.validate_url(body.url)
    except ValueError as e:
        raise HTTPException(422,str(e))
    events = ','.join(x for x,allowed in (('motion',body.motion),('storage',body.storage)) if allowed)
    if not events:
        raise HTTPException(422,'Choose at least one alert type')
    with connect() as db:
        cur=db.execute('INSERT INTO alert_destinations(label,url,enabled,events,cooldown_seconds) VALUES (?,?,?,?,?)',
                       (body.label,body.url,int(body.enabled),events,body.cooldown_seconds))
        row=db.execute('SELECT * FROM alert_destinations WHERE id=?',(cur.lastrowid,)).fetchone()
    return public_alert_row(row)


@app.put('/api/alerts/destinations/{dest_id}')
def update_alert_destination(dest_id:int, body:AlertDestination,u=Depends(require_admin)):
    events = ','.join(x for x,allowed in (('motion',body.motion),('storage',body.storage)) if allowed)
    if not events:
        raise HTTPException(422,'Choose at least one alert type')
    with connect() as db:
        prior=db.execute('SELECT * FROM alert_destinations WHERE id=?',(dest_id,)).fetchone()
        if not prior:
            raise HTTPException(404,'Alert destination not found')
        url=body.url if body.url!='KEEP_EXISTING' else prior['url']
        try:
            notifications.validate_url(url)
        except ValueError as e:
            raise HTTPException(422,str(e))
        db.execute('UPDATE alert_destinations SET label=?,url=?,enabled=?,events=?,cooldown_seconds=? WHERE id=?',
                   (body.label,url,int(body.enabled),events,body.cooldown_seconds,dest_id))
        result=db.execute('SELECT * FROM alert_destinations WHERE id=?',(dest_id,)).fetchone()
    return public_alert_row(result)


@app.delete('/api/alerts/destinations/{dest_id}')
def delete_alert_destination(dest_id:int,u=Depends(require_admin)):
    with connect() as db:
        if not db.execute('DELETE FROM alert_destinations WHERE id=?',(dest_id,)).rowcount:
            raise HTTPException(404,'Alert destination not found')
    return {'ok':True}


@app.post('/api/alerts/destinations/{dest_id}/test')
def test_alert_destination(dest_id:int,u=Depends(require_admin)):
    with connect() as db:
        row=db.execute('SELECT url FROM alert_destinations WHERE id=?',(dest_id,)).fetchone()
    if not row:raise HTTPException(404,'Alert destination not found')
    result=notifications.send_test(row['url'])
    if not result['ok']:raise HTTPException(502,result['message'])
    return result


@app.get('/api/network')
def network_settings(u=Depends(require_admin)):
    return {'port':configured_port(), 'managed':os.environ.get('PINVR_MANAGED')=='1',
            'vpn_recommended':True, 'https_enabled':False}


@app.post('/api/network')
def update_network_settings(body:NetworkPort, u=Depends(require_admin)):
    if body.port in (1984, 8555):
        raise HTTPException(422, 'Port reserved for internal streaming services')
    if os.environ.get('PINVR_MANAGED')!='1':
        raise HTTPException(409, 'Port changes require the native PiNVR systemd installation')
    with connect() as db:
        current=db.execute('SELECT hash FROM users WHERE name=?',(u['name'],)).fetchone()
        if not current or not verify(body.current_password,current['hash']):
            raise HTTPException(401,'Incorrect administrator password')
        old=configured_port()
        if body.port==old:
            return {'port':old,'previous_port':old,'restarting':False}
        # Preflight the requested listener to avoid predictable port conflicts.
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                probe.bind(('0.0.0.0',body.port))
        except OSError:
            raise HTTPException(409,'Port is unavailable on this Raspberry Pi') from None
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('previous_port',?)",(str(old),))
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('web_port',?)",(str(body.port),))
    schedule_restart()
    return {'port':body.port,'previous_port':old,'restarting':True}


class StorageSelection(BaseModel):
    uuid: str = Field(min_length=3, max_length=128)
    current_password: str


@app.get('/api/storage/drives')
def storage_drives(u=Depends(require_admin)):
    try:
        return {'drives': storage.discover(), 'current': storage.configured()}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.post('/api/storage/configure')
def configure_storage(body: StorageSelection, request: Request, u=Depends(require_admin)):
    # Require an authenticated Admin, re-entry of their password, and same-origin browser requests.
    origin = request.headers.get('origin')
    if not origin or origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Use the PiNVR web interface to configure storage')
    with connect() as db:
        account = db.execute('SELECT hash FROM users WHERE name=?', (u['name'],)).fetchone()
    if not account or not verify(body.current_password, account['hash']):
        raise HTTPException(403, 'Administrator password incorrect')
    if not storage.UUID_PATTERN.fullmatch(body.uuid):
        raise HTTPException(422, 'Invalid drive identifier')
    # The helper must be root-owned, installed by install.sh, with one sudoers command.
    try:
        result = subprocess.run(['sudo', '-n', '/usr/local/libexec/pinvr-storage-helper', body.uuid],
                                capture_output=True, text=True, timeout=35)
        payload = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise HTTPException(503, 'Storage setup helper unavailable')
    if result.returncode or not payload.get('ok'):
        raise HTTPException(409, payload.get('error', 'Could not mount USB drive'))
    return payload


@app.get('/api/status')
def status(u=Depends(require_admin)):
    mount=is_recording_mounted()
    usage=shutil.disk_usage(RECORDINGS) if mount else None
    cpu_temp=None
    try: cpu_temp=round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,1)
    except (OSError,ValueError):pass
    return {'storage_mounted':mount,'storage_total':usage.total if usage else 0,'storage_free':usage.free if usage else 0,'recorders':sum(p.poll() is None for p in PROCS.values()),'live_relay':relay.is_running(),'temperature_c':cpu_temp,'memory_mb':round(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/1048576) if hasattr(os,'sysconf') else None}


@app.get('/api/clips')
def clips(camera_id:int|None=None,u=Depends(require_user)):
    # Playback is available to viewers, but only for currently enabled cameras.
    with connect() as db:
        allowed={int(row['id']) for row in db.execute('SELECT id FROM cameras WHERE enabled=1')} if u['role']=='viewer' else None
    if camera_id is not None and allowed is not None and camera_id not in allowed:
        raise HTTPException(404,'Camera unavailable')
    if not is_recording_mounted():return []
    rows=[]
    dirs=[RECORDINGS/str(camera_id)] if camera_id is not None else [x for x in RECORDINGS.iterdir() if x.is_dir() and x.name.isdigit()]
    if allowed is not None:dirs=[d for d in dirs if int(d.name) in allowed]
    for d in dirs:
        if not d.is_dir():continue
        for f in [*d.glob('*.mp4'), *d.glob('*.mkv')]:
            if not re.fullmatch(r'\d{8}-\d{6}\.(mp4|mkv)',f.name):continue
            try: stat=f.stat()
            except OSError:continue
            if stat.st_size<1024:continue
            rows.append({'camera_id':int(d.name),'name':f.name,'size':stat.st_size,'modified':int(stat.st_mtime),'format':f.suffix[1:]})
    return sorted(rows,key=lambda x:x['modified'],reverse=True)[:500]


@app.get('/api/clips/{cid}/{filename}')
def download_clip(cid:int,filename:str,u=Depends(require_user)):
    if u['role']=='viewer':
        with connect() as db:
            allowed=db.execute('SELECT 1 FROM cameras WHERE id=? AND enabled=1',(cid,)).fetchone()
        if not allowed:raise HTTPException(404,'Camera unavailable')
    if not re.fullmatch(r'\d{8}-\d{6}\.(mp4|mkv)',filename):raise HTTPException(400,'Invalid clip name')
    if not is_recording_mounted():raise HTTPException(503,'Recording drive unavailable')
    dest=RECORDINGS/str(cid)/filename
    if not dest.is_file():raise HTTPException(404,'Clip not found')
    return FileResponse(dest,media_type='video/mp4' if dest.suffix=='.mp4' else 'video/x-matroska',filename=filename,content_disposition_type='inline' if dest.suffix=='.mp4' else 'attachment')


# Snapshots use a brief camera substream connection, saving to the external USB drive.
# Event-triggered snapshot capture will be wired into ONVIF detection separately.
@app.post('/api/cameras/{cid}/snapshots')
def capture_snapshots(cid:int, u=Depends(require_admin)):
    if not is_recording_mounted(): raise HTTPException(503,'Recording drive unavailable')
    with connect() as db:
        row=db.execute('SELECT * FROM cameras WHERE id=? AND enabled=1',(cid,)).fetchone()
    if row is None: raise HTTPException(404,'Camera unavailable')
    camera=dict(row)
    n=max(1,min(7,int(camera['snapshot_count'])))
    target=RECORDINGS/str(cid)/'snapshots'
    target.mkdir(parents=True,exist_ok=True)
    # Use a unique prefix to avoid overwriting shots from concurrent requests.
    prefix=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
    output=str(target/(prefix+'-%02d.jpg'))
    cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-rtsp_transport','tcp',
         '-i',rtsp_url(camera,sub=True),'-vf','fps=1','-frames:v',str(n),'-q:v','4',output]
    try:
        finished=subprocess.run(cmd,stdin=subprocess.DEVNULL,capture_output=True,timeout=30,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise HTTPException(502,'Camera snapshot capture timed out or FFmpeg is unavailable')
    files=sorted(target.glob(prefix+'-*.jpg'))
    if not files or finished.returncode!=0:
        for f in files: f.unlink(missing_ok=True)
        raise HTTPException(502,'Camera did not return a usable snapshot')
    return {'camera_id':cid,'count':len(files),'files':[f.name for f in files]}


@app.get('/api/snapshots')
def list_snapshots(camera_id:int|None=None,u=Depends(require_user)):
    if not is_recording_mounted(): return []
    with connect() as db:
        allowed={r['id'] for r in db.execute('SELECT id FROM cameras WHERE enabled=1')} if u['role']=='viewer' else None
    dirs=[RECORDINGS/str(camera_id)] if camera_id is not None else [p for p in RECORDINGS.iterdir() if p.is_dir() and p.name.isdigit()]
    result=[]
    for d in dirs:
        if not d.name.isdigit() or allowed is not None and int(d.name) not in allowed: continue
        snapdir=d/'snapshots'
        if not snapdir.is_dir():continue
        for f in snapdir.glob('*.jpg'):
            if not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{6}-\d{2}\.jpg',f.name): continue
            try: st=f.stat()
            except OSError:continue
            result.append({'camera_id':int(d.name),'name':f.name,'size':st.st_size,'modified':int(st.st_mtime)})
    return sorted(result,key=lambda x:x['modified'],reverse=True)[:500]


@app.get('/api/snapshots/{cid}/{filename}')
def download_snapshot(cid:int,filename:str,u=Depends(require_user)):
    if not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{6}-\d{2}\.jpg',filename):
        raise HTTPException(400,'Invalid snapshot filename')
    if not is_recording_mounted():raise HTTPException(503,'Recording drive unavailable')
    if u['role']=='viewer':
        with connect() as db:
            if not db.execute('SELECT 1 FROM cameras WHERE id=? AND enabled=1',(cid,)).fetchone():
                raise HTTPException(404,'Camera unavailable')
    f=RECORDINGS/str(cid)/'snapshots'/filename
    if not f.is_file():raise HTTPException(404,'Snapshot not found')
    return FileResponse(f,media_type='image/jpeg',filename=filename,content_disposition_type='inline')



# Camera-native motion integrations can POST events to this endpoint using a
# high-entropy camera-scoped bearer token. This is NOT an ONVIF listener.
# Camera event bridges and third-party integrations must be configured separately.
MOTION_GUARD = {}
MOTION_GUARD_LOCK = threading.RLock()


@app.post('/api/cameras/{cid}/motion-token')
def create_motion_token(cid:int,u=Depends(require_admin)):
    token=secrets.token_urlsafe(32)
    with connect() as db:
        if not db.execute('SELECT id FROM cameras WHERE id=?',(cid,)).fetchone():
            raise HTTPException(404,'Camera not found')
        db.execute('INSERT OR REPLACE INTO motion_tokens(camera_id,token_hash) VALUES (?,?)',
                   (cid,hashlib.sha256(token.encode()).hexdigest()))
    return {'camera_id':cid,'token':token,'note':'Save this token securely: it is shown only once. Generating another revokes the old one.'}


def trigger_motion(cid:int):
    if not is_recording_mounted():
        raise HTTPException(503,'Recording drive is not mounted')
    with connect() as db:
        row=db.execute('SELECT * FROM cameras WHERE id=? AND enabled=1',(cid,)).fetchone()
    if not row: raise HTTPException(404,'Camera unavailable')
    camera=dict(row)
    with MOTION_GUARD_LOCK:
        now=time.monotonic()
        if now-MOTION_GUARD.get(cid,-1000)<20:
            raise HTTPException(429,'Event cooldown active; retry in 20 seconds')
        MOTION_GUARD[cid]=now
    alert_destinations('motion', camera.get('name'))
    threading.Thread(target=_capture_motion_event,args=(camera,),daemon=True).start()
    return {'ok':True,'camera_id':cid,'status':'capturing','snapshot_count':camera['snapshot_count']}


def _capture_motion_event(camera):
    cid=camera['id']
    prefix=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target=RECORDINGS/str(cid)
    try:
        if not is_recording_mounted():return
        target.mkdir(parents=True,exist_ok=True)
        clip_name=None
        clip_thread=None
        if camera['recording'] and camera['recording_mode']=='motion':
            fmt=camera['record_format']
            clip_name=f'{prefix}.{fmt}'
            cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-rtsp_transport','tcp',
                 '-i',rtsp_url(camera),'-t','30','-map','0:v:0','-an','-c:v','copy']
            if fmt=='mp4': cmd+=['-movflags','frag_keyframe+empty_moov+default_base_moof']
            cmd+=['-y',str(target/clip_name)]
            def clip_job():
                try:subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=65,check=False)
                except (subprocess.TimeoutExpired,OSError):pass
            clip_thread=threading.Thread(target=clip_job,daemon=True)
            clip_thread.start()
        # Capture JPGs immediately, in parallel with the video recording.
        snapdir=target/'snapshots';snapdir.mkdir(exist_ok=True)
        burst_prefix=prefix+'-'+secrets.token_hex(3)
        out=str(snapdir/(burst_prefix+'-%02d.jpg'))
        cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-rtsp_transport','tcp',
             '-i',rtsp_url(camera,sub=True),'-vf','fps=1','-frames:v',str(camera['snapshot_count']),'-q:v','4',out]
        try:subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=False)
        except (subprocess.TimeoutExpired,OSError):pass
        if clip_thread: clip_thread.join(timeout=70)
        count=len(list(snapdir.glob(burst_prefix+'-*.jpg')))
        if not is_recording_mounted():return
        with connect() as db:
            db.execute('INSERT INTO motion_events(camera_id,created_at,clip_name,snapshot_count) VALUES (?,?,?,?)',
                       (cid,int(time.time()),clip_name,count))
    except (OSError,sqlite3.Error):
        pass


@app.post('/api/cameras/{cid}/test-motion')
def manual_motion_test(cid:int,u=Depends(require_admin)):
    return trigger_motion(cid)


@app.post('/api/events/motion/{cid}')
def receive_motion_event(cid:int,request:Request):
    token=request.headers.get('X-PiNVR-Event-Key','')
    if not token or len(token)>200:raise HTTPException(401,'Invalid event credential')
    with connect() as db:
        row=db.execute('SELECT token_hash FROM motion_tokens WHERE camera_id=?',(cid,)).fetchone()
    if not row or not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()):
        raise HTTPException(401,'Invalid event credential')
    return trigger_motion(cid)


@app.get('/api/events/motion')
def motion_event_list(camera_id:int|None=None,u=Depends(require_user)):
    with connect() as db:
        permitted={r['id'] for r in db.execute('SELECT id FROM cameras WHERE enabled=1')} if u['role']=='viewer' else None
        rows=db.execute('SELECT * FROM motion_events ORDER BY id DESC LIMIT 200').fetchall()
    return [dict(r) for r in rows if (camera_id is None or r['camera_id']==camera_id)
            and (permitted is None or r['camera_id'] in permitted)]

def refresh_relay():
    with connect() as db:
        relay.restart_relay([dict(x) for x in db.execute('SELECT * FROM cameras')])


@app.post('/api/talk/{cid}/offer')
async def talk_offer(cid:int, request:Request, u=Depends(require_user)):
    # This is an explicit opt-in backchannel. Camera and viewer permissions are
    # checked server-side. Browser must be on HTTPS to access its microphone.
    if u['role']!='admin' and not u['talk_allowed']:
        raise HTTPException(403,'Viewer talk permission disabled')
    with connect() as db:
        cam=db.execute('SELECT id,enabled,talk_enabled FROM cameras WHERE id=?',(cid,)).fetchone()
    if not cam or not cam['enabled'] or not cam['talk_enabled']:
        raise HTTPException(403,'Camera talk not enabled')
    if not relay.is_running():
        raise HTTPException(503,'go2rtc relay unavailable')
    offer=await request.body()
    if not (0 < len(offer) < 65536 and b'm=audio' in offer and b'a=sendonly' in offer):
        raise HTTPException(422,'An audio sending WebRTC offer is required')
    try:
        async with httpx.AsyncClient(timeout=15.0,trust_env=False) as client:
            resp=await client.post('http://127.0.0.1:1984/api/webrtc',
                params={'src':f'cam{cid}_main'}, content=offer,
                headers={'Content-Type':'application/sdp'})
            resp.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502,'Camera audio backchannel negotiation failed') from None
    return Response(resp.content,media_type='application/sdp')


@app.post('/api/live/{cid}/offer')
async def webrtc_offer(cid:int, request:Request, profile:str='sub', u=Depends(require_user)):
    if profile not in ('sub','main'):
        raise HTTPException(422,'Invalid stream profile')
    if not relay.is_running():
        raise HTTPException(503,'go2rtc relay unavailable')
    with connect() as db:
        cam=db.execute('SELECT id,enabled FROM cameras WHERE id=?',(cid,)).fetchone()
    if not cam or not cam['enabled']:
        raise HTTPException(404,'Camera unavailable')
    offer=await request.body()
    if not (0<len(offer)<65536):
        raise HTTPException(413,'Invalid SDP size')
    try:
        async with httpx.AsyncClient(timeout=12.0,trust_env=False) as client:
            resp=await client.post('http://127.0.0.1:1984/api/webrtc',params={'src':f'cam{cid}_{profile}'},content=offer,headers={'Content-Type':'application/sdp'})
            resp.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502,'Live relay could not negotiate stream') from None
    return Response(resp.content,media_type='application/sdp')
