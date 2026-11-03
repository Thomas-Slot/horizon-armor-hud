#!/usr/bin/env python3
"""Check and package a PINNED native mod with the tools of a horizon-mods clone.

Horizon's own mods (native/<name>/ in horizon-mods) name the hook-point version they need ("hooks"), and
native.py reads that key everywhere. A mod that reads the game's own state pins one Horizon commit instead
and has no "hooks". This script runs the same steps for it, using the clone's native.py unchanged:

    python3 tools/pinned.py check                   # apply on the mod's pinned Horizon, cargo check the game
    python3 tools/pinned.py check --horizon latest  # ... on the newest Nightly (does the mod still compile?)
    python3 tools/pinned.py latest                  # print the newest Nightly's full commit
    python3 tools/pinned.py repin latest            # move the pin in compatibility.json and horizon-mixin.toml
    python3 tools/pinned.py package                 # target/<id>-<version>.hnmod, the file the registry takes

The horizon-mods clone is --clone PATH, $HORIZON_MODS, or ~/dev/horizon-mods. Needs Linux or WSL (rsync).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import zipfile
from pathlib import Path

PIN_JSON = re.compile(r'("horizon_revision"\s*:\s*")[0-9a-f]{40}(")')
PIN_TOML = re.compile(r'(horizon_revision\s*=\s*")[0-9a-f]{40}(")')


def load_native(clone):
    path = Path(clone).expanduser().resolve() / "native" / "tools" / "native.py"
    if not path.is_file():
        sys.exit(f"no {path}: pass --clone PATH to a clone of horizon-rust/horizon-mods")
    spec = importlib.util.spec_from_file_location("horizon_native", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # not run as __main__, so its main() stays idle
    return module


def resolve(n, rev):
    """A full commit: `latest` is the newest Nightly, a tag or short hash is looked up on GitHub."""
    if rev == "latest":
        return n.latest_nightly()
    if re.fullmatch(r"[0-9a-f]{40}", rev):
        return rev
    return n.get_commit(rev)


def check(n, m, horizon):
    revision = resolve(n, horizon) if horizon else m.compat["horizon_revision"]
    # native.py's stage step compares each mod's "hooks" with the game's NATIVE_HOOKS_API.
    # A pinned mod needs no hook points, so 0 passes whenever the game has them at all.
    m.compat.setdefault("hooks", 0)
    n.log(f"{m.id} {m.version} on Horizon {revision[:7]}")
    out = n.stage([m], revision)
    n.cargo(out, "check")
    print(json.dumps({"mods": [m.id], "horizon": revision, "check": "ok"}))


def repin(n, mod_dir, m, revision):
    new = resolve(n, revision)
    for path, pattern in ((mod_dir / "compatibility.json", PIN_JSON), (m.bundle / "horizon-mixin.toml", PIN_TOML)):
        text = path.read_text()
        updated, count = pattern.subn(rf"\g<1>{new}\g<2>", text)
        if count != 1:
            sys.exit(f"{path}: expected one horizon_revision, found {count}")
        path.write_text(updated)
    print(f"pinned {m.id} to Horizon {new}")


def package(m, out_dir):
    """The same .hnmod native.py package writes, without a "hooks" entry unless compatibility.json has one."""
    compat = m.compat
    pinned = m.manifest.get("compatibility", {}).get("horizon_revision")
    if pinned != compat["horizon_revision"]:
        sys.exit(f"{m.id}: horizon-mixin.toml pins {pinned}, compatibility.json {compat['horizon_revision']}")
    manifest = {"schema": 1, "id": m.id, "version": m.version, "game": compat["game"],
                "horizon": compat["horizon_revision"], "mixins": compat["horizon_mixins_revision"]}
    if "hooks" in compat:
        manifest["hooks"] = compat["hooks"]
    files = {"horizon-native.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()}
    for path in sorted(m.bundle.iterdir()):
        if path.suffix in (".rs", ".toml"):
            files[f"mixins/{m.id}/{path.name}"] = path.read_bytes()
    out = out_dir / f"{m.id}-{m.version}.hnmod"
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    print(json.dumps({"file": str(out), "size": out.stat().st_size,
                      "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["check", "package", "repin", "latest"])
    parser.add_argument("revision", nargs="?", help="repin: a commit, tag or `latest`")
    parser.add_argument("--horizon", help="check: a commit, tag or `latest` instead of the mod's own pin")
    parser.add_argument("--clone", default=os.environ.get("HORIZON_MODS", "~/dev/horizon-mods"),
                        help="your clone of horizon-rust/horizon-mods")
    parser.add_argument("--mod", default=str(Path(__file__).resolve().parents[1]), help="the mod's folder")
    args = parser.parse_args(argv)
    n = load_native(args.clone)
    if args.action == "latest":
        print(n.latest_nightly())
        return
    mod_dir = Path(args.mod).expanduser().resolve()
    m = n.Mod(str(mod_dir))
    if args.action == "check":
        check(n, m, args.horizon)
    elif args.action == "repin":
        if not args.revision:
            sys.exit("repin needs a commit, a tag or `latest`")
        repin(n, mod_dir, m, args.revision)
    else:
        package(m, mod_dir / "target")


if __name__ == "__main__":
    main()
