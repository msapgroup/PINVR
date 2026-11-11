import os
import tempfile
from pathlib import Path

os.environ['PINVR_DATA'] = tempfile.mkdtemp(prefix='pinvr-test-')
os.environ['PINVR_RECORDINGS'] = tempfile.mkdtemp(prefix='pinvr-clips-')
from fastapi.testclient import TestClient
from pinvr.main import app, connect


def test_login_password_and_cameras():
    with TestClient(app) as c:
        assert c.get('/api/me').status_code == 401
        r=c.post('/api/login',json={'username':'Admin','password':'raspberry'})
        assert r.status_code==200 and r.json()['must_change']
        assert c.get('/api/cameras').status_code==403
        assert c.post('/api/password',json={'current':'raspberry','new':'MuchBetterPassword2026!'}).status_code==200
        assert c.get('/api/cameras').json()==[]
        cam={'name':'Front Entrance','host':'192.168.1.100','main_path':'/Streaming/Channels/101','sub_path':'/Streaming/Channels/102','recording':False}
        r=c.post('/api/cameras',json=cam)
        assert r.status_code==201
        cid=r.json()['id']
        assert c.get('/api/cameras').json()[0]['name']=='Front Entrance'
        assert 'password' not in c.get('/api/cameras').json()[0]
        cam['name']='Side Gate'
        assert c.put(f'/api/cameras/{cid}',json=cam).status_code==200
        assert c.delete(f'/api/cameras/{cid}').status_code==200
        assert c.get('/api/cameras').json()==[]
        assert c.post('/api/cameras',json={**cam,'host':'127.0.0.1/path'}).status_code==422
        assert c.get('/api/status').json()['storage_mounted'] is False
        assert c.post('/api/logout').status_code==200
        assert c.get('/api/me').status_code==401
