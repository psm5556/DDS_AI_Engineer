# -*- coding: utf-8 -*-
"""DDS template - OptiStruct case driver (tool key: python, platform venv).

One script per DOE/optimization case:
  1. (optional) HyperMesh batch pre-step: hmbatch -tcl hm_update_export.tcl  -> model.fem
  2. parameter change directly in the .fem text (card-field edit or {{placeholder}} in free-field lines)
  3. run OptiStruct:  optistruct case.fem -nt N
  4. check completion in case.out, extract mass from .out and max disp / von Mises / SPC force from .op2
     (pyNastran) -> results.json ; images are produced by a separate HyperView step (hv_contour_png.tcl).

Run examples:
  python os_case_driver.py --base-fem plate.fem --optistruct "C:/Altair/2024/hwsolvers/scripts/optistruct.bat" --nt 4
  python os_case_driver.py --base-fem plate.fem --skip-run          # only re-extract existing results
  python os_case_driver.py --hmbatch ".../hmbatch.exe" --hm-script hm_update_export.tcl --base-fem model.fem
Executables may also come from env DDS_ALTAIR_OPTISTRUCT / DDS_ALTAIR_HMBATCH.

Requires (for .op2): pip install pyNastran   (deck must request OP2 output, see references/optistruct.md)
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------------------------
# Card-field edit map: params.json key -> rule
#   card  : Bulk card name (PSHELL, MAT1, FORCE, PLOAD4, ...)
#   match : {field_no: value}  fields used to pick the card (field 1 = card name, field 2 = ID ...)
#   field : field number to change (must be on the first line of the card)
#   mode  : "set" (write value) or "scale" (multiply the existing value)
# Nastran fields: PSHELL PID MID1 T ...      -> T     = field 4
#                 MAT1   MID E G NU RHO ...  -> E     = field 3, NU = field 5, RHO = field 6
#                 FORCE  SID G CID F N1 N2 N3 -> F    = field 5
#                 PLOAD4 SID EID P1 ...       -> P1   = field 4
CARD_RULES = {
    "thickness": {"card": "PSHELL", "match": {2: 1}, "field": 4, "mode": "set"},
    "e_mod": {"card": "MAT1", "match": {2: 1}, "field": 3, "mode": "set"},
    "load_scale": {"card": "FORCE", "match": {2: 1}, "field": 5, "mode": "scale"},
}
# results keys read from the OP2 (subcase None = first subcase found)
SUBCASE = None


# --------------------------------------------------------------------------- Nastran formatting
def nastran_real(value, width):
    """Format a float into at most `width` chars with a decimal point (Nastran real field)."""
    value = float(value)
    if value == 0.0:
        return "0."
    for prec in range(width, 0, -1):
        s = ("%." + str(prec) + "g") % value
        if "e" in s:
            mant, exp = s.split("e")
            exp = int(exp)
            if "." not in mant:
                mant += "."
            s = "%sE%d" % (mant, exp)          # e.g. 1.5E-5 (valid Nastran real)
        elif "." not in s:
            s += "."
        if len(s) <= width:
            return s
    raise ValueError("cannot format %r into %d chars" % (value, width))


def parse_real(text):
    """Parse Nastran real incl. implicit exponent forms like 1.5-3, 2.+5."""
    t = text.strip().upper().replace("D", "E")
    if not t:
        return 0.0
    m = re.match(r"^([+-]?[0-9]*\.?[0-9]*)([+-][0-9]+)$", t)
    if m and "E" not in t:
        t = m.group(1) + "E" + m.group(2)
    return float(t)


def split_fields(line):
    """Return (fmt, fields, rest) for the first line of a card.
    fields[0] is field 1 (card name); rest = columns 73-80 (continuation marker), kept untouched."""
    body = line.rstrip("\r\n")
    if "\t" in body:
        raise ValueError("TAB character in card line - cannot edit fixed fields safely: %r" % body)
    if "," in body:
        return "free", body.split(","), ""
    name = body[:8].strip()
    if name.endswith("*"):
        fields = [body[:8]] + [body[8 + 16 * i: 24 + 16 * i] for i in range(4)]
        return "large", fields, body[72:]
    fields = [body[8 * i: 8 * i + 8] for i in range(9)]
    return "small", fields, body[72:]


def join_fields(fmt, fields, rest, tail):
    if fmt == "free":
        return ",".join(fields) + tail
    w = 16 if fmt == "large" else 8
    out = fields[0].ljust(8) + "".join(f.rjust(w) if f.strip() else f.ljust(w) for f in fields[1:])
    if rest.strip():
        return out + rest + tail
    return out.rstrip() + tail


def card_name(line):
    s = line.lstrip()
    if not s or s.startswith("$"):
        return None
    if "," in s:
        return s.split(",")[0].strip().upper().rstrip("*")
    return s[:8].strip().upper().rstrip("*")


def apply_card_rules(lines, params):
    changed = {}
    for key, rule in CARD_RULES.items():
        if key not in params:
            continue
        hits = 0
        for i, line in enumerate(lines):
            if card_name(line) != rule["card"]:
                continue
            fmt, fields, rest = split_fields(line)
            ok = True
            for fno, val in rule["match"].items():
                if fno - 1 >= len(fields) or fields[fno - 1].strip() != str(val):
                    ok = False
            if not ok:
                continue
            fidx = rule["field"] - 1
            if fidx >= len(fields):
                raise ValueError("field %d of %s is not on the first line (%s format)"
                                 % (rule["field"], rule["card"], fmt))
            new_val = float(params[key])
            if rule["mode"] == "scale":
                new_val = parse_real(fields[fidx]) * new_val
            width = {"small": 8, "large": 16, "free": 16}[fmt]
            fields[fidx] = nastran_real(new_val, width)
            tail = line[len(line.rstrip("\r\n")):]
            lines[i] = join_fields(fmt, fields, rest, tail)
            hits += 1
        if hits == 0:
            raise ValueError("no %s card matched rule for '%s'" % (rule["card"], key))
        changed[key] = hits
    return changed


def apply_placeholders(text, params):
    """Replace {{key}} only in free-field (comma) lines so fixed columns are never shifted."""
    out = []
    for line in text.splitlines(True):
        if "{{" in line:
            if "," not in line:
                raise ValueError("placeholder in fixed-field line (use free-field format): %r" % line)
            for k, v in params.items():
                line = line.replace("{{%s}}" % k, nastran_real(v, 16))
            if "{{" in line:
                raise ValueError("unresolved placeholder: %r" % line)
        out.append(line)
    return "".join(out)


# --------------------------------------------------------------------------- results
def check_out(out_path):
    if not os.path.exists(out_path):
        return False, "no .out file"
    with open(out_path, "r", errors="replace") as f:
        txt = f.read()
    # TODO(사내검증): completion banner text per OptiStruct version
    if re.search(r"ANALYSIS COMPLETED", txt, re.I) is None and \
            re.search(r"OPTIMIZATION HAS CONVERGED", txt, re.I) is None:
        return False, "completion banner not found"
    if re.search(r"\*\*\*\s*ERROR", txt):
        return False, "*** ERROR in .out"
    return True, txt


def mass_from_out(txt):
    # Iteration summary prints "Mass = 1.234E+00" (TODO(사내검증) wording; MASSPROP output gives details)
    vals = re.findall(r"\bMass\s*=\s*([-+]?[0-9.]+(?:[EeDd][-+]?[0-9]+)?)", txt)
    return float(vals[-1].replace("D", "E")) if vals else None


def _stress_tables(op2):
    """Yield (name, dict) of stress result dicts for pyNastran old/new layouts."""
    holder = getattr(getattr(op2, "op2_results", None), "stress", None)
    names = [n for n in dir(holder or op2) if n.endswith("_stress") and not n.startswith("_")]
    src = holder if holder is not None else op2
    for n in names:
        d = getattr(src, n, None)
        if isinstance(d, dict) and d:
            yield n, d


def results_from_op2(op2_path):
    from pyNastran.op2.op2 import read_op2
    op2 = read_op2(op2_path, build_dataframe=False, debug=False)
    res = {}
    if op2.displacements:
        sc = SUBCASE if SUBCASE in op2.displacements else sorted(op2.displacements)[0]
        d = op2.displacements[sc].data[-1, :, :3]
        res["max_disp"] = max(math.sqrt(x * x + y * y + z * z) for x, y, z in d)
    vm_max = None
    for name, tables in _stress_tables(op2):
        sc = SUBCASE if SUBCASE in tables else sorted(tables)[0]
        obj = tables[sc]
        headers = list(obj.get_headers() if hasattr(obj, "get_headers") else getattr(obj, "headers", []))
        if "von_mises" not in headers:
            print("[DDS] %s has no von_mises column (headers=%s)" % (name, headers))
            continue
        col = headers.index("von_mises")
        m = float(obj.data[-1, :, col].max())
        vm_max = m if vm_max is None else max(vm_max, m)
    if vm_max is not None:
        res["max_mises"] = vm_max
    if op2.spc_forces:
        sc = SUBCASE if SUBCASE in op2.spc_forces else sorted(op2.spc_forces)[0]
        f = op2.spc_forces[sc].data[-1, :, :3]
        res["reaction_fx"] = float(f[:, 0].sum())
        res["reaction_fy"] = float(f[:, 1].sum())
        res["reaction_fz"] = float(f[:, 2].sum())
    return res


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-fem", required=True, help="template/base deck (copied into the case dir by inputs)")
    ap.add_argument("--case-name", default="case", help="output deck name -> case.fem, case.out, case.op2")
    ap.add_argument("--mode", choices=["cards", "placeholder", "none"], default="cards")
    ap.add_argument("--optistruct", default=os.environ.get("DDS_ALTAIR_OPTISTRUCT"))
    ap.add_argument("--nt", type=int, default=4)
    ap.add_argument("--extra", default="", help="extra OptiStruct options, e.g. \"-core in\"")
    ap.add_argument("--hmbatch", default=os.environ.get("DDS_ALTAIR_HMBATCH"))
    ap.add_argument("--hm-script", default=None, help="HyperMesh Tcl pre-step writing --base-fem")
    ap.add_argument("--skip-run", action="store_true")
    args = ap.parse_args()

    case_dir = os.getcwd()
    res_path = os.path.join(case_dir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    with open(os.path.join(case_dir, "params.json"), "r", encoding="utf-8") as f:
        params = json.load(f)

    # 1. optional HyperMesh pre-step
    if args.hm_script:
        if not args.hmbatch:
            raise SystemExit("--hm-script given but no hmbatch executable")
        env = dict(os.environ, DDS_HM_STANDALONE="0")
        rc = subprocess.call([args.hmbatch, "-tcl", os.path.abspath(args.hm_script)], cwd=case_dir, env=env)
        print("[DDS] hmbatch exit code %s" % rc)
        if os.path.exists(res_path):
            os.remove(res_path)
        if not os.path.exists(args.base_fem):
            raise SystemExit("HyperMesh step did not produce %s" % args.base_fem)

    fem = os.path.join(case_dir, args.case_name + ".fem")
    out = os.path.join(case_dir, args.case_name + ".out")
    op2 = os.path.join(case_dir, args.case_name + ".op2")

    # 2. deck parameter change
    if not args.skip_run:
        with open(args.base_fem, "r", errors="strict") as f:
            text = f.read()
        if "INCLUDE" in text.upper():
            print("[DDS] warning: deck has INCLUDE files - only the main file is edited")
        if args.mode == "cards":
            lines = text.splitlines(True)
            changed = apply_card_rules(lines, params)
            text = "".join(lines)
            print("[DDS] card edits: %s" % changed)
        elif args.mode == "placeholder":
            text = apply_placeholders(text, params)
        with open(fem, "w") as f:
            f.write(text)

        # 3. run solver
        if not args.optistruct:
            raise SystemExit("OptiStruct executable not given (--optistruct or env DDS_ALTAIR_OPTISTRUCT)")
        for p in (out, op2):
            if os.path.exists(p):
                os.remove(p)
        cmd = [args.optistruct, os.path.basename(fem), "-nt", str(args.nt)] + args.extra.split()
        print("[DDS] run: %s" % " ".join(cmd))
        rc = subprocess.call(cmd, cwd=case_dir)
        print("[DDS] optistruct exit code %s" % rc)

    # 4. completion + extraction
    ok, txt = check_out(out)
    if not ok:
        print("[DDS] solver failed: %s (see %s, %s.stat)" % (txt, out, args.case_name))
        sys.exit(2)
    res = {}
    m = mass_from_out(txt)
    if m is not None:
        res["mass"] = m
    if os.path.exists(op2):
        res.update(results_from_op2(op2))
    else:
        print("[DDS] no .op2 - add OP2 output request to the deck (references/optistruct.md)")
    if not res:
        print("[DDS] nothing extracted")
        sys.exit(3)
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("[DDS] results: %s" % json.dumps(res))


if __name__ == "__main__":
    main()
