import os
import tempfile
from pathlib import Path

os.environ['PINVR_DATA'] = tempfile.mkdtemp(prefix='pinvr-test-')
os.environ['PINVR_RECORDINGS'] = tempfile.mkdtemp(prefix='pinvr-clips-')
from fastapi.testclient import TestClient
from pinvr.main import app, connect


def test_login_password_and_cameras():
    with TestClient(app) as c:
        assert c.get('/').status_code == 200
        assert c.get('/app.js').status_code == 200
        assert c.get('/api/me').status_code == 401
        r=c.post('/api/login',json={'username':'Admin','password':'raspberry'})
        assert r.status_code==200 and r.json()['must_change']
        assert c.get('/api/cameras').status_code==200
        assert c.get('/api/me').json()['must_change'] is True
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
        assert c.post('/api/live/99/offer',data='v=0').status_code==503
        assert c.post('/api/cameras',json={**cam,'host':'127.0.0.1/path'}).status_code==422
        assert c.get('/api/status').json()['storage_mounted'] is False
        assert c.post('/api/logout').status_code==200
        assert c.get('/api/me').status_code==401


def test_network_port_settings(monkeypatch, tmp_path):
    from pinvr import main
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DB', tmp_path/'pinvr.db')
    with TestClient(app) as c:
        assert c.get('/api/network').status_code==401
        c.post('/api/login',json={'username':'Admin','password':'raspberry'})
        assert c.get('/api/network').status_code==200
        c.post('/api/password',json={'current':'raspberry','new':'AnotherStrongPass2026#'})
        assert c.get('/api/network').json()['port']==8080
        assert c.post('/api/network',json={'port':8091,'current_password':'AnotherStrongPass2026#'}).status_code==409
        monkeypatch.setenv('PINVR_MANAGED','1')
        triggered=[]
        monkeypatch.setattr(main,'schedule_restart',lambda:triggered.append(True))
        assert c.post('/api/network',json={'port':8555,'current_password':'AnotherStrongPass2026#'}).status_code==422
        assert c.post('/api/network',json={'port':8091,'current_password':'wrong'}).status_code==401
        changed=c.post('/api/network',json={'port':8091,'current_password':'AnotherStrongPass2026#'})
        assert changed.status_code==200 and changed.json()['restarting'] and triggered
        assert c.get('/api/network').json()['port']==8091
        assert c.post('/api/network',json={'port':8091,'current_password':'AnotherStrongPass2026#'}).json()['restarting'] is False


def test_viewer_permissions_and_password_lifecycle(monkeypatch, tmp_path):
    from pinvr import main
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DB', tmp_path/'pinvr.db')
    with TestClient(app) as admin:
        admin.post('/api/login',json={'username':'Admin','password':'raspberry'})
        assert admin.post('/api/password',json={'current':'raspberry','new':'UniqueAdminPassword2026$'}).status_code==200
        assert admin.get('/api/users').json()[0]['role']=='admin'
        cam={'name':'Driveway','host':'192.168.3.50','recording':False,'password':'cam-secret'}
        assert admin.post('/api/cameras',json=cam).status_code==201
        made=admin.post('/api/users',json={'username':'GuestViewer','password':'TemporaryViewer2026!'} )
        assert made.status_code==201 and made.json()['role']=='viewer'
        assert admin.post('/api/users',json={'username':'guestviewer','password':'TemporaryViewer2026!'}).status_code==409
        with TestClient(app) as viewer:
            r=viewer.post('/api/login',json={'username':'GuestViewer','password':'TemporaryViewer2026!'})
            assert r.status_code==200 and r.json()['role']=='viewer' and r.json()['must_change']
            assert viewer.get('/api/cameras').status_code==200
            assert viewer.get('/api/clips').status_code==200
            assert viewer.post('/api/password',json={'current':'TemporaryViewer2026!','new':'ViewerLongTermSecret2026!'}).status_code==200
            assert viewer.get('/api/me').json()['role']=='viewer'
            result=viewer.get('/api/cameras').json()
            assert len(result)==1 and result[0]['name']=='Driveway' and 'host' not in result[0] and 'password' not in result[0]
            for url in ['/api/users','/api/network','/api/status']:
                assert viewer.get(url).status_code==403,url
            assert viewer.get('/api/clips/1/20260101-010101.mp4').status_code in (404,503)
            assert viewer.post('/api/cameras',json=cam).status_code==403
            assert viewer.put('/api/cameras/1',json=cam).status_code==403
            assert viewer.delete('/api/cameras/1').status_code==403
            assert viewer.post('/api/network',json={'port':8090,'current_password':'ViewerLongTermSecret2026!'}).status_code==403
            assert viewer.post('/api/users',json={'username':'evil','password':'Password2026!aaaa'}).status_code==403
            assert viewer.delete('/api/users/Admin').status_code==403
            assert admin.put('/api/users/GuestViewer/password',json={'password':'ReplacementViewer2026!'}).status_code==200
            assert viewer.get('/api/cameras').status_code==401
        assert admin.delete('/api/users/Admin').status_code==404
        assert admin.delete('/api/users/GuestViewer').status_code==200
        assert len(admin.get('/api/users').json())==1


def test_migrates_existing_admin_schema(monkeypatch,tmp_path):
    import sqlite3
    from pinvr import main
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.setattr(main,'DB',tmp_path/'pinvr.db')
    with sqlite3.connect(main.DB) as db:
        db.execute('CREATE TABLE users (name TEXT PRIMARY KEY COLLATE NOCASE, hash TEXT NOT NULL, must_change INTEGER NOT NULL)')
        db.execute('INSERT INTO users VALUES (?,?,0)',('Admin',main.password_hash('SomeExistingStrongPassword!')))
    with TestClient(app) as c:
        assert c.post('/api/login',json={'username':'Admin','password':'SomeExistingStrongPassword!'}).json()['role']=='admin'
        assert c.get('/api/users').json()[0]['username']=='Admin'


def test_password_reminder_nonblocking_and_viewer_recording_access(monkeypatch, tmp_path):
    from pinvr import main
    monkeypatch.setattr(main,'DATA',tmp_path/'db')
    monkeypatch.setattr(main,'DB',tmp_path/'db'/'pinvr.db')
    recording_dir=tmp_path/'recordings'
    recording_dir.mkdir()
    monkeypatch.setattr(main,'RECORDINGS',recording_dir)
    monkeypatch.setattr(main,'is_recording_mounted',lambda: True)
    with TestClient(app) as admin:
        assert admin.post('/api/login',json={'username':'Admin','password':'raspberry'}).json()['must_change']
        # The warning must not prevent normal API access before updating password.
        assert admin.get('/api/network').status_code==200
        assert admin.get('/api/users').status_code==200
        cam={'name':'Public View','host':'192.168.12.10','enabled':True}
        cid=admin.post('/api/cameras',json=cam).json()['id']
        hidden_id=admin.post('/api/cameras',json={**cam,'name':'Offline Cam','enabled':False}).json()['id']
        for camera_id in (cid,hidden_id):
            directory=recording_dir/str(camera_id)
            directory.mkdir()
            (directory/'20261110-120000.mp4').write_bytes(b'0'*2048)
        assert admin.post('/api/users',json={'username':'FamilyViewer','password':'SafeViewerPassword2026!'}).status_code==201
        with TestClient(app) as viewer:
            vr=viewer.post('/api/login',json={'username':'FamilyViewer','password':'SafeViewerPassword2026!'})
            assert vr.status_code==200 and vr.json()['must_change']
            assert viewer.get('/api/me').json()['must_change']
            assert viewer.get('/api/cameras').status_code==200
            clips=viewer.get('/api/clips')
            assert clips.status_code==200
            assert len(clips.json())==1 and clips.json()[0]['camera_id']==cid
            assert viewer.get(f'/api/clips/{cid}/20261110-120000.mp4').status_code==200
            assert viewer.get(f'/api/clips/{hidden_id}/20261110-120000.mp4').status_code==404
            assert viewer.get('/api/clips?camera_id='+str(hidden_id)).status_code==404
            for url in ('/api/users','/api/network','/api/status'):
                assert viewer.get(url).status_code==403
            assert viewer.post('/api/cameras',json=cam).status_code==403
            assert viewer.post('/api/password',json={'current':'SafeViewerPassword2026!','new':'UniqueViewerPassword2026!'}).status_code==200
            assert viewer.get('/api/me').json()['must_change'] is False
        assert admin.get('/api/users').status_code==200


def test_recording_preferences_motion_token_and_snapshots(monkeypatch,tmp_path):
    from pinvr import main
    monkeypatch.setattr(main,'DATA',tmp_path/'db')
    monkeypatch.setattr(main,'DB',tmp_path/'db'/'pinvr.db')
    monkeypatch.setattr(main,'RECORDINGS',tmp_path/'recordings')
    (tmp_path/'recordings').mkdir()
    monkeypatch.setattr(main,'is_recording_mounted',lambda:True)
    monkeypatch.setattr(main,'_capture_motion_event',lambda camera:None)
    with TestClient(app) as a:
        a.post('/api/login',json={'username':'Admin','password':'raspberry'})
        base={'name':'Main Gate','host':'192.168.1.33','recording':False,'recording_mode':'motion',
              'record_format':'mkv','snapshot_count':7}
        assert a.post('/api/cameras',json={**base,'snapshot_count':8}).status_code==422
        assert a.post('/api/cameras',json={**base,'record_format':'avi'}).status_code==422
        res=a.post('/api/cameras',json=base)
        assert res.status_code==201,res.text
        cid=res.json()['id']
        row=a.get('/api/cameras').json()[0]
        assert row['record_format']=='mkv' and row['snapshot_count']==7 and row['recording_mode']=='motion'
        assert a.post('/api/users',json={'username':'ViewOnly','password':'UniqueViewerPass2026!'}).status_code==201
        key=a.post(f'/api/cameras/{cid}/motion-token')
        assert key.status_code==200
        secret=key.json()['token']
        assert a.post(f'/api/events/motion/{cid}').status_code==401
        assert a.post(f'/api/events/motion/{cid}',headers={'X-PiNVR-Event-Key':secret}).status_code==200
        assert a.post(f'/api/events/motion/{cid}',headers={'X-PiNVR-Event-Key':secret}).status_code==429
        with TestClient(app) as viewer:
            viewer.post('/api/login',json={'username':'ViewOnly','password':'UniqueViewerPass2026!'})
            assert viewer.get('/api/onvif/discover').status_code==403
            assert viewer.post(f'/api/cameras/{cid}/motion-token').status_code==403
            assert viewer.post(f'/api/cameras/{cid}/snapshots').status_code==403
            assert viewer.post(f'/api/cameras/{cid}/test-motion').status_code==403
            assert viewer.get('/api/snapshots').status_code==200
            assert viewer.get('/api/events/motion').status_code==200
            assert viewer.put(f'/api/cameras/{cid}',json=base).status_code==403
        assert a.post('/api/cameras',json={**base,'recording_mode':'off'}).status_code==201


def test_storage_discovery_filters_system_and_busy_drives(monkeypatch):
    from pinvr import storage
    devices = [
        {'name':'mmcblk0','path':'/dev/mmcblk0','type':'disk','tran':None,'rm':False,
         'children':[{'path':'/dev/mmcblk0p2','type':'part','fstype':'ext4','uuid':'OS-UUID',
                      'mountpoints':['/'],'ro':False}]},
        {'name':'sda','path':'/dev/sda','type':'disk','tran':'usb','rm':False,'model':'USB HDD',
         'children':[{'path':'/dev/sda1','type':'part','fstype':'ext4','uuid':'SAFE-USB-123',
                      'mountpoints':[None],'size':2000000000000,'ro':False},
                     {'path':'/dev/sda2','type':'part','fstype':'ext4','uuid':'BUSY-USB-123',
                      'mountpoints':['/media/other'],'ro':False},
                     {'path':'/dev/sda3','type':'part','fstype':'ntfs','uuid':'NTFS-USB-123',
                      'mountpoints':[None],'ro':False}]}
    ]
    monkeypatch.setattr(storage,'_tree',lambda:devices)
    monkeypatch.setattr(storage,'_run',lambda *args,**kwargs:type('Result',(),{'stdout':'/dev/mmcblk0p2'})())
    items=storage.discover()
    assert len(items)==1 and items[0]['uuid']=='SAFE-USB-123'
    assert items[0]['device']=='/dev/sda1'


def test_storage_setup_requires_admin_and_confirmation(monkeypatch,tmp_path):
    from pinvr import main
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.setattr(main,'DB',tmp_path/'pinvr.db')
    monkeypatch.setattr(main.storage,'discover',lambda:[{'uuid':'SAFE-USB-123','filesystem':'ext4','model':'USB HDD','size_bytes':10000,'device':'/dev/sda1','mounted':False}])
    monkeypatch.setattr(main.storage,'configured',lambda:{'mounted':False,'details':''})
    calls=[]
    def fake_helper(argv,**kwargs):
        calls.append(argv)
        return type('Result',(),{'stdout':'{"ok":true,"message":"Mounted"}','returncode':0})()
    monkeypatch.setattr(main.subprocess,'run',fake_helper)
    with TestClient(app) as admin:
        assert admin.get('/api/storage/drives').status_code==401
        admin.post('/api/login',json={'username':'Admin','password':'raspberry'})
        assert admin.get('/api/storage/drives').json()['drives'][0]['uuid']=='SAFE-USB-123'
        body={'uuid':'SAFE-USB-123','current_password':'raspberry'}
        assert admin.post('/api/storage/configure',json=body).status_code==403  # Missing same-origin header
        assert admin.post('/api/storage/configure',json={**body,'current_password':'bad'},headers={'Origin':'http://testserver'}).status_code==403
        good=admin.post('/api/storage/configure',json=body,headers={'Origin':'http://testserver'})
        assert good.status_code==200 and good.json()['ok']
        assert len(calls)==1 and calls[0][-1]=='SAFE-USB-123'
        admin.post('/api/users',json={'username':'Readonly123','password':'ViewerPassword2026!'} )
        with TestClient(app) as viewer:
            viewer.post('/api/login',json={'username':'Readonly123','password':'ViewerPassword2026!'})
            assert viewer.get('/api/storage/drives').status_code==403
            assert viewer.post('/api/storage/configure',json=body,headers={'Origin':'http://testserver'}).status_code==403
