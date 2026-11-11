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
from pinvr import relay
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
        db.execute('CREATE TABLE IF NOT EXISTS cameras (id INTEGER PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, main_path TEXT NOT NULL, sub_path TEXT NOT NULL, username TEXT, password TEXT, recording INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, created_at TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT, expires INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY, camera_id INTEGER, filename TEXT UNIQUE NOT NULL, started_at INTEGER NOT NULL, bytes INTEGER DEFAULT 0)')
        if not db.execute('SELECT 1 FROM users').fetchone():
            db.execute('INSERT INTO users VALUES (?,?,1)', ('Admin', password_hash('raspberry')))


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
        r = db.execute('SELECT u.name,u.must_change FROM sessions s JOIN users u ON s.username=u.name WHERE s.token_hash=? AND s.expires>?', (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    if not r:
        raise HTTPException(401, 'Session expired')
    if r['must_change'] and not (request.url.path in ('/api/me', '/api/password', '/api/logout')):
        raise HTTPException(403, 'Password change required')
    return dict(r)


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
    enabled: bool = True


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
    cmd = ['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-rtsp_transport','tcp',
           '-i',rtsp_url(camera),'-map','0:v:0','-an','-c:v','copy','-f','segment',
           '-segment_time','300','-reset_timestamps','1','-strftime','1',
           str(target/'%Y%m%d-%H%M%S.mp4')]
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
            if cam['recording'] and cam['enabled']:
                active_ids.add(cam['id'])
                p=PROCS.get(cam['id'])
                if not p or p.poll() is not None:
                    if p: PROCS.pop(cam['id'],None)
                    run_recorder(cam)
        for cid in list(PROCS):
            if cid not in active_ids: stop_recorder(cid)


def recording_worker():
    while not STOP.wait(15):
        try: reconcile()
        except Exception: pass


@asynccontextmanager
async def lifespan(app):
    init_db()
    STOP.clear()
    worker=threading.Thread(target=recording_worker, daemon=True)
    worker.start()
    reconcile()
    with connect() as db: relay.restart_relay([dict(x) for x in db.execute('SELECT * FROM cameras')])
    yield
    STOP.set()
    relay.stop_relay()
    with LOCK:
        for cid in list(PROCS):stop_recorder(cid)


app=FastAPI(title='PiNVR',docs_url=None,redoc_url=None,lifespan=lifespan)


@app.middleware('http')
async def secure_headers(req, call_next):
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
    return {'username':u['name'],'must_change':bool(u['must_change'])}


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
    return {'username':user['name'],'must_change':bool(user['must_change'])}


@app.post('/api/password')
def change_password(body:ChangePassword, request:Request,u=Depends(require_user)):
    if body.new.lower()=='raspberry' or body.new==body.current or len(set(body.new))<5:
        raise HTTPException(422,'Choose a stronger, unique password')
    with connect() as db:
        user=db.execute('SELECT hash FROM users WHERE name=?',(u['name'],)).fetchone()
        if not verify(body.current,user['hash']):raise HTTPException(401,'Current password incorrect')
        db.execute('UPDATE users SET hash=?,must_change=0 WHERE name=?',(password_hash(body.new),u['name']))
        token=request.cookies.get(COOKIE,'')
        db.execute('DELETE FROM sessions WHERE token_hash<>?', (hashlib.sha256(token.encode()).hexdigest(),))
    return {'ok':True}


@app.post('/api/logout')
def logout(request:Request,response:Response,u=Depends(require_user)):
    with connect() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@app.get('/api/cameras')
def cameras(u=Depends(require_user)):
    with connect() as db:return [camera_row(x) for x in db.execute('SELECT * FROM cameras ORDER BY id')]


@app.post('/api/cameras',status_code=201)
def add_camera(cam:Camera,u=Depends(require_user)):
    validate_cam(cam)
    with connect() as db:
        cur=db.execute('INSERT INTO cameras(name,host,main_path,sub_path,username,password,recording,enabled,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(cam.name,cam.host,cam.main_path,cam.sub_path,cam.username,cam.password,int(cam.recording),int(cam.enabled),datetime.now(timezone.utc).isoformat()))
        cid=cur.lastrowid
    reconcile()
    refresh_relay()
    return {'id':cid}


@app.put('/api/cameras/{cid}')
def edit_camera(cid:int,cam:Camera,u=Depends(require_user)):
    validate_cam(cam)
    with LOCK:
        with connect() as db:
            old=db.execute('SELECT password FROM cameras WHERE id=?',(cid,)).fetchone()
            if not old: raise HTTPException(404,'Camera not found')
            stop_recorder(cid)
            db.execute('UPDATE cameras SET name=?,host=?,main_path=?,sub_path=?,username=?,password=?,recording=?,enabled=? WHERE id=?',(cam.name,cam.host,cam.main_path,cam.sub_path,cam.username,cam.password or old['password'],int(cam.recording),int(cam.enabled),cid))
        reconcile()
        refresh_relay()
    return {'id':cid}


@app.delete('/api/cameras/{cid}')
def delete_camera(cid:int,u=Depends(require_user)):
    with LOCK:
        stop_recorder(cid)
        with connect() as db:
            if not db.execute('DELETE FROM cameras WHERE id=?',(cid,)).rowcount:raise HTTPException(404,'Camera not found')
    refresh_relay()
    return {'ok':True,'note':'Recorded clips are preserved on disk'}


@app.get('/api/status')
def status(u=Depends(require_user)):
    mount=is_recording_mounted()
    usage=shutil.disk_usage(RECORDINGS) if mount else None
    cpu_temp=None
    try: cpu_temp=round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,1)
    except (OSError,ValueError):pass
    return {'storage_mounted':mount,'storage_total':usage.total if usage else 0,'storage_free':usage.free if usage else 0,'recorders':sum(p.poll() is None for p in PROCS.values()),'live_relay':relay.is_running(),'temperature_c':cpu_temp,'memory_mb':round(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/1048576) if hasattr(os,'sysconf') else None}


@app.get('/api/clips')
def clips(camera_id:int|None=None,u=Depends(require_user)):
    if not is_recording_mounted():return []
    rows=[]
    dirs=[RECORDINGS/str(camera_id)] if camera_id else [x for x in RECORDINGS.iterdir() if x.is_dir() and x.name.isdigit()]
    for d in dirs:
        if not d.is_dir():continue
        for f in d.glob('*.mp4'):
            if not re.fullmatch(r'\d{8}-\d{6}\.mp4',f.name):continue
            try: stat=f.stat()
            except OSError:continue
            if stat.st_size<1024:continue
            rows.append({'camera_id':int(d.name),'name':f.name,'size':stat.st_size,'modified':int(stat.st_mtime)})
    return sorted(rows,key=lambda x:x['modified'],reverse=True)[:500]


@app.get('/api/clips/{cid}/{filename}')
def download_clip(cid:int,filename:str,u=Depends(require_user)):
    if not re.fullmatch(r'\d{8}-\d{6}\.mp4',filename):raise HTTPException(400,'Invalid clip name')
    if not is_recording_mounted():raise HTTPException(503,'Recording drive unavailable')
    dest=RECORDINGS/str(cid)/filename
    if not dest.is_file():raise HTTPException(404,'Clip not found')
    return FileResponse(dest,media_type='video/mp4',filename=filename,content_disposition_type='inline')


def refresh_relay():
    with connect() as db:
        relay.restart_relay([dict(x) for x in db.execute('SELECT * FROM cameras')])


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
