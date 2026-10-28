#!/usr/bin/env python3
"""Phone notes (iCloud Drive) -> data_dir/health-context.txt, a real tracked file.

The phone Shortcut can only write to iCloud, so iCloud stays the inbox. But a file that only
lives there has no history: the ledger honours context only if it was recorded before a
prediction resolved, and that can't be checked without a dated record of when each line
appeared. Copying into the data repo on every sync makes git that record.

Lines that vanished or changed on the phone since the last sync are printed, not blocked -
fixing a typo is legitimate, and the previous wording survives in git either way.
"""
import os

from estebot import config

_cfg = config.load()


def main():
    src = _cfg.newest_export("notes")
    dst = _cfg.data_file("health-context.txt")

    # The data repo used to hold a symlink to the iCloud file. Writing through it would
    # overwrite the source with itself and leave git tracking only the link.
    if dst.is_symlink():
        dst.unlink()
        print(f"   replaced symlink {dst.name} with a tracked copy")

    old = dst.read_text().splitlines() if dst.exists() else []
    text = src.read_text()
    new = text.splitlines()

    added = [l for l in new if l.strip() and l not in set(old)]
    gone = [l for l in old if l.strip() and l not in set(new)]

    dst.write_text(text)
    print(f"health-context.txt: {len(new)} lines, {len(added)} new since last sync")
    for l in added:
        print(f"   + {l}")
    if gone:
        print(f"   ! {len(gone)} line(s) edited or removed on the phone (old text kept in git):")
        for l in gone:
            print(f"   - {l}")


if __name__ == "__main__":
    main()
