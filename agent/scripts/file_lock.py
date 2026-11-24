"""Portable advisory locks, keeping the existing flock call sites explicit."""
import portalocker

LOCK_EX = portalocker.LOCK_EX
LOCK_NB = portalocker.LOCK_NB
LOCK_UN = portalocker.LOCK_UN

def flock(file, flags):
    try:
        if flags == LOCK_UN:
            portalocker.unlock(file)
        else:
            portalocker.lock(file, flags)
    except portalocker.exceptions.LockException as exc:
        raise BlockingIOError('Another process owns this lock.') from exc
