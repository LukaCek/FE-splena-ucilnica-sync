#!/usr/bin/env python3
"""Temporary private Chromium login controlled from a phone via Tailscale.

Run on TrueNAS with sudo. WebDriver is reachable only on localhost.
Never exposes SSO credentials, cookies or a browser directly to the Internet.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib import error, request

BASE = "https://ucilnica-fe.uni-lj.si"
WEBDRIVER = "http://127.0.0.1:14444"
IMAGE = "selenium/standalone-chromium:4.48.0-20260905"
NAME = "fe-moodle-phone-login"
STATE_DIR = Path("/mnt/tank/apps/fe-moodle-sync/state")
SESSION_FILE = STATE_DIR / "phone-login.json"
COOKIE_FILE = Path("/mnt/tank/apps/fe-moodle-sync/secrets/moodle-session-cookies.json")
TAILNET = ipaddress.IPv4Network("100.64.0.0/10")
PRIVATE_NETS = [ipaddress.IPv4Network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


class LoginError(RuntimeError):
    pass


def docker(*args, capture=False):
    return subprocess.run(["docker", *args], check=True, text=True,
                          capture_output=capture)


def private_ip(value):
    try:
        ip = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise LoginError("Neveljaven IPv4 naslov.") from exc
    if ip not in TAILNET and not any(ip in net for net in PRIVATE_NETS):
        raise LoginError("Dovoljen je samo Tailscale ali zasebni LAN naslov NAS-a.")
    return str(ip)


def tailscale_ip():
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], check=True,
                           capture_output=True, text=True, timeout=5)
        for value in r.stdout.split():
            if ipaddress.IPv4Address(value) in TAILNET:
                return value
    except (OSError, ValueError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired):
        pass
    return None


def webdriver(method, path, payload=None, timeout=25):
    req = request.Request(
        WEBDRIVER + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except (error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise LoginError("Selenium WebDriver ni dosegljiv ali je vrnil napako.") from exc
    val = data.get("value") if isinstance(data, dict) else None
    if isinstance(val, dict) and val.get("error"):
        raise LoginError("Selenium: " + str(val["error"]))
    return data


def stop():
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True,
                   check=False)
    SESSION_FILE.unlink(missing_ok=True)


def start(ip_arg):
    if SESSION_FILE.exists():
        raise LoginError("Seja že obstaja; uporabi 'finish' ali 'stop'.")
    ip = private_ip(ip_arg) if ip_arg else tailscale_ip()
    if not ip:
        raise LoginError(
            "Tailscale IP ni dosegljiv na NAS-u. Če si na istem Wi-Fi kot NAS, "
            "uporabi start --bind-ip 192.168.1.120 (preveri dejanski IP).")
    password = secrets.token_urlsafe(16)
    print("Zaganjam zasebni začasni Chromium ...", flush=True)
    try:
        docker("run", "-d", "--rm", "--name", NAME,
               "--shm-size=1g", "--memory=2g", "--pids-limit=256",
               "--security-opt=no-new-privileges", "--cap-drop=ALL",
               "-e", "SE_VNC_PASSWORD=" + password,
               "-e", "SE_NODE_SESSION_TIMEOUT=1800",
               "-e", "SE_SCREEN_WIDTH=1280",
               "-e", "SE_SCREEN_HEIGHT=900",
               "-p", ip + ":17900:7900",
               "-p", "127.0.0.1:14444:4444", IMAGE, capture=True)
        for _ in range(40):
            try:
                if webdriver("GET", "/status", timeout=3).get("value", {}).get("ready"):
                    break
            except LoginError:
                pass
            time.sleep(2)
        else:
            raise LoginError("Chromium se ni zagnal. Preveri docker logs " + NAME)
        session = webdriver("POST", "/session", {
            "capabilities": {"alwaysMatch": {
                "browserName": "chrome", "pageLoadStrategy": "none"}}}, timeout=75)
        sid = session.get("value", {}).get("sessionId") or session.get("sessionId")
        if not sid:
            raise LoginError("Selenium ni ustvaril seje.")
        webdriver("POST", "/session/" + sid + "/url", {
            "url": BASE + "/my/courses.php"})
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"id": sid, "ip": ip}, f)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        stop()
        raise
    print("\nODPRI NA TELEFONU (Tailscale ali isti Wi-Fi):")
    print("http://" + ip + ":17900/?autoconnect=1&resize=scale")
    print("Geslo za VNC:", password)
    print("Ko v Chromiumu vidiš predmete e.FE, v TrueNAS Shell zaženi:")
    print("sudo python3 /tmp/nas_phone_login.py finish")
    print("Gesla in zaslona s prijavo ne deli z nikomer.")


def get_moodle_cookies(sid):
    webdriver("POST", "/session/" + sid + "/url", {
        "url": BASE + "/my/courses.php"})
    time.sleep(3)
    url = webdriver("GET", "/session/" + sid + "/url").get("value", "")
    if not isinstance(url, str) or not url.startswith(BASE + "/") or "/login/" in url:
        raise LoginError("Prijava UL SSO še ni zaključena. Najprej odpri predmete.")
    raw = webdriver("GET", "/session/" + sid + "/cookie").get("value")
    if not isinstance(raw, list):
        raise LoginError("Piškotkov ni bilo mogoče prebrati.")
    cookies = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        domain = str(c.get("domain", "")).lstrip(".").lower()
        name, value, path = c.get("name"), c.get("value"), c.get("path", "/")
        if (domain == "ucilnica-fe.uni-lj.si"
                and isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name)
                and isinstance(value, str)
                and isinstance(path, str) and path.startswith("/")):
            cookies.append({"domain": domain, "name": name,
                            "value": value, "path": path})
    if not any(c["name"].lower().startswith("moodlesession") for c in cookies):
        raise LoginError("Ni veljavnega MoodleSession piškotka. Ponovi prijavo.")
    return cookies


def verify_cookies(cookies):
    cookie_header = "; ".join(c["name"] + "=" + c["value"] for c in cookies)
    req = request.Request(BASE + "/my/courses.php", headers={
        "User-Agent": "Mozilla/5.0", "Cookie": cookie_header})
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    try:
        with request.build_opener(NoRedirect).open(req, timeout=15) as res:
            if res.status != 200:
                raise LoginError("e.FE ne potrdi prijave.")
            html = res.read(1000000).decode("utf-8", "replace")
            if '/login/index.php' in html and 'name="username"' in html:
                raise LoginError("e.FE še zahteva prijavo.")
    except error.HTTPError as exc:
        raise LoginError("Prijava zavrnjena: HTTP " + str(exc.code)) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise LoginError("Preverjanje e.FE povezave ni uspelo.") from exc


def finish():
    if not SESSION_FILE.is_file():
        raise LoginError("Začasnega brskalnika ni; najprej 'start'.")
    sid = json.loads(SESSION_FILE.read_text(encoding="utf-8"))["id"]
    cookies = get_moodle_cookies(sid)
    verify_cookies(cookies)
    if not COOKIE_FILE.is_file() or COOKIE_FILE.is_symlink():
        raise LoginError("Obstoječa datoteka piškotkov ne obstaja; ničesar ne spreminjam.")
    # Keep inode: Docker read-only bind mount references the existing file inode.
    fd = os.open(COOKIE_FILE, os.O_WRONLY | os.O_TRUNC)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"site": BASE, "cookies": cookies}, f)
        f.flush()
        os.fsync(f.fileno())
    print("Obstoječa Moodle seja na NAS-u je posodobljena.")
    names = docker("ps", "--filter", "name=fe-moodle-sync",
                   "--format", "{{.Names}}", capture=True).stdout.splitlines()
    names = [n for n in names if "fe-moodle-sync" in n]
    if len(names) != 1:
        raise LoginError("Piškotki so shranjeni, toda ne najdem Moodle Sync vsebnika.")
    docker("restart", names[0], capture=True)
    cmd = ("from fe_sync.client import MoodleClient;"
           " c=MoodleClient('/run/secrets/moodle-session-cookies.json');"
           " print('Prijava uspešna:',len(c.courses()),'predmetov')")
    check = subprocess.run(["docker", "exec", names[0], "python", "-c", cmd],
                           capture_output=True, text=True, timeout=100)
    if check.returncode != 0:
        raise LoginError("Nova seja je shranjena, vendar je Moodle Sync ni potrdil. "
                         "Brskalnik ostaja odprt.")
    print(check.stdout.strip())
    stop()
    print("Brskalnik odstranjen; sinhronizacija uporablja novo sejo.")


def main():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="mode", required=True)
    sub = subs.add_parser("start")
    sub.add_argument("--bind-ip", help="Tailscale ali zasebni LAN IP NAS-a")
    subs.add_parser("finish")
    subs.add_parser("stop")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Zaženi s sudo na TrueNAS.")
    try:
        if args.mode == "start":
            start(args.bind_ip)
        elif args.mode == "finish":
            finish()
        else:
            stop()
            print("Začasni brskalnik je odstranjen.")
    except subprocess.CalledProcessError:
        print("NAPAKA: ukaz Docker ni uspel; preveri 'docker logs " + NAME + "'.",
              file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("NAPAKA: preverjanje traja predolgo.", file=sys.stderr)
        return 1
    except LoginError as exc:
        print("NAPAKA:", str(exc)[:200], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
