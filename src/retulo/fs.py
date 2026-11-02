"""Small local-filesystem primitives. No recursive discovery or external actions."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


class EngineError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def safe_path(value, roots, *, exists=False, regular=False):
    raw = Path(value).expanduser().absolute()
    if ".." in raw.parts:
        raise EngineError("unsafe_path", "Parent traversal is not allowed")
    target = raw.resolve(strict=False)
    bases = [Path(root).resolve(strict=False) for root in roots]
    matches = [root for root in bases if target == root or root in target.parents]
    if not matches:
        raise EngineError("outside_scope", "Path is outside configured roots")
    root = max(matches, key=lambda p: len(p.parts))
    # A platform alias above a configured root (/var on macOS, for example)
    # is harmless; symlinks inside the granted boundary are never accepted.
    cursor = raw
    while cursor.resolve(strict=False) != root:
        if cursor.is_symlink():
            raise EngineError("unsafe_path", "Symlinks inside a granted root are not allowed")
        if cursor == cursor.parent:
            raise EngineError("outside_scope", "Cannot resolve path boundary")
        cursor = cursor.parent
    if raw.is_symlink() and target != root:
        raise EngineError("unsafe_path", "Symbolic link is not an artifact")
    if exists and not target.exists():
        raise EngineError("missing_file", "Required path does not exist")
    if regular and target.exists() and not stat.S_ISREG(target.lstat().st_mode):
        raise EngineError("unsafe_path", "Only regular files are supported")
    return target


def file_info(path):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EngineError("unreadable_file", "File cannot be read safely", {"errno": exc.errno}) from exc
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise EngineError("unsafe_path", "Only regular files are supported")
        hashed = hashlib.sha256()
        while data := stream.read(1024 * 1024):
            hashed.update(data)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise EngineError("file_changed", "File changed while being verified")
        return {"sha256": hashed.hexdigest(), "size": after.st_size}


def read_json(path, default=None):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return default
    if path.is_symlink() or not path.is_file():
        raise EngineError("unsafe_state", "State must be a regular file")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise EngineError("state_limit", "State exceeds the local size limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineError("invalid_state", "State is unreadable or malformed") from exc


def sync_directory(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        # Some filesystems cannot fsync directories. Local platform capability
        # remains explicit; a directory fsync is not cross-filesystem atomicity.
        pass


def atomic_bytes(path, data, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise EngineError("unsafe_state", "Refusing to replace a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if len(data) > 8 * 1024 * 1024:
        raise EngineError("state_limit", "State exceeds the local size limit")
    atomic_bytes(path, data)


def publish_copy(source, target, expected_hash):
    """Copy into a same-directory temporary file, then publish without overwrite."""
    source, target = Path(source), Path(target)
    if target.exists() or target.is_symlink():
        if not target.is_symlink() and target.is_file() and file_info(target)["sha256"] == expected_hash:
            return  # A journal retry of the already published exact bytes.
        raise EngineError("publication_conflict", "Publication destination already contains other bytes")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=target.parent)
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(source_fd, "rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            fd = -1
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                raise EngineError("unsafe_path", "Publication source must be regular")
            checksum = hashlib.sha256()
            while chunk := incoming.read(1024 * 1024):
                checksum.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if checksum.hexdigest() != expected_hash:
            raise EngineError("file_changed", "Publication source changed after verification")
        # link(2) is an exclusive publication: existing names are never replaced.
        os.link(temporary, target, follow_symlinks=False)
        sync_directory(target.parent)
    except FileExistsError as exc:
        raise EngineError("publication_conflict", "Publication name was taken concurrently") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def process_lock(path):
    try:
        import fcntl
    except ImportError as exc:
        raise EngineError("unsupported_platform", "This build verifies process locking on macOS/Linux only") from exc
    path = Path(path)
    if path.is_symlink():
        raise EngineError("unsafe_state", "Lock path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
