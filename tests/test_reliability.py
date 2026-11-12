"""Recording protection and authentication regression tests."""
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from pinvr import retention, main


def test_retention_expired_files_oldest_first_and_keeps_new(monkeypatch, tmp_path):
    root=tmp_path/"recordings"
    root.mkdir()
    cam=root/"1"
    (cam/"snapshots").mkdir(parents=True)
    old=cam/"20200101-000000.mp4"
    fresh=cam/"20260101-000000.mp4"
    snap=cam/"snapshots"/"20200101-000000-abcdef-01.jpg"
    unknown=cam/"personal-files.txt"
    for path in (old,fresh,snap,unknown):path.write_bytes(b"x"*2048)
    import os
    os.utime(old,(1000,1000));os.utime(snap,(1000,1000))
    os.utime(fresh,(1_000_000,1_000_000));os.utime(unknown,(1000,1000))
    monkeypatch.setattr(retention.os.path, "ismount", lambda _:True)
    now=1_000_060
    result=retention.prune(root,lambda:True,retention_days=1,max_used_percent=90,
                           min_free_gb=5, now=now,
                           usage_func=lambda _:SimpleNamespace(free=30*1024**3,used=1,total=31*1024**3))
    assert result['ok'] and result['removed']==2
    assert not old.exists() and not snap.exists()
    assert fresh.exists() and unknown.exists()


def test_retention_refuses_unmounted_drive_and_skips_symlinks(monkeypatch,tmp_path):
    root=tmp_path/"recordings"
    cam=root/"2"
    cam.mkdir(parents=True)
    outside=tmp_path/"outside.mp4"
    outside.write_bytes(b"x"*1024)
    link=cam/"20200101-000000.mp4"
    link.symlink_to(outside)
    monkeypatch.setattr(retention.os.path,"ismount",lambda _:False)
    r=retention.prune(root,lambda:False)
    assert not r['ok'] and outside.exists()
    monkeypatch.setattr(retention.os.path,"ismount",lambda _:True)
    r=retention.prune(root,lambda:True,now=1_000_000,
                      usage_func=lambda _:SimpleNamespace(free=100*1024**3,used=1,total=101*1024**3))
    assert r['removed']==0 and outside.exists() and link.is_symlink()


def test_login_lockout_and_admin_storage_settings(monkeypatch,tmp_path):
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"DB",tmp_path/"pinvr.db")
    main.LOGIN_ATTEMPTS.clear()
    with TestClient(main.app) as client:
        for _ in range(main.LOGIN_MAX_FAILURES):
            assert client.post("/api/login",json={"username":"Admin","password":"wrong-password"}).status_code==401
        assert client.post("/api/login",json={"username":"Admin","password":"raspberry"}).status_code==429
        main.LOGIN_ATTEMPTS.clear()
        assert client.post("/api/login",json={"username":"Admin","password":"raspberry"}).status_code==200
        assert client.get("/api/retention").json()["retention_days"]==14
        assert client.put("/api/retention",json={"retention_days":30,"max_used_percent":85,"min_free_gb":10}).status_code==200
        assert client.get("/api/retention").json()["min_free_gb"]==10
        assert client.put("/api/retention",json={"retention_days":0,"max_used_percent":20,"min_free_gb":0}).status_code==422
        assert client.post("/api/users",json={"username":"ViewerMember","password":"Viewer-Long-Password2026"}).status_code==201
        with TestClient(main.app) as viewer:
            viewer.post("/api/login",json={"username":"ViewerMember","password":"Viewer-Long-Password2026"})
            assert viewer.get("/api/retention").status_code==403
            assert viewer.put("/api/retention",json={"retention_days":1,"max_used_percent":75,"min_free_gb":1}).status_code==403
            assert viewer.post("/api/retention/run").status_code==403
    main.LOGIN_ATTEMPTS.clear()


def test_disconnected_usb_stops_existing_recorders(monkeypatch,tmp_path):
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"DB",tmp_path/"pinvr.db")
    with TestClient(main.app) as client:
        called=[]
        class FakeProcess:
            def poll(self):return None
            def terminate(self):called.append('terminated')
            def wait(self,timeout):return 0
        main.PROCS[100000]=FakeProcess()
        monkeypatch.setattr(main,"is_recording_mounted",lambda:False)
        monkeypatch.setattr(main.motion,"configure",lambda *args:None)
        main.reconcile()
        assert called==['terminated']
        assert not main.PROCS
