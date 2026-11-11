"""RTSP connection test does not need a camera or an external network."""
import json
import subprocess
from fastapi.testclient import TestClient
from pinvr import rtsp_test, main


def test_probe_reports_video_and_codec_without_credentials(monkeypatch):
    calls=[]
    def fake_run(command, **kwargs):
        calls.append((command,kwargs))
        payload={"streams":[{"codec_type":"video","codec_name":"h264",
                             "width":1920,"height":1080,"avg_frame_rate":"25/1"},
                            {"codec_type":"audio","codec_name":"aac"}]}
        return subprocess.CompletedProcess(command,0,stdout=json.dumps(payload).encode(),stderr=b"")
    monkeypatch.setattr(rtsp_test.subprocess,"run",fake_run)
    report=rtsp_test.inspect_stream("rtsp://camerauser:secret@192.168.1.8/channel","main")
    assert report["ok"] and report["codec"]=="h264"
    assert report["width"]==1920 and report["height"]==1080 and report["fps"]==25
    assert report["audio"]==["aac"] and "secret" not in str(report)
    assert calls and calls[0][1]["timeout"]==9
    assert calls[0][0][0]=="ffprobe" and "-rtsp_transport" in calls[0][0]


def test_probe_distinguishes_errors_and_timeouts(monkeypatch):
    def denied(cmd, **kw):
        return subprocess.CompletedProcess(cmd,1,stdout=b"",
                                           stderr=b"Server returned 401 Unauthorized rtsp://admin:secret@127.0.0.1")
    monkeypatch.setattr(rtsp_test.subprocess,"run",denied)
    r=rtsp_test.inspect_stream("rtsp://admin:secret@127.0.0.1/live","substream")
    assert not r["ok"] and "username or password" in r["message"]
    assert "secret" not in str(r)
    def timed_out(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd,kw["timeout"])
    monkeypatch.setattr(rtsp_test.subprocess,"run",timed_out)
    assert "timed out" in rtsp_test.inspect_stream("rtsp://127.0.0.1","main")["message"]


def test_admin_connection_test_is_read_only_and_reuses_saved_secret(monkeypatch,tmp_path):
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"DB",tmp_path/"pinvr.db")
    collected=[]
    def fake_inspect(camera,builder):
        collected.append(camera.copy())
        return {"ok":True,"streams":[{"stream":"main","ok":True,"codec":"h264"},
                                     {"stream":"substream","ok":True,"codec":"h264"}],
                "saved":False,"tested":True}
    monkeypatch.setattr(main.rtsp_test,"inspect_camera",fake_inspect)
    with TestClient(main.app) as admin:
        assert admin.post("/api/login",json={"username":"Admin","password":"raspberry"}).status_code==200
        camera={"name":"Yard","host":"192.168.2.101","username":"camadmin","password":"secret",
                "main_path":"/Streaming/Channels/101","sub_path":"/Streaming/Channels/102"}
        assert admin.post("/api/cameras/test-connection",json={"camera":camera}).status_code==200
        assert admin.get("/api/cameras").json()==[]
        saved=admin.post("/api/cameras",json=camera)
        assert saved.status_code==201
        cid=saved.json()["id"]
        update={**camera,"password":""}
        tested=admin.post("/api/cameras/test-connection",json={"camera":update,"camera_id":cid})
        assert tested.status_code==200
        assert collected[-1]["password"]=="secret"
        assert admin.post("/api/cameras/test-connection",json={
            "camera":update,"camera_id":999999}).status_code==404
        assert admin.post("/api/cameras/test-connection",json={
            "camera":{**camera,"host":"bad/host"}}).status_code==422
        assert admin.post("/api/users",json={"username":"CameraViewer","password":"ViewerUniquePassword2026!"}).status_code==201
        with TestClient(main.app) as viewer:
            assert viewer.post("/api/login",json={"username":"CameraViewer","password":"ViewerUniquePassword2026!"}).status_code==200
            assert viewer.post("/api/cameras/test-connection",json={"camera":camera}).status_code==403
