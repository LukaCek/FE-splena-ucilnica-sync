"""Private one-click e.FE sign-in portal for a personal TrueNAS installation.

The portal and browser are separate unprivileged Compose services. Nothing
mounts the Docker socket. Chromium/noVNC are reachable only on an internal
Docker network; the portal alone binds to a private Tailscale host address.

A launch capability is stored in the private study folder as a link, NOT as
Moodle cookies or UL credentials. Only explicitly allow this on trusted devices.
Not a multi-user authentication platform or an authorized university OAuth app.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import ipaddress
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from .client import BASE, MoodleClient
from .pages import MARKER, write_generated

STATE = Path(os.getenv("SYNC_STATE_DIR", "/state"))
FE_ROOT = Path(os.getenv("FE_ROOT", "/data/FE"))
COOKIE = Path(os.getenv("FE_MOODLE_SESSION_FILE",
                         "/run/secrets/moodle-session-cookies.json"))
WEBDRIVER = os.getenv("FE_LOGIN_WEBDRIVER",
                       "http://fe-login-browser:4444").rstrip("/")
NOVNC = os.getenv("FE_LOGIN_NOVNC", "http://fe-login-browser:7900").rstrip("/")
PORT = 17899
SESSION_SECONDS = 20 * 60
ALLOWED_PRIVATE = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
LAUNCH_TOKEN_FILE = "login-access-token"


def private_base_url(value: str) -> str:
    """Require a literal private IPv4 address; public DNS/proxy are not accepted."""
    try:
        u = urlsplit(value)
        addr = ipaddress.ip_address(u.hostname or "")
        port = u.port
    except ValueError as exc:
        raise ValueError("FE_LOGIN_BASE_URL mora biti zaseben IPv4 NAS-a.") from exc
    if (u.scheme != "http" or not isinstance(addr, ipaddress.IPv4Address)
            or not any(addr in net for net in ALLOWED_PRIVATE)
            or u.username or u.password or u.path not in ("", "/")
            or u.query or u.fragment or port != PORT):
        raise ValueError("Dovoljen je samo HTTP na Tailscale/LAN IPv4 in vratih 17899.")
    return "http://" + str(addr) + ":" + str(PORT)


def launch_page(url: str, access: str) -> str:
    # Fragment does not reach the HTTP server, reverse proxies or request logs.
    dest = html.escape(url + "/connect#access=" + access, quote=True)
    return (
        '<!doctype html><html lang="sl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="refresh" content="0;url=' + dest + '">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;">'
        '<title>Poveži spletno učilnico</title>'
        '<style>body{font:16px system-ui;background:#f6f7f9;color:#20252c;'
        'max-width:600px;margin:9vh auto;padding:20px}main{background:white;'
        'border-radius:16px;padding:28px;border:1px solid #ddd}'
        'a.button{display:block;background:#b51d27;color:white;padding:15px;'
        'border-radius:9px;text-align:center;text-decoration:none;font-weight:700}'
        'p{line-height:1.55}</style></head><body>' + MARKER +
        '<main><h1>Poveži spletno učilnico</h1>'
        '<p>Prijava se odpre v zasebnem brskalniku na NAS-u. '
        'Uporabi svoj uradni račun UL SSO.</p>'
        '<p><a class="button" href="' + dest +
        '" referrerpolicy="no-referrer" rel="noreferrer">Prijava v e.FE</a></p>'
        '<p>Telefon mora biti povezan v isti Tailscale mreži kot NAS. '
        'Povezave ne posreduj drugim osebam.</p></main></body></html>'
    )


def load_or_make_access(state: Path) -> str:
    state.mkdir(parents=True, exist_ok=True)
    p = state / LAUNCH_TOKEN_FILE
    if p.exists():
        if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077:
            raise ValueError("Unsafe access-token file permissions.")
        token = p.read_text(encoding="ascii").strip()
        if len(token) < 40 or len(token) > 200:
            raise ValueError("Invalid existing access token.")
        return token
    value = secrets.token_urlsafe(48)
    # Create exclusively and atomically to avoid accidental replacement.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(value + "\n")
        f.flush()
        os.fsync(f.fileno())
    return value


def write_launcher(fe_root: Path, base_url: str, access: str) -> Path:
    relative = Path("Spletna-ucilnica/POVEZI.html")
    write_generated(fe_root, relative, launch_page(base_url, access))
    return relative


def security_headers(resp: web.StreamResponse) -> web.StreamResponse:
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def portal_html() -> str:
    return r'''<!doctype html><html lang="sl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Poveži e.FE</title>
<style>
*{box-sizing:border-box}
body{font:16px/1.5 system-ui;background:#f3f5f7;color:#16212c;margin:0}
header{padding:14px 22px;background:#aa1927;color:white;font-weight:700}
main{max-width:980px;margin:24px auto;padding:0 16px}
section{background:white;border:1px solid #ddd;border-radius:14px;padding:20px}
h1{font-size:24px;margin:0 0 12px}p{margin:7px 0 16px}
#viewer{width:100%;height:72vh;min-height:460px;border:1px solid #ddd;border-radius:10px}
#viewer[hidden]{display:none}
#status{font-weight:600;padding:10px 12px;background:#f6f7fa;border-radius:8px}
small{color:#526271}
</style></head><body>
<header>FE · Poveži spletno učilnico</header>
<main><section>
<h1>Prijava prek UL SSO</h1>
<p>Prijavi se v uradno spletno učilnico v brskalniku spodaj.
Ko bo prijava uspešna, bo sistem sam obnovil sinhronizacijo.</p>
<div id="status" role="status">Pripravljam zasebni brskalnik …</div>
<iframe id="viewer" title="Zasebni brskalnik za prijavo v UL SSO" hidden></iframe>
<p><small>Ta spletna stran je zasebni prehod na tvojem NAS-u, ne uradna prijavna stran UL.
GESLO vnesi samo, ko v brskalniku vidiš pravo domeno univerze.
Prijave in piškotkov ne pošiljaj v pogovor.</small></p>
</section></main>
<script>
"use strict";
const label = document.getElementById("status");
const viewer = document.getElementById("viewer");
let done = false;
function showState(s) {
  const text = {
    starting: "Zaganjam zasebni brskalnik …",
    login: "Prijavi se v uradno stran UL SSO v brskalniku.",
    verifying: "Preverjam prijavo in pripravljam sinhronizacijo …",
    synced: "Prijava je potrjena. Sinhronizacija e.FE je sprožena.",
    error: "Prijava še ni potrjena. Preveri brskalnik ali poskusi znova."
  };
  label.textContent = text[s] || "Čakam na prijavo …";
  if (s === "synced") {
    done = true;
    viewer.hidden = true;
    viewer.src = "about:blank";
  }
}
async function status() {
  if (done) return;
  try {
    const r = await fetch("/api/status", {cache:"no-store",credentials:"same-origin"});
    if (!r.ok) throw Error("Unauthorized");
    const j = await r.json();
    showState(j.status);
    if (j.status === "login" || j.status === "verifying") {
      if (viewer.hidden) {
        viewer.src = "/viewer/vnc.html?autoconnect=1&resize=scale&path=viewer/websockify";
        viewer.hidden = false;
      }
    }
  } catch (_) { showState("error"); }
}
async function begin() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get("access");
  history.replaceState({}, "", location.pathname);
  try {
    if (token) {
      const r = await fetch("/api/begin", {
        method:"POST",headers:{"Content-Type":"application/json"},
        credentials:"same-origin",body:JSON.stringify({access:token})
      });
      if (!r.ok) throw Error("Invalid link");
    }
    await status();
    setInterval(status, 4000);
  } catch (_) {
    label.textContent = "Povezava ni veljavna. Ponovno odpri POVEZI.html v svoji FE mapi.";
  }
}
begin();
</script></body></html>'''


class Portal:
    def __init__(self, state: Path, secret: Path, fe_root: Path, base_url: str,
                 access: str, webdriver_url: str = WEBDRIVER,
                 novnc_url: str = NOVNC):
        self.state, self.secret, self.fe_root = state, secret, fe_root
        self.base_url, self.access = base_url, access
        self.webdriver_url, self.novnc_url = webdriver_url, novnc_url
        self.client: ClientSession | None = None
        self.sid: str | None = None
        self.authorizations: dict[str, float] = {}
        self.started = 0.0
        self.current_status = "starting"
        self.lock = asyncio.Lock()
        self.poller: asyncio.Task | None = None

    async def http(self, method: str, path: str, *, data=None, timeout=30):
        assert self.client
        async with self.client.request(method, self.webdriver_url + path,
                                       json=data, timeout=ClientTimeout(total=timeout)) as res:
            body = await res.json()
            if res.status >= 400 or not isinstance(body, dict) or (
                    isinstance(body.get("value"), dict) and body["value"].get("error")):
                raise RuntimeError("Selenium request failed.")
            return body.get("value")

    def authorized(self, req: web.Request) -> bool:
        key = req.cookies.get("fe_login")
        exp = self.authorizations.get(key or "")
        return bool(key and exp and exp > time.monotonic())

    async def begin(self, req: web.Request):
        try:
            payload = await req.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest()
        if not isinstance(payload, dict) or not isinstance(payload.get("access"), str) \
                or not secrets.compare_digest(payload["access"], self.access):
            raise web.HTTPForbidden(text="Invalid private link")
        async with self.lock:
            await self.ensure_browser()
            session_key = secrets.token_urlsafe(32)
            self.authorizations.clear()  # Only one active owner at a time.
            self.authorizations[session_key] = time.monotonic() + SESSION_SECONDS
        response = web.json_response({"status": self.current_status})
        response.set_cookie("fe_login", session_key, httponly=True,
                            secure=False, samesite="Strict", path="/",
                            max_age=SESSION_SECONDS)
        return security_headers(response)

    async def ensure_browser(self):
        if self.sid and time.monotonic() - self.started < SESSION_SECONDS:
            return
        await self.close_browser()
        self.current_status = "starting"
        for _ in range(36):
            try:
                if (await self.http("GET", "/status", timeout=5) or {}).get("ready"):
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            raise RuntimeError("Selenium browser unavailable.")
        data = await self.http("POST", "/session", data={"capabilities": {
            "alwaysMatch": {"browserName": "chrome", "pageLoadStrategy": "none"}}}, timeout=90)
        if not isinstance(data, dict) or not isinstance(data.get("sessionId"), str):
            raise RuntimeError("Chromium did not start a Selenium session.")
        self.sid = data["sessionId"]
        self.started = time.monotonic()
        await self.http("POST", f"/session/{self.sid}/url",
                        data={"url": BASE + "/my/courses.php"}, timeout=45)
        self.current_status = "login"
        if self.poller:
            self.poller.cancel()
        self.poller = asyncio.create_task(self.watch_login(self.sid))

    async def close_browser(self):
        task, self.poller = self.poller, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        sid, self.sid = self.sid, None
        if sid:
            try:
                await self.http("DELETE", "/session/" + sid, timeout=12)
            except (Exception, asyncio.CancelledError):
                pass

    async def watch_login(self, sid: str):
        """Only verify after we are back on an authenticated Moodle page."""
        try:
            while self.sid == sid and time.monotonic() - self.started < SESSION_SECONDS:
                await asyncio.sleep(5)
                url = await self.http("GET", f"/session/{sid}/url", timeout=15)
                if not isinstance(url, str) or not url.startswith(BASE + "/") \
                        or "/login/" in url:
                    continue
                raw = await self.http("GET", f"/session/{sid}/cookie", timeout=15)
                cookies = filtered_cookies(raw)
                if not cookies:
                    continue
                self.current_status = "verifying"
                try:
                    await asyncio.to_thread(self.verify_and_persist, cookies)
                except Exception:
                    self.current_status = "login"
                    continue
                self.current_status = "synced"
                await self.close_browser()
                return
        except asyncio.CancelledError:
            return
        except Exception:
            self.current_status = "error"

    def verify_and_persist(self, cookies: list[dict]) -> None:
        """Never overwrite the existing secret before a successful Moodle read."""
        if not self.secret.is_file() or self.secret.is_symlink():
            raise RuntimeError("Existing Moodle session file missing.")
        client = MoodleClient(self.secret)
        client.session.cookies.clear()
        for row in cookies:
            client.session.cookies.set(row["name"], row["value"],
                                       domain=row["domain"], path=row["path"],
                                       secure=True)
        found = client.courses()
        if not found:
            raise RuntimeError("Moodle did not return any courses.")
        # Preserve existing inode: mounted into the sync container as a file.
        payload = json.dumps({"site": BASE, "cookies": cookies}, ensure_ascii=False)
        fd = os.open(self.secret, os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # Atomic wake-up event. Main sync loop reads it within seconds.
        fd, tmp = tempfile.mkstemp(prefix=".login-refresh-", dir=self.state)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(datetime.now(timezone.utc).isoformat())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state / "sync.request")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    async def status(self, req: web.Request):
        if not self.authorized(req):
            raise web.HTTPUnauthorized()
        return security_headers(web.json_response({"status": self.current_status}))

    async def viewer(self, req: web.Request):
        if not self.authorized(req) or not self.sid:
            raise web.HTTPForbidden()
        tail = req.match_info.get("tail", "")
        if ".." in tail.split("/") or "\\" in tail:
            raise web.HTTPBadRequest()
        assert self.client
        url = self.novnc_url + "/" + tail
        if req.query_string:
            url += "?" + req.query_string
        async with self.client.get(url, timeout=ClientTimeout(total=20),
                                   allow_redirects=False) as upstream:
            body = await upstream.read()
            headers = {
                "Content-Type": upstream.headers.get("Content-Type", "application/octet-stream"),
                "Cache-Control": "no-store",
            }
            return security_headers(web.Response(status=upstream.status, body=body,
                                                 headers=headers))

    async def websocket(self, req: web.Request):
        if not self.authorized(req) or not self.sid:
            raise web.HTTPForbidden()
        assert self.client
        front = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await front.prepare(req)
        backend_url = self.novnc_url.replace("http://", "ws://", 1) + "/websockify"
        try:
            async with self.client.ws_connect(backend_url, max_msg_size=16 * 1024 * 1024,
                                              timeout=20) as upstream:
                async def user_to_vnc():
                    async for msg in front:
                        if msg.type == WSMsgType.BINARY:
                            await upstream.send_bytes(msg.data)
                        elif msg.type == WSMsgType.TEXT:
                            await upstream.send_str(msg.data)
                        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                            break

                async def vnc_to_user():
                    async for msg in upstream:
                        if msg.type == WSMsgType.BINARY:
                            await front.send_bytes(msg.data)
                        elif msg.type == WSMsgType.TEXT:
                            await front.send_str(msg.data)
                        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                            break
                tasks = [asyncio.create_task(user_to_vnc()),
                         asyncio.create_task(vnc_to_user())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass
        finally:
            await front.close()
        return front


def filtered_cookies(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        domain = str(c.get("domain", "")).lstrip(".").lower()
        name, value, path = c.get("name"), c.get("value"), c.get("path", "/")
        if (domain == "ucilnica-fe.uni-lj.si" and isinstance(name, str)
                and name.isascii() and name.replace("-", "").replace("_", "").isalnum()
                and isinstance(value, str) and isinstance(path, str)
                and path.startswith("/") and len(value) <= 8192):
            out.append({"domain": domain, "name": name, "value": value, "path": path})
    if not any(c["name"].lower().startswith("moodlesession") for c in out):
        return []
    return out


async def create_app(base_url=None, state=None, fe_root=None, secret=None):
    base_url = private_base_url(base_url or os.getenv("FE_LOGIN_BASE_URL", ""))
    state, fe_root, secret = Path(state or STATE), Path(fe_root or FE_ROOT), Path(secret or COOKIE)
    access = load_or_make_access(state)
    write_launcher(fe_root, base_url, access)
    portal = Portal(state, secret, fe_root, base_url, access)
    portal.client = ClientSession(timeout=ClientTimeout(total=40))
    app = web.Application(client_max_size=8192)
    app["portal"] = portal
    app.router.add_get("/connect", lambda req: security_headers(
        web.Response(text=portal_html(), content_type="text/html")))
    app.router.add_post("/api/begin", portal.begin)
    app.router.add_get("/api/status", portal.status)
    app.router.add_get("/viewer/websockify", portal.websocket)
    app.router.add_get("/viewer/{tail:.*}", portal.viewer)
    async def cleanup(_app):
        await portal.close_browser()
        await portal.client.close()
    app.on_cleanup.append(cleanup)
    return app


def main():
    from aiohttp import web as aio_web
    base = private_base_url(os.environ["FE_LOGIN_BASE_URL"])
    # run_app takes the async factory and starts it in its own event loop.
    # Port mapping on the TrueNAS host MUST target its private Tailscale IPv4.
    aio_web.run_app(create_app(base_url=base), host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
