#!/usr/bin/env python3
"""make tidy: show what can be deleted to free disk space, and delete it only after a yes, group by group.

Groups (results are never touched):
  old server images    Docker images named like a server (st-, dy-, ws-) whose folder is no longer in
                       benchmarks/ (renamed or removed servers), with their variants (-nobw, ...);
                       note: an image may belong to another WSEB checkout, so read the list
  unused image layers  Docker's dangling images (left behind by rebuilds)
  build cache          Docker's stored build steps (rebuilt when needed; builds are slower once)
  native copies        .cache/native/ copies whose image is gone or was rebuilt (never used again)
  empty/abandoned      result folders with no file, or marked abandoned (started again from zero)

  python3 tools/tidy.py            list, then ask per group (needs a terminal; else only lists)
  python3 tools/tidy.py --dry-run  only list
  python3 tools/tidy.py --yes      delete every listed group without asking
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_server  # noqa: E402

SERVER_IMAGE = re.compile(r"^(st|dy|ws)-[a-z0-9]+-")


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def server_folders(benchmarks_dir="benchmarks"):
    return {name for root, dirs, files in os.walk(benchmarks_dir) if "Dockerfile" in files
            for name in [os.path.basename(root)]}


def old_server_images(images, folders):
    """Server-named images that are neither a current server nor a variant of one (<server>-<name>)."""
    old = []
    for name in images:
        if not SERVER_IMAGE.match(name) or name in folders:
            continue
        if any(name.startswith(f + "-") for f in folders):
            continue                                          # a variant of a current server
        old.append(name)
    return sorted(old)


def _gb(n):
    return f"{n / 1e9:.1f} GB"


def _parse_size(text):
    m = re.match(r"([0-9.]+)\s*([kKMGT]?B)", text.strip())
    if not m:
        return 0
    return float(m.group(1)) * {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[m.group(2)]


def empty_or_abandoned_results(results_dir="results"):
    found = []
    try:
        names = sorted(os.listdir(results_dir))
    except OSError:
        return found
    for name in names:
        folder = os.path.join(results_dir, name)
        if not os.path.isdir(folder):
            continue
        has_file = any(files for _, _, files in os.walk(folder))
        if not has_file:
            found.append((folder, 0, "empty"))
        elif os.path.exists(os.path.join(folder, ".abandoned")):
            found.append((folder, native_server._size(folder), "abandoned"))
    return found


def remove_result_folder(folder, results_dir="results"):
    root = os.path.realpath(results_dir)
    real = os.path.realpath(folder)
    if os.path.dirname(real) != root:
        raise ValueError(f"not a results folder: {folder}")
    shutil.rmtree(real)


def groups():
    """[(title, [lines], size text, delete function)] for every group that has something."""
    out = []
    images = [l.split("\t") for l in _run(["docker", "images", "--format", "{{.Repository}}\t{{.Size}}"]).splitlines() if "\t" in l]
    sizes = {n: s for n, s in images}
    old = old_server_images([n for n, _ in images], server_folders())
    if old:
        out.append(("old server images (no folder in benchmarks/; may belong to another WSEB checkout)",
                    [f"{n}  ({sizes.get(n, '?')})" for n in old], "shares layers, so less than the sum",
                    lambda: _run(["docker", "rmi"] + old)))
    dangling = _run(["docker", "images", "-q", "-f", "dangling=true"]).split()
    if dangling:
        out.append(("unused image layers (dangling images)", [f"{len(dangling)} image(s)"], "",
                    lambda: _run(["docker", "image", "prune", "-f"])))
    for line in _run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Reclaimable}}"]).splitlines():
        kind, _, reclaim = line.partition("\t")
        if kind == "Build Cache" and _parse_size(reclaim.split(" ")[0]) > 0:
            out.append(("Docker build cache", [reclaim], "", lambda: _run(["docker", "builder", "prune", "-f"])))
    stale = native_server.stale_copies()
    if stale:
        out.append(("native copies that can never be used again",
                    [f"{os.path.basename(f)}  ({_gb(b)}; {why})" for f, b, why in stale], _gb(sum(b for _, b, _ in stale)),
                    lambda: [native_server.remove_copy(f) for f, _, _ in stale]))
    results = empty_or_abandoned_results()
    if results:
        out.append(("empty or abandoned result folders (finished results are never listed)",
                    [f"{f}  ({why}{', ' + _gb(b) if b else ''})" for f, b, why in results],
                    _gb(sum(b for _, b, _ in results)),
                    lambda: [remove_result_folder(f) for f, _, _ in results]))
    return out


def ask(question, tty="/dev/tty"):
    try:
        with open(tty) as t:
            print(question, end=" ", flush=True)
            return t.readline().strip().lower().startswith("y")
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description="Show what can be deleted to free disk space; delete after a yes.")
    ap.add_argument("--dry-run", action="store_true", help="only list")
    ap.add_argument("--yes", action="store_true", help="delete every listed group without asking")
    args = ap.parse_args()
    before = shutil.disk_usage(".").free
    found = groups()
    if not found:
        print(f"Nothing to tidy. Free disk space: {_gb(before)}")
        return
    print(f"Free disk space: {_gb(before)}. Can be deleted (results are never touched):\n")
    interactive = not args.dry_run and not args.yes and os.path.exists("/dev/tty")
    for title, lines, size, delete in found:
        print(f"* {title}" + (f": {size}" if size else ""))
        for line in lines[:30]:
            print(f"    {line}")
        if len(lines) > 30:
            print(f"    ... and {len(lines) - 30} more")
        if args.dry_run:
            continue
        if args.yes or (interactive and ask("  Delete these? [y/N]")):
            delete()
            print("  deleted")
        else:
            print("  kept")
        print()
    if not args.dry_run:
        print(f"Free disk space now: {_gb(shutil.disk_usage('.').free)} (was {_gb(before)})")


if __name__ == "__main__":
    main()
