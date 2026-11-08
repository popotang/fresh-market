"""Single-host reentrant writer exclusion, shared by legacy and CLI pipelines."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import threading

ROOT=Path(__file__).resolve().parents[1]
_guard=threading.RLock()
_owned={}


class WriterBusy(RuntimeError): pass


def acquire_writer(path=None):
    path=Path(path or ROOT/'.kemai-writer.lock').resolve()
    identity=(os.getpid(),threading.get_ident(),str(path))
    with _guard:
        if identity in _owned:
            fd,count=_owned[identity];_owned[identity]=(fd,count+1)
        else:
            fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:
                os.close(fd);raise WriterBusy('skipped_busy: another Kemai writer owns the shared lock') from error
            _owned[identity]=(fd,1)
    released=False
    def release():
        nonlocal released
        with _guard:
            if released:return
            released=True
            fd,count=_owned[identity]
            if count==1:
                del _owned[identity];os.close(fd)
            else:_owned[identity]=(fd,count-1)
    return release


@contextmanager
def writer_lock(path=None):
    release=acquire_writer(path)
    try:yield
    finally:release()
