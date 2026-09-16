"""
Zen profile backup + restore.

The migration pipeline takes another browser as input and writes to a
Zen profile. This module is the parallel two-way path for Zen itself:
export the user's current Zen profile to a single portable
``.zenbackup`` file, restore it into a Zen profile on another machine.

The archive format is a gzip-compressed tar with a small ``manifest.json``
at the root and the profile files under ``profile/``. The manifest
carries a ``format_version`` so future browser2zen releases can evolve
the layout without breaking older readers.

What goes in: user picks via the GUI's category toggles. The four
"safe core" categories (workspaces, browsing, cookies, favicons) cover
what almost everyone wants. The riskier three (passwords, prefs,
extensions) default off because their portability is conditional on
the target machine matching the source's NSS / Zen version / pref
shape.

The exporter snapshots SQLite files + their WAL/SHM siblings into a
tempdir before tarring (same pattern as
``chromium_history_importer.HistoryImporter._snapshot``) so we never
read a half-flushed page cache.

The importer takes a ``.backup.<ts>`` of every existing target file
before overwriting (mirrors ``app/bridge.py:restore_backup``) so the
user can roll back via the existing Backups screen.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Bumped when the archive layout or manifest schema changes. The
# importer refuses any other value so a v1.2 install doesn't try to
# read a future-shaped archive.
ARCHIVE_FORMAT_VERSION = 1


# Category → list of profile-relative paths. SQLite files implicitly
# include their WAL/SHM/journal siblings (handled by the snapshot logic
# below). Directories under ``extensions`` recurse.
CATEGORY_FILES: dict[str, tuple[str, ...]] = {
    "workspaces": (
        "containers.json",
        "zen-sessions.jsonlz4",
        "sessionstore.jsonlz4",
        "sessionstore-backups/recovery.jsonlz4",
    ),
    "browsing": (
        "places.sqlite",
    ),
    "cookies": (
        "cookies.sqlite",
    ),
    "favicons": (
        "favicons.sqlite",
    ),
    "passwords": (
        "key4.db",
        "key3.db",
        "logins.json",
        "signons.txt",
    ),
    "prefs": (
        "prefs.js",
        "user.js",
        "xulstore.json",
    ),
    "extensions": (
        "addons.json",
        "extensions.json",
        "addonStartup.json.lz4",
        "extensions",   # directory; recursed below
    ),
    "mods": (
        # Zen Mods (UI customisations, themes) live in the profile's
        # chrome/ directory: userChrome.css, userContent.css, plus any
        # per-mod subfolders the Zen Mods feature drops in there.
        "chrome",       # directory; recursed below — captures everything
    ),
}

# All categories in canonical order — used as default for "include
# everything in the archive" on the importer.
ALL_CATEGORIES = tuple(CATEGORY_FILES.keys())

DEFAULT_CATEGORIES = ("workspaces", "browsing", "cookies", "favicons", "mods")

# SQLite file suffixes that must travel with the main file.
_SQLITE_SIBLINGS = ("-wal", "-shm", "-journal")

# Prefixes scrubbed out of a restored ``prefs.js``. These carry the SOURCE
# profile's machine/account identity, not user data:
#
#  * ``services.sync.*`` / ``identity.fxaccounts.*`` — the Mozilla account
#    the source was signed into. Restoring them makes the target sign into
#    that same account on next launch, and Sync then *overwrites* the
#    imported workspaces/sessions from the cloud — the profile appears to
#    lose everything and a second restore fights the now-active Sync.
#  * ``toolkit.profiles.*`` / ``browser.profiles.*`` — the unified-profile
#    registration (storeID, names, "created" flags). If these come from
#    another install they can make Zen no longer associate the profile.
#  * ``browser.laterrun.*`` / ``datareporting.dau.*`` / ``nimbus.profileId``
#    / ``toolkit.telemetry.cachedProfileGroupID`` — per-install telemetry
#    identity.
#  * ``extensions.webextensions.uuids`` — the storage UUID map for the
#    source profile; stale entries corrupt extension storage on the target.
_SOURCE_IDENTITY_PREF_PREFIXES = (
    "services.sync.",
    "identity.fxaccounts.",
    "toolkit.profiles.",
    "browser.profiles.",
    "browser.laterrun.",
    "datareporting.dau.",
    "toolkit.telemetry.cachedProfileGroupID",
    "nimbus.profileId",
    "extensions.webextensions.uuids",
)

# ``user_pref("name", value);`` — captures the name so we can prefix-match.
_USER_PREF_RE = re.compile(r'^user_pref\("([^"]+)"')


# --------------------------------------------------------------------- helpers


def _is_sqlite(rel_path: str) -> bool:
    return rel_path.endswith(".sqlite") or rel_path.endswith(".db")


def _profile_display_name(dir_name: str) -> str:
    # Zen profile dirs are "<random>.<human-readable>" (e.g. "o9i57a6u.Default (release)").
    # Strip the prefix so the manifest shows what the user actually recognises.
    return dir_name.split(".", 1)[1] if "." in dir_name else dir_name


def _sqlite_sibling_paths(src: Path) -> list[Path]:
    """Return the WAL/SHM/journal siblings of a SQLite file, if any exist."""
    return [src.with_name(src.name + suffix)
            for suffix in _SQLITE_SIBLINGS
            if src.with_name(src.name + suffix).exists()]


def _archive_member(rel_path: str) -> str:
    """Where this file lives inside the archive."""
    return f"profile/{rel_path}"


def _is_source_identity_pref(name: str) -> bool:
    """Whether a ``user_pref`` name holds the source profile's identity."""
    return name.startswith(_SOURCE_IDENTITY_PREF_PREFIXES)


def _scrub_prefs_text(text: str) -> str:
    """Drop the source profile's account/identity/sync prefs from ``prefs.js``.

    Keeps every genuine user preference; only strips the lines that would
    either re-bind the target to the source's Mozilla account (Sync then
    clobbers the imported data) or tie the target to the source install's
    profile registration.
    """
    kept: list[str] = []
    for line in text.splitlines():
        match = _USER_PREF_RE.match(line.lstrip())
        if match and _is_source_identity_pref(match.group(1)):
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def _identity_pref_lines(text: str) -> list[str]:
    """Extract the identity/registration ``user_pref`` lines from a prefs.js."""
    out: list[str] = []
    for line in text.splitlines():
        match = _USER_PREF_RE.match(line.lstrip())
        if match and _is_source_identity_pref(match.group(1)):
            out.append(line)
    return out


def _merge_prefs_text(source_text: str, target_text: str | None,
                      fallback_store_id: str | None = None) -> str:
    """Merge the source's user prefs over the target's own identity.

    The source profile's account/sync/profile-registration lines are
    dropped; the TARGET profile's own such lines are kept verbatim on top
    so Zen still associates the profile with this install's Profile
    Groups store (a foreign ``toolkit.profiles.storeID`` makes the
    profile vanish from Zen after a restart).

    ``fallback_store_id`` is used when the target has no storeID of its
    own yet — we derive it from the install's ``Profile Groups/`` so the
    restored profile is still registered with the right store.
    """
    scrubbed = _scrub_prefs_text(source_text).rstrip("\n")
    target_identity = _identity_pref_lines(target_text) if target_text else []
    if not any("toolkit.profiles.storeID" in line for line in target_identity):
        if fallback_store_id:
            target_identity.append(
                f'user_pref("toolkit.profiles.storeID", "{fallback_store_id}");'
            )
    if not target_identity:
        return scrubbed + "\n"
    return scrubbed + "\n" + "\n".join(target_identity) + "\n"


def _resolve_target_store_id(target_profile: Path) -> str | None:
    """Find the ``Profile Groups/<storeID>`` this install uses.

    Prefers the store that already lists ``target_profile``; falls back to
    the sole store when there's exactly one. Returns ``None`` when the
    directory is absent or ambiguous.
    """
    groups_dir = target_profile.parent / "Profile Groups"
    if not groups_dir.is_dir():
        return None
    stores = [db for db in sorted(groups_dir.glob("*.sqlite"))
              if not db.name.endswith(("-wal", "-shm"))]
    if not stores:
        return None
    target_name = target_profile.name
    fallback: str | None = None
    for db in stores:
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
            try:
                rows = conn.execute("SELECT path FROM Profiles").fetchall()
            finally:
                conn.close()
        except Exception:
            continue
        paths = {str(r[0]) for r in rows if r and r[0]}
        if any(p == target_name or p.endswith("/" + target_name) for p in paths):
            return db.stem
        fallback = db.stem
    return fallback if len(stores) == 1 else None


def _scrub_logins_json(data: bytes) -> bytes:
    """Drop the source's Mozilla-account credential from ``logins.json``.

    The signed-in Firefox Account lives as a ``chrome://FirefoxAccounts``
    login. Restoring it would silently re-bind the target to the source's
    Sync account (clobbering the imported data); the user can sign in
    fresh. Every real website credential is kept.
    """
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    logins = doc.get("logins")
    if isinstance(logins, list):
        doc["logins"] = [
            entry for entry in logins
            if not (isinstance(entry, dict)
                    and str(entry.get("hostname", "")).startswith("chrome://FirefoxAccounts"))
        ]
    return json.dumps(doc).encode("utf-8")


def _scrub_extensions_json(data: bytes, target_profile: Path) -> bytes:
    """Re-point source-machine absolute paths in ``extensions.json``.

    The addon entries are fine; their ``path``/``rootURI`` point at the
    SOURCE profile directory though, which doesn't exist on the target.
    Rewrite the directory prefix to this target profile's ``extensions``
    dir so the enabled/disabled state still applies (Firefox expects
    absolute paths here).
    """
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data

    target_ext_dir = str(target_profile / "extensions")
    target_quoted = target_ext_dir.replace(" ", "%20")

    def _fix(value: object) -> object:
        if not isinstance(value, str):
            return value
        # Plain absolute path: ``<prefix>/extensions/<file>.xpi``
        marker = "/extensions/"
        idx = value.find(marker)
        if idx != -1 and not value.startswith("jar:"):
            return target_ext_dir + "/" + value[idx + len(marker):]
        # Packed jar rootURI: ``jar:file://<prefix>/extensions/<file>.xpi!/``
        jidx = value.find("/extensions/")
        if value.startswith("jar:") and jidx != -1:
            return "jar:file://" + target_quoted + "/" + value[jidx + len("/extensions/"):]
        # URL-encoded prefix that isn't a jar rootURI.
        eidx = value.find("extensions/")
        if eidx != -1 and ("/" in value[:eidx]):
            return target_quoted + "/" + value[eidx + len("extensions/"):]
        return value

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key in ("path", "rootURI", "resourceURI", "descriptor"):
                    node[key] = _fix(val)
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    try:
        walk(doc)
    except Exception:
        return data
    return json.dumps(doc).encode("utf-8")


# --------------------------------------------------------------------- exporter


class ZenBackupExporter:
    """Bundle a Zen profile into a single ``.zenbackup`` file."""

    def __init__(
        self,
        zen_profile: Path,
        output_path: Path,
        includes: list[str] | None = None,
    ):
        self.zen_profile = Path(zen_profile)
        self.output_path = Path(output_path)
        self.includes = list(includes) if includes is not None else list(DEFAULT_CATEGORIES)
        unknown = [c for c in self.includes if c not in CATEGORY_FILES]
        if unknown:
            raise ValueError(f"Unknown backup categories: {unknown}")
        self._tempdir: Path | None = None

    def export(self) -> dict:
        result: dict = {
            "ok": False,
            "archive_path": str(self.output_path),
            "file_count": 0,
            "bytes_in": 0,
            "bytes_out": 0,
            "errors": [],
        }
        if not self.zen_profile.is_dir():
            result["errors"].append("zen_profile_missing")
            return result
        if not self.includes:
            result["errors"].append("no_categories_selected")
            return result

        self._tempdir = Path(tempfile.mkdtemp(prefix="browser2zen_export_"))
        staged_root = self._tempdir / "profile"
        staged_root.mkdir(parents=True, exist_ok=True)

        try:
            inventory: list[str] = []

            for category in self.includes:
                for rel_path in CATEGORY_FILES[category]:
                    src = self.zen_profile / rel_path
                    if not src.exists():
                        # Files within optional categories may simply not
                        # be there (e.g. prefs.js exists; user.js usually
                        # doesn't). That's not an error.
                        continue
                    if src.is_dir():
                        self._stage_directory(src, staged_root / rel_path, inventory)
                    elif _is_sqlite(rel_path):
                        self._stage_sqlite(src, staged_root / rel_path, inventory)
                    else:
                        self._stage_file(src, staged_root / rel_path, inventory)

            manifest = {
                "format_version": ARCHIVE_FORMAT_VERSION,
                "browser2zen_version": _read_version(),
                "source_profile_name": _profile_display_name(self.zen_profile.name),
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "included": list(self.includes),
                "file_inventory": sorted(inventory),
            }
            (self._tempdir / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )

            # Total bytes staged before compression.
            result["bytes_in"] = sum(
                p.stat().st_size for p in self._tempdir.rglob("*") if p.is_file()
            )

            # Tar.gz the staging dir into the output path.
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(self.output_path, "w:gz", compresslevel=6) as tar:
                tar.add(self._tempdir / "manifest.json", arcname="manifest.json")
                if staged_root.exists():
                    tar.add(staged_root, arcname="profile",
                            filter=_strip_owner)

            result["bytes_out"] = self.output_path.stat().st_size
            result["file_count"] = len(inventory)
            result["ok"] = True
            return result
        except Exception as exc:
            logger.exception("zen export failed")
            result["errors"].append(f"export_failed: {exc}")
            return result
        finally:
            self._cleanup()

    # ---- staging helpers ----

    def _stage_file(self, src: Path, dest: Path, inventory: list) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        inventory.append(str(dest.relative_to(self._tempdir / "profile")))

    def _stage_sqlite(self, src: Path, dest: Path, inventory: list) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        inventory.append(str(dest.relative_to(self._tempdir / "profile")))
        for sibling in _sqlite_sibling_paths(src):
            sib_dest = dest.with_name(sibling.name)
            shutil.copy2(sibling, sib_dest)
            inventory.append(str(sib_dest.relative_to(self._tempdir / "profile")))

    def _stage_directory(self, src: Path, dest: Path, inventory: list) -> None:
        if not src.is_dir():
            return
        for entry in src.rglob("*"):
            if entry.is_file():
                rel = entry.relative_to(src)
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)
                inventory.append(str(target.relative_to(self._tempdir / "profile")))

    def _cleanup(self) -> None:
        if self._tempdir and self._tempdir.exists():
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None


def _strip_owner(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Don't leak the source machine's UID/GID into the archive."""
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


# --------------------------------------------------------------------- importer


class ZenBackupImporter:
    """Restore a ``.zenbackup`` archive into a Zen profile."""

    def __init__(
        self,
        archive_path: Path,
        target_zen_profile: Path,
        includes: list[str] | None = None,
    ):
        self.archive_path = Path(archive_path)
        self.target = Path(target_zen_profile)
        # ``includes`` is a per-category allow-list; if None, restore
        # everything the archive contains.
        self.includes = list(includes) if includes is not None else None

    def preview(self) -> dict:
        """Read the manifest without unpacking. Read-only."""
        result: dict = {"ok": False, "errors": []}
        if not self.archive_path.is_file():
            result["errors"].append("archive_missing")
            return result
        try:
            with tarfile.open(self.archive_path, "r:gz") as tar:
                member = tar.getmember("manifest.json")
                fh = tar.extractfile(member)
                if fh is None:
                    result["errors"].append("manifest_unreadable")
                    return result
                manifest = json.loads(fh.read().decode("utf-8"))
        except (tarfile.TarError, KeyError, json.JSONDecodeError) as exc:
            result["errors"].append(f"unreadable_archive: {exc}")
            return result

        if manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
            result["errors"].append("unsupported_archive_version")
            result["manifest"] = manifest
            return result

        result["ok"] = True
        result["manifest"] = manifest
        result["archive_size"] = self.archive_path.stat().st_size
        return result

    def import_archive(self) -> dict:
        result: dict = {
            "ok": False,
            "restored_files": [],
            "skipped": [],
            "errors": [],
        }

        if not self.archive_path.is_file():
            result["errors"].append("archive_missing")
            return result
        if not self.target.is_dir():
            result["errors"].append("target_profile_missing")
            return result

        preview = self.preview()
        if not preview["ok"]:
            result["errors"].extend(preview["errors"])
            return result
        manifest = preview["manifest"]

        # Resolve which categories to actually restore: the user's
        # opt-in list intersected with what the archive carries. If the
        # caller didn't constrain ``includes``, restore everything in
        # the manifest.
        archive_includes = set(manifest.get("included", []))
        wanted = set(self.includes) if self.includes is not None else archive_includes
        active = wanted & archive_includes
        skipped_categories = wanted - archive_includes
        for cat in skipped_categories:
            result["skipped"].append({"category": cat, "reason": "not_in_archive"})

        if not active:
            result["errors"].append("no_categories_to_restore")
            return result

        active_files: set[str] = set()
        for cat in active:
            cat_files = CATEGORY_FILES.get(cat)
            if cat_files is None:
                # Forward-compat: a newer browser2zen could have written
                # an archive with a category we don't know about. Skip
                # cleanly instead of KeyError-ing.
                result["skipped"].append({"category": cat, "reason": "unknown_category"})
                continue
            for f in cat_files:
                # Both the file itself and any directory prefix are
                # recorded so the per-member filter below can match
                # extension subdirectories like ``extensions/abc/...``.
                active_files.add(f)

        try:
            with tarfile.open(self.archive_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name == "manifest.json":
                        continue
                    if not member.isfile():
                        continue
                    if not member.name.startswith("profile/"):
                        continue
                    rel = member.name[len("profile/"):]
                    if not _matches_active(rel, active_files):
                        continue

                    target_path = self.target / rel
                    self._snapshot_existing(target_path)

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    fh = tar.extractfile(member)
                    if fh is None:
                        result["skipped"].append({"file": rel, "reason": "unreadable"})
                        continue
                    payload = fh.read()
                    # Scrub source-profile identity / machine state that must
                    # never follow a restore: a signed-in source's Sync would
                    # otherwise clobber the imported data on next launch, and
                    # a foreign profile-registration storeID makes Zen drop
                    # the profile after a restart.
                    if rel == "prefs.js":
                        existing = None
                        if target_path.is_file():
                            try:
                                existing = target_path.read_text("utf-8", "replace")
                            except OSError:
                                existing = None
                        payload = _merge_prefs_text(
                            payload.decode("utf-8", "replace"), existing,
                            fallback_store_id=_resolve_target_store_id(self.target),
                        ).encode("utf-8")
                    elif rel == "extensions.json":
                        payload = _scrub_extensions_json(payload, self.target)
                    elif rel == "logins.json":
                        payload = _scrub_logins_json(payload)
                    target_path.write_bytes(payload)
                    result["restored_files"].append(rel)

                    # Drop any stale wal/shm so SQLite re-reads the new
                    # page cache cleanly.
                    if _is_sqlite(rel):
                        for suffix in _SQLITE_SIBLINGS:
                            stale = target_path.with_name(target_path.name + suffix)
                            if stale.is_file() and rel + suffix not in {m.name[len('profile/'):] for m in tar.getmembers()}:
                                try:
                                    stale.unlink()
                                except OSError:
                                    pass
        except tarfile.TarError as exc:
            result["errors"].append(f"unreadable_archive: {exc}")
            return result

        try:
            (self.target / ".browser2zen-restored").write_text(
                json.dumps({
                    "ts": time.time(),
                    "format_version": ARCHIVE_FORMAT_VERSION,
                    "source_profile_name": manifest.get("source_profile_name"),
                    "exported_at": manifest.get("exported_at"),
                }),
                encoding="utf-8",
            )
        except OSError:
            pass

        result["ok"] = True
        return result

    @staticmethod
    def _snapshot_existing(target_path: Path) -> None:
        if not target_path.exists() or target_path.is_dir():
            return
        ts = int(time.time())
        backup = target_path.with_name(f"{target_path.name}.backup.{ts}")
        try:
            shutil.copy2(target_path, backup)
        except OSError as exc:
            logger.warning("could not snapshot %s before overwrite: %s", target_path, exc)


def _matches_active(rel: str, active_files: set[str]) -> bool:
    """Whether ``rel`` (a profile-relative archive path) should be restored.

    The archive may carry SQLite WAL/SHM siblings (``places.sqlite-wal``)
    that aren't enumerated in CATEGORY_FILES verbatim — they get included
    if their parent (``places.sqlite``) is active. Same for files inside
    the ``extensions/`` directory.
    """
    if rel in active_files:
        return True
    # WAL / SHM / journal siblings.
    for suffix in _SQLITE_SIBLINGS:
        if rel.endswith(suffix):
            base = rel[: -len(suffix)]
            if base in active_files:
                return True
    # Recurse-into-directory members.
    for entry in active_files:
        if entry and rel.startswith(entry + "/"):
            return True
    return False


def _read_version() -> str:
    """Best-effort lookup of the current browser2zen version."""
    try:
        from app.__version__ import VERSION  # type: ignore[import-not-found]
        return VERSION
    except Exception:
        return "unknown"
