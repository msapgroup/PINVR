"""Regressions for PiNVR 2026 camera configuration, PTZ, storage and health."""
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
from pinvr import main, recording_health, onvif_ptz


def test_camera_motion_and_ptz_roundtrip_and_viewer_denied(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "DB", tmp_path / "pinvr.db")
    monkeypatch.setattr(main.motion, "configure", lambda *args: None)
    called=[]
    monkeypatch.setattr(onvif_ptz, "move", lambda camera, direction: called.append((camera["id"],direction)) or {"ok":True})
    with TestClient(main.app) as admin:
        assert admin.post("/api/login",json={"username":"Admin","password":"raspberry"}).status_code==200
        settings={"name":"Front door", "host":"192.168.10.20", "username":"operator", "password":"secret",
                  "motion_detection":True,"motion_sensitivity":9,"ptz_enabled":True,"onvif_port":8899}
        r=admin.post("/api/cameras",json=settings)
        assert r.status_code==201,r.text
        cid=r.json()["id"]
        saved=admin.get("/api/cameras").json()[0]
        assert saved["motion_detection"] is True and saved["motion_sensitivity"]==9
        assert saved["ptz_enabled"] is True and saved["onvif_port"]==8899
        assert admin.post(f"/api/cameras/{cid}/ptz",json={"direction":"right"}).status_code==200
        assert called==[(cid,"right")]
        edit={**settings,"motion_detection":False,"motion_sensitivity":2,"ptz_enabled":False}
        assert admin.put(f"/api/cameras/{cid}",json=edit).status_code==200
        saved=admin.get("/api/cameras").json()[0]
        assert saved["motion_detection"] is False and saved["motion_sensitivity"]==2
        assert admin.post(f"/api/cameras/{cid}/ptz",json={"direction":"left"}).status_code==403
        assert admin.post("/api/users",json={"username":"familyview","password":"UnpredictablePassword2026!"}).status_code==201
        with TestClient(main.app) as viewer:
            assert viewer.post("/api/login",json={"username":"familyview","password":"UnpredictablePassword2026!"}).status_code==200
            assert viewer.post(f"/api/cameras/{cid}/ptz",json={"direction":"stop"}).status_code==403


def test_ptz_endpoint_is_bounded_to_camera_and_standard_soap(monkeypatch):
    cam={"id":7,"host":"192.168.1.66","username":"staff","password":"private","onvif_port":80}
    capability=f'<s:Envelope xmlns:s="{onvif_ptz.NS["soap"]}" xmlns:tt="{onvif_ptz.NS["tt"]}"><s:Body><tt:Capabilities><tt:Media><tt:XAddr>http://192.168.1.66:80/onvif/media_service</tt:XAddr></tt:Media><tt:PTZ><tt:XAddr>http://192.168.1.66:80/onvif/ptz_service</tt:XAddr></tt:PTZ></tt:Capabilities></s:Body></s:Envelope>'
    profiles=f'<s:Envelope xmlns:s="{onvif_ptz.NS["soap"]}" xmlns:trt="{onvif_ptz.NS["trt"]}" xmlns:tt="{onvif_ptz.NS["tt"]}"><s:Body><trt:Profiles token="Profile_1"><tt:PTZConfiguration token="1"/></trt:Profiles></s:Body></s:Envelope>'
    sent=[]
    def fake_call(url, config, body):
        sent.append((url,body))
        if "GetCapabilities" in body:return ET.fromstring(capability)
        if "GetProfiles" in body:return ET.fromstring(profiles)
        return ET.fromstring(f'<s:Envelope xmlns:s="{onvif_ptz.NS["soap"]}"><s:Body/></s:Envelope>')
    monkeypatch.setattr(onvif_ptz,"call",fake_call)
    onvif_ptz.CACHE.clear()
    assert onvif_ptz.move(cam,"up")["ok"]
    assert "ContinuousMove" in sent[-1][1] and 'y="0.5"' in sent[-1][1]
    assert "PT0.5S" in sent[-1][1]
    assert onvif_ptz.move(cam,"stop")["ok"]
    assert "tptz:Stop" in sent[-1][1]
    try:
        onvif_ptz._validate_endpoint("http://127.0.0.1:1984/api",cam)
        assert False,"SSRF service host should be rejected"
    except onvif_ptz.CameraPTZError:pass
    onvif_ptz.CACHE.clear()


def test_recent_recording_bytes_vs_stalled_and_earlier_files(tmp_path):
    directory=tmp_path/"8"
    directory.mkdir()
    now=time.time()
    old=directory/"20261121-010000.mp4"
    old.write_bytes(b"A"*4000)
    os.utime(old,(now-900,now-900))
    assert not recording_health.recent_output(tmp_path,8,now-1200,now=now)
    new=directory/"20261121-020000.mp4"
    new.write_bytes(b"B"*2048)
    assert recording_health.recent_output(tmp_path,8,now-120,now=now)
    assert not recording_health.recent_output(tmp_path,8,now+10,now=now)
    new.unlink()
    new.symlink_to(old)
    assert not recording_health.recent_output(tmp_path,8,now-120,now=now)


def test_orphan_event_cleanup_preserves_available_jpgs(monkeypatch,tmp_path):
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"DB",tmp_path/"pinvr.db")
    monkeypatch.setattr(main,"RECORDINGS",tmp_path/"recordings")
    monkeypatch.setattr(main,"is_recording_mounted",lambda:True)
    with TestClient(main.app) as client:
        with main.connect() as db:
            stamp=int(time.time())-2400
            db.execute("INSERT INTO motion_events(camera_id,created_at,clip_name,snapshot_count) VALUES (?,?,?,?)",(3,stamp,None,0))
            db.execute("INSERT INTO motion_events(camera_id,created_at,clip_name,snapshot_count) VALUES (?,?,?,?)",(4,stamp,None,1))
        snapdir=main.RECORDINGS/"4"/"snapshots"
        snapdir.mkdir(parents=True)
        timestamp=datetime.fromtimestamp(stamp-45,timezone.utc).strftime("%Y%m%d-%H%M%S")
        (snapdir/f"{timestamp}-abcdef-01.jpg").write_bytes(b"jpeg")
        assert main.cleanup_orphaned_motion_events()==1
        with main.connect() as db:
            remaining=[r["camera_id"] for r in db.execute("SELECT camera_id FROM motion_events")]
        assert remaining==[4]


def test_login_limiter_prunes_empty_caches_without_crashing(monkeypatch):
    main.LOGIN_ATTEMPTS.clear()
    main.LOGIN_CLIENT_ATTEMPTS.clear()
    for n in range(2001):
        main.LOGIN_ATTEMPTS[("ip",str(n))]=[]
    main.check_login_limit("ip","admin")
    assert len(main.LOGIN_ATTEMPTS)<=2000
    assert main.LOGIN_ATTEMPTS[("ip","admin")]==[]
    main.LOGIN_ATTEMPTS.clear()
    main.LOGIN_CLIENT_ATTEMPTS.clear()
