"""Start native PiNVR on its configured port; fall back if the new port is occupied."""
import os
import socket
import sqlite3
from pathlib import Path
import uvicorn


def read_port():
    db_path=Path(os.environ.get('PINVR_DATA','/var/lib/pinvr'))/'pinvr.db'
    if not db_path.exists():
        return 8080
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        settings=dict(db.execute('SELECT key,value FROM settings'))
        try: port=int(settings.get('web_port','8080'))
        except ValueError: port=8080
        if not (1024<=port<=65535) or port in (1984,8555): port=8080
        try: fallback=int(settings.get('previous_port','8080'))
        except ValueError: fallback=8080
        with socket.socket() as test:
            try: test.bind(('0.0.0.0',port))
            except OSError:
                if port==fallback: raise
                with socket.socket() as check: check.bind(('0.0.0.0',fallback))
                db.execute("INSERT OR REPLACE INTO settings VALUES ('web_port',?)",(str(fallback),))
                db.commit()
                port=fallback
        return port


if __name__=='__main__':
    os.environ['PINVR_MANAGED']='1'
    uvicorn.run('pinvr.main:app',host='0.0.0.0',port=read_port(),workers=1,proxy_headers=False)
