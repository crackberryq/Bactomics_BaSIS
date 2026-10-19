"""Optional flushed stderr progress; does not change scientific configuration."""
import functools,os,sys,threading,time
from contextlib import contextmanager

def enabled():return os.environ.get('BASIS_VERBOSE','').lower() in {'1','true','yes'}
def emit(message):
    if enabled():print(time.strftime('[%H:%M:%S] ')+str(message),file=sys.stderr,flush=True)

@contextmanager
def verbosity(requested=False):
    """Enable requested progress for this invocation and restore caller state.

    An already enabled BASIS_VERBOSE remains effective without a CLI flag.
    Nested calls restore their own prior value even when an exception escapes.
    """
    prior = os.environ.get("BASIS_VERBOSE")
    try:
        if requested and not enabled():
            os.environ["BASIS_VERBOSE"] = "1"
        yield
    finally:
        if prior is None:
            os.environ.pop("BASIS_VERBOSE", None)
        else:
            os.environ["BASIS_VERBOSE"] = prior

@contextmanager
def stage(label,interval=30):
    if not label or not enabled():
        yield;return
    start=time.monotonic();done=threading.Event()
    emit(label+' — started')
    def heartbeat():
        while not done.wait(interval):emit(f'{label} — still running ({time.monotonic()-start:.0f}s elapsed)')
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        yield
    except BaseException:
        emit(f'{label} — stopped with error ({time.monotonic()-start:.1f}s)');raise
    else:emit(f'{label} — finished ({time.monotonic()-start:.1f}s)')
    finally:done.set();thread.join()

def tracked(label):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args,**kwargs):
            name=label(*args,**kwargs) if callable(label) else label
            with stage(name):return fn(*args,**kwargs)
        return wrapper
    return decorate
