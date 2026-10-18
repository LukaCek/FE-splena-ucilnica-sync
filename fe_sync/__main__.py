"""CLI entry point: python -m fe_sync once | loop."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
from .sync import run_sync, atomic_json

stop=threading.Event()
def halt(_signal,_frame):stop.set()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['once','loop'])
    options=parser.parse_args()
    signal.signal(signal.SIGTERM,halt)
    signal.signal(signal.SIGINT,halt)
    fe_root=Path(os.environ.get('FE_ROOT','/data/FE'))
    state_dir=Path(os.environ.get('SYNC_STATE_DIR','/state'))
    cookie_file=Path(os.environ.get('FE_MOODLE_SESSION_FILE','/run/secrets/moodle-session-cookies.json'))
    state_dir.mkdir(parents=True,exist_ok=True)
    interval=max(300,int(os.environ.get('SYNC_INTERVAL_SECONDS','7200')))
    while not stop.is_set():
        summary=run_sync(fe_root,state_dir/'files.sqlite',cookie_file,
            max_files=int(os.environ.get('SYNC_MAX_FILES','2000')),
            delay=float(os.environ.get('SYNC_DELAY_SECONDS','0.3')))
        atomic_json(state_dir,Path('status.json'),summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
        if options.mode=='once':return 0 if summary['status']=='ok' else 1
        stop.wait(interval)
    return 0

if __name__=='__main__':raise SystemExit(main())
