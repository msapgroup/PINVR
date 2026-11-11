"""Safe read-only discovery and narrowly scoped privileged mounting for PiNVR.

No formatting, partitioning, unmounting, or recursive ownership changes.
"""
import json
import os
import pwd
import re
import subprocess
from pathlib import Path

MOUNT = '/mnt/pinvr-recordings'
FSTAB = Path('/etc/fstab')
SUPPORTED = {'ext4', 'exfat'}
UUID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{2,127}\Z')


def _run(args, check=True):
    return subprocess.run(args, text=True, capture_output=True, timeout=15, check=check)


def _tree():
    return json.loads(_run(['lsblk', '-J', '-b', '-o', 'NAME,PATH,TYPE,SIZE,FSTYPE,UUID,MOUNTPOINTS,TRAN,RM,MODEL,RO']).stdout)['blockdevices']


def discover():
    """Only return USB/removable filesystem partitions unmounted or already at our target.

    Never offer mounted operating-system partitions or mounted volumes used elsewhere.
    """
    try:
        devices = _tree()
        root_src = _run(['findmnt', '-n', '-o', 'SOURCE', '/'], check=False).stdout.strip()
    except (subprocess.SubprocessError, ValueError, KeyError, OSError) as exc:
        raise RuntimeError('Unable to inspect USB drives') from exc
    out = []
    # Exclude an entire USB disk when any of its partitions hosts the running OS.
    # Checking just the candidate partition is not enough on USB-booted Pis.
    def has_system_mount(node):
        mounts = [m for m in node.get('mountpoints') or [] if m]
        if any(m in ('/', '/boot', '/boot/firmware', '/usr', '/var') for m in mounts):
            return True
        return any(has_system_mount(child) for child in node.get('children') or [])

    def walk(node, is_external=False, model=''):
        external = is_external or node.get('tran') == 'usb' or bool(node.get('rm'))
        model = model or node.get('model') or ''
        # A filesystem may live directly on a USB disk without a partition.
        fs = (node.get('fstype') or '').lower()
        uuid = node.get('uuid') or ''
        mounts = [x for x in (node.get('mountpoints') or []) if x]
        path = node.get('path') or ''
        if (external and fs in SUPPORTED and UUID_PATTERN.fullmatch(uuid)
                and not bool(node.get('ro')) and (not mounts or mounts == [MOUNT])
                and not (root_src and (path == root_src or root_src.startswith(path+'[')))):
            out.append({'uuid': uuid, 'device': path, 'filesystem': fs,
                        'size_bytes': node.get('size') or 0, 'model': model.strip(),
                        'mounted': mounts == [MOUNT]})
        for child in node.get('children') or []:
            walk(child, external, model)
    for item in devices:
        if not has_system_mount(item):
            walk(item)
    return out


def configured():
    try:
        result = _run(['findmnt', '-n', '-o', 'SOURCE,FSTYPE', MOUNT], check=False)
        return {'mounted': result.returncode == 0, 'details': result.stdout.strip()}
    except (OSError, subprocess.SubprocessError):
        return {'mounted': False, 'details': ''}


def configure(uuid):
    """Root-only function; invoked by a fixed sudoers command with validated UUID.

    A failed mount rolls fstab back. Existing mounts at other paths are not touched.
    """
    if os.geteuid() != 0:
        raise PermissionError('Administrator storage helper required')
    if not UUID_PATTERN.fullmatch(uuid):
        raise ValueError('Invalid UUID')
    candidates = {d['uuid']: d for d in discover()}
    if uuid not in candidates:
        raise ValueError('Drive not available; connect an unmounted USB drive')
    device = candidates[uuid]
    existing = configured()
    if existing['mounted']:
        if device['mounted']:
            return {'ok': True, 'message': 'Selected USB drive is already mounted'}
        raise ValueError('Recording location is already used by another device')
    # Do not modify fstab if there is already an entry for this target.
    original = FSTAB.read_text()
    if any(line.strip() and not line.lstrip().startswith('#') and len(line.split()) > 1
           and line.split()[1] == MOUNT for line in original.splitlines()):
        raise ValueError('A recording mount entry already exists. Ask an administrator to inspect it.')
    owner = pwd.getpwnam('pinvr')
    if device['filesystem'] == 'ext4':
        options = 'defaults,nofail,nosuid,nodev,x-systemd.device-timeout=10'
        suffix = '0 2'
    elif device['filesystem'] == 'exfat':
        options = f'defaults,nofail,nosuid,nodev,uid={owner.pw_uid},gid={owner.pw_gid},umask=007,x-systemd.device-timeout=10'
        suffix = '0 0'
    else:
        raise ValueError('Unsupported filesystem')
    line = f'UUID={uuid} {MOUNT} {device["filesystem"]} {options} {suffix}\n'
    target = Path(MOUNT)
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise ValueError('Recording directory is not empty while unmounted')
    # Back up before writing and preserve existing file mode and ownership.
    import shutil
    import tempfile
    import time
    backup = FSTAB.with_name('fstab.pinvr-backup-'+str(int(time.time())))
    shutil.copy2(FSTAB, backup)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir='/etc', prefix='.fstab.pinvr-', delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(original.rstrip('\n')+'\n'+line)
        shutil.copymode(FSTAB, tmp)
        os.chown(tmp, 0, 0)
        os.replace(tmp, FSTAB)
        _run(['systemctl', 'daemon-reload'])
        _run(['mount', MOUNT])
        mounted = configured()
        if not mounted['mounted']:
            raise RuntimeError('USB drive did not mount')
        if device['filesystem'] == 'ext4':
            os.chown(target, owner.pw_uid, owner.pw_gid)
            os.chmod(target, 0o750)
        _run(['runuser', '-u', 'pinvr', '--', 'touch', MOUNT+'/.pinvr-write-test'])
        _run(['runuser', '-u', 'pinvr', '--', 'rm', MOUNT+'/.pinvr-write-test'])
        return {'ok': True, 'message': 'USB recording drive ready and configured for reboot'}
    except Exception:
        # Avoid leaving a new, broken fstab entry in place.
        shutil.copy2(backup, FSTAB)
        _run(['systemctl', 'daemon-reload'], check=False)
        raise
    finally:
        if tmp and tmp.exists():
            tmp.unlink()


def main():
    import sys
    if len(sys.argv) != 2 or not UUID_PATTERN.fullmatch(sys.argv[1]):
        raise SystemExit('Usage: pinvr-storage-helper UUID')
    try:
        print(json.dumps(configure(sys.argv[1])))
    except (ValueError, RuntimeError, PermissionError, subprocess.SubprocessError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:250]}))
        raise SystemExit(1)

if __name__ == '__main__':
    main()
