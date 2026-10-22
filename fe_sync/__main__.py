"""CLI entry point: python -m fe_sync once | loop."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
import time
from .sync import run_sync, atomic_json, render_cached
from .status import publish_status

stop=threading.Event()
def halt(_signal,_frame):stop.set()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['once','loop','render','login'])
    options=parser.parse_args()
    signal.signal(signal.SIGTERM,halt)
    signal.signal(signal.SIGINT,halt)
    fe_root=Path(os.environ.get('FE_ROOT','/data/FE'))
    state_dir=Path(os.environ.get('SYNC_STATE_DIR','/state'))
    cookie_file=Path(os.environ.get('FE_MOODLE_SESSION_FILE','/run/secrets/moodle-session-cookies.json'))
    state_dir.mkdir(parents=True,exist_ok=True)
    if options.mode=='login':
        from .login_portal import main as login_main
        return login_main()
    if options.mode=='render':
        try:
            outcome=render_cached(fe_root,state_dir)
            old_status=json.loads((state_dir/'status.json').read_text(encoding='utf-8'))
            publish_status(fe_root,state_dir,old_status)
            print(json.dumps({'status':'ok','navigation':outcome},ensure_ascii=False),flush=True)
            return 0
        except Exception as exc:
            print(json.dumps({'status':'failed','error_type':type(exc).__name__,
                'reason':str(exc)[:220]},ensure_ascii=False),flush=True)
            return 1
    interval=max(300,int(os.environ.get('SYNC_INTERVAL_SECONDS','7200')))
    while not stop.is_set():
        summary=run_sync(fe_root,state_dir/'files.sqlite',cookie_file,
            max_files=int(os.environ.get('SYNC_MAX_FILES','2000')),
            delay=float(os.environ.get('SYNC_DELAY_SECONDS','0.3')))
        atomic_json(state_dir,Path('status.json'),summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
        if options.mode=='once':return 0 if summary['status']=='ok' else 1
        deadline=time.monotonic()+interval
        # Login portal creates sync.request only after verifying real Moodle access.
        # Wake within seconds without running a second concurrent sync process.
        while not stop.is_set():
            flag=state_dir/'sync.request'
            if flag.is_file() and not flag.is_symlink():
                flag.unlink()
                break
            remain=deadline-time.monotonic()
            if remain<=0:
                break
            stop.wait(min(5,remain))
    return 0

if __name__=='__main__':raise SystemExit(main())
