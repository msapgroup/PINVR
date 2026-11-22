"""Conservative ONVIF PTZ control for cameras with ONVIF enabled.

Credentials come from saved camera profiles. Only services hosted on the same
camera address are accepted. Every move carries a short camera-side timeout.
Camera firmware / profile capability still requires physical verification.
"""
import base64
import datetime
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx

NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "tt": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
}
CACHE = {}


class CameraPTZError(Exception):
    pass


def header(username, password):
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = os.urandom(16)
    digest = base64.b64encode(hashlib.sha1(nonce+created.encode()+password.encode()).digest()).decode()
    return f"""<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken><wsse:Username>{_xml(username)}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password><wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>"""


def _xml(s):
    import xml.sax.saxutils as su
    return su.escape(str(s))


def endpoint_for_camera(camera):
    host = camera["host"]
    # RTSP port is independent of ONVIF service port.
    if ':' in host:
        host = host.rsplit(':', 1)[0]
    return f"http://{host}:{int(camera.get('onvif_port') or 80)}/onvif/device_service"


def _validate_endpoint(url, camera):
    parsed = urlsplit(url)
    saved = camera["host"].rsplit(':', 1)[0] if ':' in camera["host"] else camera["host"]
    if parsed.scheme != "http" or (parsed.hostname or "").casefold() != saved.casefold():
        raise CameraPTZError("Camera returned an unsafe ONVIF service address")
    if parsed.username or parsed.password or parsed.fragment or parsed.port is None or parsed.port < 1:
        raise CameraPTZError("Invalid camera ONVIF service address")
    return url


def call(url, camera, body):
    request=f"""<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="{NS['soap']}" xmlns:tds="{NS['tds']}" xmlns:trt="{NS['trt']}" xmlns:tptz="{NS['tptz']}" xmlns:tt="{NS['tt']}"><s:Header>{header(camera.get('username') or '',camera.get('password') or '')}</s:Header><s:Body>{body}</s:Body></s:Envelope>"""
    try:
        with httpx.Client(timeout=4.5,trust_env=False) as client:
            res=client.post(url,content=request.encode(),headers={"Content-Type":"application/soap+xml; charset=utf-8"})
            res.raise_for_status()
            root=ET.fromstring(res.content[:262144])
    except (httpx.HTTPError,ET.ParseError,ValueError) as exc:
        raise CameraPTZError("ONVIF camera did not accept this command or is unreachable") from None
    fault=root.find(".//{"+NS["soap"]+"}Fault")
    if fault is not None:
        raise CameraPTZError("Camera rejected ONVIF command; check permissions and PTZ support")
    return root


def _xaddr(root, service):
    node=root.find(".//{"+NS["tt"]+"}"+service+"/{"+NS["tt"]+"}XAddr")
    if node is None or not node.text:
        raise CameraPTZError("Camera does not advertise ONVIF "+service)
    return node.text.strip()


def detect(camera):
    """Find ONVIF PTZ and media endpoints and a PTZ-enabled media profile."""
    capabilities=call(endpoint_for_camera(camera),camera,"<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>")
    media=_validate_endpoint(_xaddr(capabilities,"Media"),camera)
    ptz=_validate_endpoint(_xaddr(capabilities,"PTZ"),camera)
    profiles=call(media,camera,"<trt:GetProfiles/>")
    for profile in profiles.findall(".//{"+NS["trt"]+"}Profiles"):
        token=profile.attrib.get("token","")
        if token and profile.find("{"+NS["tt"]+"}PTZConfiguration") is not None:
            return {"endpoint":ptz,"profile":token}
    raise CameraPTZError("No PTZ-capable ONVIF media profile found")


def move(camera, direction):
    if direction not in ("left","right","up","down","zoom_in","zoom_out","stop"):
        raise CameraPTZError("Invalid PTZ command")
    if not camera.get("username") or not camera.get("password"):
        raise CameraPTZError("ONVIF PTZ needs camera credentials")
    key=(camera["id"],camera["host"],camera.get("onvif_port"),camera.get("username"))
    # Cache no passwords or tokens derived from credentials.
    entry=CACHE.get(key)
    now=__import__("time").monotonic()
    if entry is None or entry[0] < now:
        config=detect(camera)
        CACHE[key]=(now+120,config)
    else:
        config=entry[1]
    token=_xml(config["profile"])
    if direction=="stop":
        body=f'<tptz:Stop><tptz:ProfileToken>{token}</tptz:ProfileToken><tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>'
    else:
        x,y,z={"left":(-.5,0,0),"right":(.5,0,0),"up":(0,.5,0),"down":(0,-.5,0),
               "zoom_in":(0,0,.5),"zoom_out":(0,0,-.5)}[direction]
        body=f'<tptz:ContinuousMove><tptz:ProfileToken>{token}</tptz:ProfileToken><tptz:Velocity><tt:PanTilt x="{x}" y="{y}"/><tt:Zoom x="{z}"/></tptz:Velocity><tptz:Timeout>PT0.5S</tptz:Timeout></tptz:ContinuousMove>'
    call(config["endpoint"],camera,body)
    return {"ok":True,"direction":direction}
