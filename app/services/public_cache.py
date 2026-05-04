from threading import Lock

_public_resorts_version = 1
_lock = Lock()


def get_public_resorts_version() -> int:
    return _public_resorts_version


def bump_public_resorts_version() -> int:
    global _public_resorts_version
    with _lock:
        _public_resorts_version += 1
        return _public_resorts_version
