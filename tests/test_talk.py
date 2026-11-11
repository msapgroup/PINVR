import tempfile
from fastapi.testclient import TestClient
from pinvr import main

def test_talk_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "DB", tmp_path / "pinvr.db")
    monkeypatch.setattr(main.relay, "is_running", lambda: False)
    with TestClient(main.app) as admin:
        admin.post("/api/login", json={"username":"Admin","password":"raspberry"})
        c = admin.post("/api/cameras", json={"name":"Entry","host":"192.168.5.60","talk_enabled":True})
        assert c.status_code == 201
        cid = c.json()["id"]
        assert admin.post("/api/users", json={"username":"CameraViewer","password":"StrongUniqueViewer2026!"}).status_code == 201
        with TestClient(main.app) as viewer:
            viewer.post("/api/login", json={"username":"CameraViewer","password":"StrongUniqueViewer2026!"})
            assert viewer.get("/api/me").json()["talk_allowed"] is False
            assert viewer.get("/api/cameras").json()[0]["talk_enabled"] is False
            assert viewer.post(f"/api/talk/{cid}/offer", content="v=0").status_code == 403
            assert viewer.put("/api/users/CameraViewer/talk",json={"talk_allowed":True}).status_code == 403
            assert admin.put("/api/users/CameraViewer/talk",json={"talk_allowed":True}).status_code == 200
            assert viewer.get("/api/me").json()["talk_allowed"] is True
            assert viewer.get("/api/cameras").json()[0]["talk_enabled"] is True
            assert viewer.post(f"/api/talk/{cid}/offer",content="v=0").status_code == 503
            assert admin.put("/api/users/CameraViewer/talk",json={"talk_allowed":False}).status_code == 200
            assert viewer.post(f"/api/talk/{cid}/offer", content="v=0").status_code == 403
        assert admin.put("/api/users/Admin/talk",json={"talk_allowed":True}).status_code == 404
        assert admin.put(f"/api/cameras/{cid}",json={"name":"Entry","host":"192.168.5.60","talk_enabled":False}).status_code == 200
        assert admin.post(f"/api/talk/{cid}/offer",content="v=0").status_code == 403
