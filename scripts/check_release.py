#!/usr/bin/env python3
"""Bounded checks of public source and distribution bytes; never publishes files."""
import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import tomllib
import zipfile


REQUIRED = (
    "README.md", "LICENSE", "SECURITY.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md", "pyproject.toml", "requirements.lock", "src/retulo/__init__.py",
    "src/retulo/__main__.py", "src/retulo/schemas/task.schema.json",
    "scripts/install_codex.py", "adapters/codex/AGENTS.md", "adapters/codex/SKILL.md",
    ".github/workflows/ci.yml",
    "src/retulo/workflow.py", "src/retulo/planner.py", "src/retulo/recipes.py",
    "src/retulo/workflow_engine.py", "src/retulo/schemas/task-v2.schema.json",
    "src/retulo/schemas/workflow.schema.json", "src/retulo/schemas/recipe.schema.json",
    "scripts/demo_incremental.py", "scripts/report_fixture.py", "docs/WORKFLOWS.md",
    "install.sh", "src/retulo/installation.py", "src/retulo/integrations/codex.py",
    "src/retulo/integrations/codex_restore.py", "src/retulo/integrations/claude.py",
    "src/retulo/resources/codex/SKILL.md", "src/retulo/resources/codex/AGENTS.md",
    "src/retulo/resources/claude/CLAUDE.md",
    "src/retulo/git_backend.py", "src/retulo/git_workspaces.py", "src/retulo/git_snapshot.py",
)
SOURCE_ARCHIVE_REQUIRED = (
    "README.md", "LICENSE", "pyproject.toml", "THIRD_PARTY_NOTICES.md",
    "src/retulo/__init__.py", "src/retulo/schemas/task.schema.json",
    "scripts/install_codex.py", "adapters/codex/AGENTS.md", "adapters/codex/SKILL.md",
    "src/retulo/workflow.py", "src/retulo/planner.py", "src/retulo/recipes.py",
    "src/retulo/workflow_engine.py", "src/retulo/schemas/task-v2.schema.json",
    "src/retulo/schemas/workflow.schema.json", "src/retulo/schemas/recipe.schema.json",
    "scripts/demo_incremental.py", "scripts/report_fixture.py", "docs/WORKFLOWS.md",
    "install.sh", "src/retulo/installation.py", "src/retulo/integrations/codex.py",
    "src/retulo/integrations/codex_restore.py", "src/retulo/integrations/claude.py",
    "src/retulo/resources/codex/SKILL.md", "src/retulo/resources/codex/AGENTS.md",
    "src/retulo/resources/claude/CLAUDE.md",
    "src/retulo/git_backend.py", "src/retulo/git_workspaces.py", "src/retulo/git_snapshot.py",
)
WHEEL_REQUIRED = (
    "retulo/installation.py", "retulo/integrations/codex.py",
    "retulo/integrations/codex_restore.py", "retulo/integrations/claude.py",
    "retulo/resources/codex/SKILL.md", "retulo/resources/codex/AGENTS.md",
    "retulo/resources/claude/CLAUDE.md",
    "retulo/git_backend.py", "retulo/git_workspaces.py", "retulo/git_snapshot.py",
    "retulo/__init__.py", "retulo/schemas/task.schema.json",
    "retulo/workflow.py", "retulo/planner.py", "retulo/recipes.py", "retulo/workflow_engine.py",
    "retulo/schemas/task-v2.schema.json", "retulo/schemas/workflow.schema.json", "retulo/schemas/recipe.schema.json",
)
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
# These are output folders only at the project/distribution root. A dependency
# named "build" can legitimately appear in third_party/licenses/build/LICENSE.
ROOT_GENERATED_DIRS = {"build", "dist"}
PRIVATE_DIRS = {".workspace", ".codex", ".agents", "runtime", "sessions", "hook-evidence",
                "CodexBrain", "AgentWorkspaces", "RetuloWorkspaces"}
PRIVATE_NAMES = {"auth.json", "credentials.json", "credentials.yaml", "credentials.yml",
                 ".env", ".DS_Store"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
MAX_TAR_EXTENSION_DEPTH = 32
OLD_BRANDING = re.compile(r"workspace[_-]engine", re.IGNORECASE)
HOME_PATH = re.compile(r"/(?:Users|home)/([A-Za-z0-9._-]+)/")
PLACEHOLDER_USERS = {"user", "username", "example", "example-user", "your-user"}
TOKEN = re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|"
                   r"github_pat_[A-Za-z0-9_]{35,}|AKIA[0-9A-Z]{16})\b")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")


def finding(path, code, message, line=None):
    result = {"path": str(path), "code": code, "message": message}
    if line is not None:
        result["line"] = line
    return result


def private_name(path):
    name = path.name
    return (name in PRIVATE_NAMES or name.startswith(".env.") or
            name.endswith((".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm", ".jsonl", ".pem", ".p12")))


def inspect_bytes(path, raw):
    """Report categories and locations, never echo possible credentials."""
    results = []
    if len(raw) > MAX_FILE_BYTES:
        return [finding(path, "oversized_file", "File exceeds the bounded text inspection limit")]
    text = raw.decode("utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        if TOKEN.search(line) or PRIVATE_KEY.search(line):
            results.append(finding(path, "credential_pattern", "Possible credential material", number))
        if any(match.group(1) not in PLACEHOLDER_USERS for match in HOME_PATH.finditer(line)):
            results.append(finding(path, "personal_home_path", "User-specific absolute home path", number))
        if str(path) != "docs/MIGRATION.md" and OLD_BRANDING.search(line):
            results.append(finding(path, "legacy_branding", "Unresolved development package/command name", number))
    return results


def check_inventory(files):
    """Validate optional notices against already bounded, public file bytes."""
    inventory_path = "third_party/inventory.json"
    if inventory_path not in files:
        return []
    results = []
    try:
        packages = json.loads(files[inventory_path])
    except (ValueError, UnicodeError):
        return [finding(inventory_path, "inventory_structure", "License inventory must be valid JSON")]
    if not isinstance(packages, list):
        return [finding(inventory_path, "inventory_structure", "License inventory must contain a package list")]
    seen = set()
    for package in packages:
        if (not isinstance(package, dict) or
                any(not isinstance(package.get(key), str) or not package[key].strip()
                    for key in ("name", "version")) or not isinstance(package.get("license_files"), list)):
            results.append(finding(inventory_path, "inventory_structure", "Invalid package license record"))
            continue
        for item in package["license_files"]:
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str) or
                    not isinstance(item.get("sha256"), str) or
                    re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
                results.append(finding(inventory_path, "inventory_structure", "Invalid license path/hash record"))
                continue
            name = item["path"]
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name or
                    any(ord(char) < 32 for char in name) or path.as_posix() != name or
                    len(path.parts) < 3 or path.parts[:2] != ("third_party", "licenses")):
                results.append(finding(inventory_path, "invalid_license_path", "License path must be a safe relative file below third_party/licenses"))
                continue
            if name in seen:
                results.append(finding(name, "duplicate_license_path", "Inventory license paths must be unique"))
            seen.add(name)
            raw = files.get(name)
            if raw is None:
                results.append(finding(name, "missing_license_file", "Inventory license file is absent or not safely readable"))
            elif hashlib.sha256(raw).hexdigest() != item["sha256"]:
                results.append(finding(name, "license_hash_mismatch", "License bytes do not match the inventory SHA-256"))
    return results


def check_source(root):
    root = Path(root)
    results, inspected, inventory_files = [], 0, {}
    if root.is_symlink() or not root.is_dir():
        return [finding(".", "invalid_root", "Source root must be a real directory")], inspected
    for required in REQUIRED:
        if not (root / required).is_file():
            results.append(finding(required, "missing_required", "Required public release file is absent"))
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in list(dirs):
            path = parent / name
            relative = path.relative_to(root)
            if path.is_symlink():
                results.append(finding(relative, "symlink", "Public source must not follow external links"))
                dirs.remove(name)
            elif (name in IGNORED_DIRS or name.endswith(".egg-info") or
                  (parent == root and name in ROOT_GENERATED_DIRS)):
                dirs.remove(name)
            elif name in PRIVATE_DIRS:
                results.append(finding(relative, "private_directory", "Runtime/user data directory must be excluded"))
                dirs.remove(name)
        for name in files:
            path = parent / name
            relative = path.relative_to(root)
            if path.is_symlink():
                results.append(finding(relative, "symlink", "Public source must not follow external links"))
                continue
            if private_name(relative):
                results.append(finding(relative, "private_filename", "Possible private/runtime file must be excluded"))
                continue
            if name.endswith((".pyc", ".pyo")):
                results.append(finding(relative, "compiled_cache", "Compiled caches are not release source"))
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                results.append(finding(relative, "oversized_file", "File exceeds the bounded text inspection limit"))
                continue
            raw = path.read_bytes()
            results.extend(inspect_bytes(relative, raw))
            if relative.as_posix() == "third_party/inventory.json" or relative.parts[:2] == ("third_party", "licenses"):
                inventory_files[relative.as_posix()] = raw
            inspected += 1
    results.extend(check_inventory(inventory_files))
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        if project.get("name") != "retulo" or project.get("scripts", {}).get("retulo") != "retulo.cli:main":
            results.append(finding("pyproject.toml", "package_identity", "Expected retulo package and CLI identity"))
    except (OSError, ValueError, KeyError):
        results.append(finding("pyproject.toml", "invalid_metadata", "Cannot read project metadata"))
    return results, inspected


def _preflight_zip(stream):
    """Bound actual central records before ZipFile creates any ZipInfo objects.

    Use the same stdlib footer reader (including ZIP64/prepended-data handling)
    as ZipFile. The footer's advertised member count is not a safe loop bound.
    """
    end = zipfile._EndRecData(stream)
    if end is None:
        raise ValueError("Missing ZIP end record")
    size = end[zipfile._ECD_SIZE]
    location = end[zipfile._ECD_LOCATION]
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds inspection limits")
    # Patched Python releases locate ZIP64 at its own end record; older
    # releases locate the classic EOCD. Inspect that fixed signature instead
    # of guessing from the Python minor version.
    if end[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
        stream.seek(location)
        signature = stream.read(4)
        if signature == zipfile.stringEndArchive:
            location -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
        elif signature != zipfile.stringEndArchive64:
            raise ValueError("Invalid ZIP64 end record location")
    start = location - size
    if start < 0:
        raise ValueError("Invalid ZIP central directory offset")
    stream.seek(start)
    consumed = count = 0
    while consumed < size:
        header = stream.read(zipfile.sizeCentralDir)
        if len(header) != zipfile.sizeCentralDir or header[:4] != zipfile.stringCentralDir:
            raise ValueError("Invalid ZIP central directory record")
        count += 1
        if count > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Archive exceeds inspection limits")
        lengths = struct.unpack_from("<HHH", header, 28)
        consumed += zipfile.sizeCentralDir + sum(lengths)
        if consumed > size:
            raise ValueError("Truncated ZIP central directory record")
        stream.seek(start + consumed)
    stream.seek(0)


class _BoundedTarInfo(tarfile.TarInfo):
    """Intercept every physical header before stdlib extension parsing."""

    def _proc_member(self, archive):
        archive._release_headers = getattr(archive, "_release_headers", 0) + 1
        archive._release_bytes = getattr(archive, "_release_bytes", 0) + 512 + self._block(self.size)
        depth = getattr(archive, "_release_depth", 0)
        if (self.size < 0 or archive._release_headers > MAX_ARCHIVE_MEMBERS or
                archive._release_bytes > MAX_ARCHIVE_BYTES * 2 or depth >= MAX_TAR_EXTENSION_DEPTH):
            raise ValueError("Archive exceeds inspection limits")
        extension = self.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
                                 tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE)
        if extension and self.size > MAX_FILE_BYTES:
            raise ValueError("TAR extension exceeds inspection limits")
        archive._release_depth = depth + 1
        try:
            return super()._proc_member(archive)
        finally:
            archive._release_depth = depth

    def _proc_sparse(self, *args):
        raise ValueError("Sparse TAR members are not release files")

    _proc_gnusparse_00 = _proc_sparse
    _proc_gnusparse_01 = _proc_sparse
    _proc_gnusparse_10 = _proc_sparse


def _archive_entries(path):
    """Validate member metadata before reading any payload; never extract."""
    # Compression-container metadata (for example gzip optional name/comment
    # fields) is parsed before the first TAR header reaches our TarInfo hook.
    if path.stat().st_size > MAX_ARCHIVE_BYTES * 2:
        raise ValueError("Archive exceeds inspection limits")
    if path.suffix == ".whl" or path.suffix == ".zip":
        with path.open("rb") as stream:
            _preflight_zip(stream)
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Archive exceeds inspection limits")
                for member in members:
                    mode = (member.external_attr >> 16) & 0o170000
                    yield member.filename, member.is_dir(), mode == 0o120000, member.file_size, (
                        b"" if member.is_dir() or mode == 0o120000 or member.file_size > MAX_FILE_BYTES
                        else archive.read(member))
    else:
        with tarfile.open(path, "r:*", tarinfo=_BoundedTarInfo) as archive:
            count = total = metadata = 0
            for member in archive:
                count += 1
                total += member.size
                metadata += sum(len(k) + len(v) for k, v in member.pax_headers.items())
                if (count > MAX_ARCHIVE_MEMBERS or member.size < 0 or total > MAX_ARCHIVE_BYTES or
                        metadata > MAX_ARCHIVE_BYTES):
                    raise ValueError("Archive exceeds inspection limits")
                # We never resolve TAR links; don't retain copied global PAX maps.
                archive.members.clear()
                special = not (member.isdir() or member.isfile())
                raw = b""
                if member.isfile() and member.size <= MAX_FILE_BYTES:
                    stream = archive.extractfile(member)
                    if stream is not None:
                        with stream:
                            raw = stream.read(MAX_FILE_BYTES + 1)
                yield member.name, member.isdir(), special, member.size, raw


def check_archive(path):
    path = Path(path)
    results, entries = [], {}
    try:
        for name, directory, special, size, raw in _archive_entries(path):
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                results.append(finding(name, "unsafe_archive_path", "Unsafe archive member path"))
                continue
            if special:
                results.append(finding(name, "archive_link", "Links/special files must not be distributed"))
                continue
            if directory:
                continue
            if name in entries:
                results.append(finding(name, "duplicate_member", "Duplicate archive member"))
            entries[name] = raw
            if size > MAX_FILE_BYTES:
                results.append(finding(name, "oversized_file", "Archive member exceeds inspection limit"))
    except (OSError, ValueError, EOFError, UnicodeError, struct.error,
            tarfile.TarError, zipfile.BadZipFile) as exc:
        return [finding(path.name, "invalid_archive", str(exc))], 0
    wheel = path.suffix == ".whl"
    paths = list(entries)
    prefix = ""
    if not wheel and paths and all("/" in name for name in paths):
        first = paths[0].split("/", 1)[0]
        if all(name.startswith(first + "/") for name in paths):
            prefix = first + "/"
    normalized = {name.removeprefix(prefix): raw for name, raw in entries.items()}
    results.extend(check_inventory(normalized))
    for name, raw in normalized.items():
        member = PurePosixPath(name)
        if (any(part in PRIVATE_DIRS | IGNORED_DIRS for part in member.parts) or
                (member.parts and member.parts[0] in ROOT_GENERATED_DIRS) or private_name(member)):
            results.append(finding(name, "private_archive_member", "Private/generated member must be excluded"))
        if name.endswith((".pyc", ".pyo")):
            results.append(finding(name, "compiled_cache", "Compiled caches are not release payload"))
        results.extend(inspect_bytes(name, raw))
    required = WHEEL_REQUIRED if wheel else SOURCE_ARCHIVE_REQUIRED
    for name in required:
        if name not in normalized:
            results.append(finding(name, "missing_archive_file", "Required distribution file is absent"))
    if wheel:
        metadata = [raw for name, raw in normalized.items() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or Parser().parsestr(metadata[0].decode()).get("Name") != "retulo":
            results.append(finding(path.name, "package_identity", "Wheel metadata must identify retulo"))
        if not any(name.endswith("/LICENSE") and ".dist-info/" in name for name in normalized):
            results.append(finding(path.name, "missing_license", "Wheel must include its license"))
    return results, len(entries)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args(argv)
    findings, count = check_source(args.root)
    artifacts = list(args.artifact)
    if args.artifacts_dir is not None:
        artifacts += sorted(args.artifacts_dir.glob("*.whl")) + sorted(args.artifacts_dir.glob("*.tar.gz"))
        if not any(p.suffix == ".whl" for p in artifacts) or not any(p.name.endswith(".tar.gz") for p in artifacts):
            findings.append(finding("dist", "missing_distribution", "Expected both a wheel and source distribution"))
    summaries = []
    for artifact in artifacts:
        issues, members = check_archive(artifact)
        findings.extend({"artifact": artifact.name, **item} for item in issues)
        summaries.append({"name": artifact.name, "members": members, "findings": len(issues)})
    print(json.dumps({"ok": not findings, "source_files": count, "artifacts": summaries,
                      "findings": findings,
                      "scope": "Bounded heuristic source/distribution checks; not a security or legal guarantee"},
                     ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
