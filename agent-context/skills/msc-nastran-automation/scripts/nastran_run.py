# -*- coding: utf-8 -*-
"""DDS template - MSC Nastran case driver (tool key: python, platform venv).

One call per DOE/optimization case, run in the case folder:
  1. template deck -> <job>.bdf with params.json applied (bdf_card_edit.py rules / {{placeholders}})
  2. make sure the deck writes an OP2 (inserts PARAM,POST,-1 after BEGIN BULK if no PARAM,POST)
  3. nastran <job>.bdf scr=yes old=no batch=no [extra keywords]
  4. f06 check + OP2 extraction + images (nastran_op2_extract.py) -> results.json

  python nastran_run.py --deck plate.bdf [--job case] [--rules card_rules.json] [--placeholders]
                        [--nastran "C:/MSC.Software/MSC_Nastran/2024.1/bin/nastran.exe"]
                        [--extra "mem=4gb smp=4"] [--images vm,disp] [--skip-run]
Executable: --nastran, else env DDS_MSC_NASTRAN / DDS_TOOL_MSC_NASTRAN (set by the platform from
agent.toml [tools] msc_nastran), else "nastran" on PATH.
The study adapter copies only the step script into the case folder, so list the helper modules in
"inputs" (scripts/bdf_card_edit.py, scripts/nastran_op2_extract.py) - they are imported from the cwd.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import subprocess
import sys

for _d in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
    if _d not in sys.path:
        sys.path.insert(0, _d)
try:
    import bdf_card_edit  # noqa: E402
    import nastran_op2_extract  # noqa: E402
except ImportError as _e:
    print("[DDS] FAILED: %s - add scripts/bdf_card_edit.py and scripts/nastran_op2_extract.py to the "
          "study 'inputs' so they are copied into the case folder" % _e)
    sys.exit(7)


def ensure_op2(path):
    """Insert PARAM,POST,-1 right after BEGIN BULK when the deck has no PARAM POST (any format)."""
    with io.open(path, "r", encoding="latin-1", newline="") as f:
        lines = f.read().splitlines(True)
    has_post = any(re.match(r"^PARAM\*?[ ,]+\s*POST\b", l.upper()) or
                   (l.upper().startswith("PARAM") and l[8:16].strip().upper() == "POST") for l in lines)
    if has_post:
        return False
    for i, l in enumerate(lines):
        u = l.strip().upper()
        if u.startswith("BEGIN") and "BULK" in u:
            eol = l[len(l.rstrip("\r\n")):] or "\n"
            lines.insert(i + 1, "PARAM,POST,-1" + eol)
            with io.open(path, "w", encoding="latin-1", newline="") as f:
                f.write("".join(lines))
            print("[DDS] inserted PARAM,POST,-1 (OP2 output)")
            return True
    print("[DDS] warning: no BEGIN BULK in main deck - cannot insert PARAM,POST,-1")
    return False


def clean_outputs(job):
    for ext in (".f06", ".f04", ".log", ".op2", ".xdb", ".h5", ".DBALL", ".MASTER"):
        p = job + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError as e:
                print("[DDS] warning: cannot remove %s (%s) - locked by a previous run?" % (p, e))


def run_nastran(exe, job, extra, timeout):
    cmd = [exe, job + ".bdf", "scr=yes", "old=no", "batch=no"] + extra
    print("[DDS] run: %s" % " ".join(cmd))
    try:
        rc = subprocess.call(cmd, timeout=timeout) if timeout else subprocess.call(cmd)
    except OSError as e:
        nastran_op2_extract.fail("cannot start nastran '%s': %s" % (exe, e), 5)
    except subprocess.TimeoutExpired:
        nastran_op2_extract.fail("nastran timeout after %s s" % timeout, 6)
    print("[DDS] nastran exit code %s (completion is judged from the f06)" % rc)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", required=True, help="template deck copied into the case folder (inputs)")
    ap.add_argument("--job", default="case")
    ap.add_argument("--rules", default=None)
    ap.add_argument("--placeholders", action="store_true")
    ap.add_argument("--no-edit", action="store_true")
    ap.add_argument("--free-width", type=int, default=8)
    ap.add_argument("--no-ensure-op2", action="store_true")
    ap.add_argument("--nastran", default=os.environ.get("DDS_MSC_NASTRAN") or
                    os.environ.get("DDS_TOOL_MSC_NASTRAN") or "nastran")
    ap.add_argument("--extra", default="", help="extra nastran keywords, e.g. \"mem=4gb smp=4\"")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--skip-run", action="store_true", help="only re-check / re-extract")
    ap.add_argument("--subcase", type=int, default=None)
    ap.add_argument("--n-modes", type=int, default=6)
    ap.add_argument("--images", default="vm,disp")
    ap.add_argument("--deformed", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--vmin", type=float, default=None)
    ap.add_argument("--vmax", type=float, default=None)
    ap.add_argument("--fields", default="", help="stress,disp -> fields/<name>.npz")
    a = ap.parse_args()

    if os.path.exists("results.json"):
        os.remove("results.json")
    job = a.job
    deck_out = job + ".bdf"
    if not a.skip_run:
        if not os.path.exists(a.deck):
            nastran_op2_extract.fail("template deck not found: %s" % a.deck, 2)
        try:
            with io.open("params.json", "r", encoding="utf-8") as f:
                params = json.load(f)
            if a.no_edit:
                if os.path.abspath(a.deck) != os.path.abspath(deck_out):
                    with io.open(a.deck, "rb") as s, io.open(deck_out, "wb") as d:
                        d.write(s.read())
            else:
                rules = None
                rules_path = a.rules or ("card_rules.json" if os.path.exists("card_rules.json") else None)
                if rules_path:
                    with io.open(rules_path, "r", encoding="utf-8") as f:
                        rules = json.load(f)
                rep = bdf_card_edit.edit_deck(a.deck, deck_out, params, rules, a.placeholders, a.free_width)
                print("[DDS] edits: %s" % json.dumps(rep))
        except (bdf_card_edit.DeckError, ValueError, KeyError, IOError, OSError) as e:
            nastran_op2_extract.fail("deck edit: %s" % e, 2)
        if not a.no_ensure_op2:
            ensure_op2(deck_out)
        clean_outputs(job)
        run_nastran(a.nastran, job, a.extra.split(), a.timeout)

    nastran_op2_extract.run(job, deck_out if os.path.exists(deck_out) else None, None, a.subcase,
                            a.n_modes, a.images, bool(a.deformed), a.scale, a.vmin, a.vmax, True, a.fields)


if __name__ == "__main__":
    main()
