"""Notification destination tests do not require any external provider."""
from fastapi.testclient import TestClient
from pinvr import main, notifications


def test_alert_destinations_are_admin_only_and_hide_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DB', tmp_path/'pinvr.db')
    delivered=[]
    monkeypatch.setattr(notifications, 'send_test',
                        lambda url: delivered.append(url) or {'ok':True,'message':'Test accepted'})
    with TestClient(main.app) as admin:
        admin.post('/api/login',json={'username':'Admin','password':'raspberry'})
        payload={'label':'My Phone','url':'ntfy://private-random-topic',
                 'enabled':True,'motion':True,'storage':True,'cooldown_seconds':120}
        created=admin.post('/api/alerts/destinations',json=payload)
        assert created.status_code==201,created.text
        did=created.json()['id']
        assert created.json()['provider']=='ntfy'
        assert 'url' not in created.json()
        records=admin.get('/api/alerts/destinations').json()
        assert len(records)==1 and 'private-random-topic' not in str(records)
        assert admin.post('/api/alerts/destinations',json={**payload,'url':'file:///etc/passwd'}).status_code==422
        assert admin.post(f'/api/alerts/destinations/{did}/test').status_code==200
        assert delivered == ['ntfy://private-random-topic']
        updated=admin.put(f'/api/alerts/destinations/{did}',json={**payload,'url':'KEEP_EXISTING','motion':False})
        assert updated.status_code==200 and updated.json()['motion'] is False
        admin.post('/api/users',json={'username':'CameraViewer','password':'SuperStrongViewer2026!'})
        with TestClient(main.app) as viewer:
            viewer.post('/api/login',json={'username':'CameraViewer','password':'SuperStrongViewer2026!'})
            assert viewer.get('/api/alerts/destinations').status_code==403
            assert viewer.post('/api/alerts/destinations',json=payload).status_code==403
            assert viewer.post(f'/api/alerts/destinations/{did}/test').status_code==403
            assert viewer.delete(f'/api/alerts/destinations/{did}').status_code==403
        assert admin.delete(f'/api/alerts/destinations/{did}').status_code==200
        assert admin.get('/api/alerts/destinations').json()==[]


def test_alert_delivery_respects_cooldown_and_filter(monkeypatch):
    sent=[]
    monkeypatch.setattr(notifications._EXECUTOR,'submit',
                        lambda fn,*args: sent.append((fn,args)))
    monkeypatch.setattr(notifications.time,'monotonic',lambda:123456.0)
    notifications._LAST.clear()
    data=[{'id':41001,'label':'Phone','url':'ntfy://private-random-topic',
           'enabled':1,'events':'motion,storage','cooldown_seconds':120}]
    notifications.dispatch(data,'motion','Front Driveway')
    notifications.dispatch(data,'motion','Front Driveway')
    notifications.dispatch(data,'motion','Side Gate')
    notifications.dispatch(data,'storage')
    assert len(sent)==3
    notifications.dispatch([{**data[0],'enabled':0}],'motion','Other')
    assert len(sent)==3
