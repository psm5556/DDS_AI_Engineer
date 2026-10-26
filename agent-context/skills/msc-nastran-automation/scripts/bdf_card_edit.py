# -*- coding: utf-8 -*-
"""DDS template - safe Nastran BDF card editor (MSC Nastran / any Nastran deck). Tool key: python.

Edits single fields of Bulk Data cards without shifting columns. Every physical line keeps its own
format, so decks that mix formats are fine:
  small field : 8-char fields   ("PSHELL         1       1     2.5")
  large field : 16-char fields  ("PSHELL*  1   ...", continuation lines start with "*")
  free field  : comma separated ("PSHELL,1,1,2.5")
Only the edited field text is rewritten; all other characters of the line are kept as they were.

Field numbering ("field" in a rule) = Nastran QRG numbering of the first line (1 = card name,
2 = first data field, ... 9). Continuation lines continue the count: the first data field of the
2nd (small-field) line is 10, then 11 ... 17, etc.  This equals pyNastran card.field(field - 1).
  PSHELL PID MID1 T MID2 12I/T3 MID3 TS/T NSM | Z1(10) Z2(11) MID4(12)
  MAT1   MID E G NU RHO A TREF GE | ST(10) SC(11) SS(12)
  FORCE  SID G CID F N1 N2 N3          PLOAD4 SID EID P1 P2 P3 P4 ...
  PBARL  PID MID GROUP TYPE | DIM1(10) DIM2(11) ...    (PBARL dims are on continuation lines)

Rules (JSON, params.json key -> rule):
  {"thickness":  {"card": "PSHELL", "id": 1, "field": 4},
   "e_mod":      {"card": "MAT1",   "id": 1, "field": 3},
   "load_scale": {"card": "FORCE",  "id": 10, "field": 5, "mode": "scale"},
   "n_modes":    {"kind": "regex", "section": "bulk", "pattern": "^(EIGRL,\\s*1,[^,]*,[^,]*,)\\s*\\d+",
                  "replace": "\\g<1>{ivalue}"},
   "method_id":  {"kind": "regex", "pattern": "^\\s*METHOD\\s*=\\s*\\d+", "replace": "METHOD = {ivalue}"}}
  card rule keys: card, field, id (= match {2: id}) or match {"field_no": value}, mode set|scale|add,
                  type real|int (default real), count (exact number of cards expected, optional)
  regex rule keys: pattern, replace ({value} / {ivalue} = int(round(value))), section control|bulk|all
                  (default control = text before BEGIN BULK)

CLI (run in the case folder; reads params.json):
  python bdf_card_edit.py --deck model.bdf --out case.bdf [--rules card_rules.json] [--placeholders] [--verify]
Import:
  from bdf_card_edit import edit_deck
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import sys

DEFAULT_RULES = {
    "thickness": {"card": "PSHELL", "id": 1, "field": 4},
    "e_mod": {"card": "MAT1", "id": 1, "field": 3},
    "load_scale": {"card": "FORCE", "id": 1, "field": 5, "mode": "scale"},
}

CONT_START = ("+", "*", ",")
INCLUDE_RE = re.compile(r"^\s*INCLUDE\s+'([^']+)'\s*$", re.I)


class DeckError(Exception):
    pass


# ----------------------------------------------------------------------------- number formatting
def nastran_real(value, width):
    """Shortest exact-enough text of `value` with a decimal point that fits in `width` chars."""
    value = float(value)
    if value == 0.0:
        return "0."
    best = None
    for prec in range(min(width, 15), 0, -1):
        s = ("%." + str(prec) + "g") % value
        if "e" in s:
            mant, exp = s.split("e")
            if "." not in mant:
                mant += "."
            s = "%sE%d" % (mant, int(exp))           # 1.5E-5 (explicit exponent, always valid)
        elif "." not in s:
            s += "."
        if len(s) <= width:
            best = s
            break
    if best is None:
        raise DeckError("cannot format %r into %d chars" % (value, width))
    return best


def nastran_int(value, width):
    f = float(value)
    if abs(f - round(f)) > 1e-9:
        raise DeckError("integer field got non-integer value %r" % value)
    s = str(int(round(f)))
    if len(s) > width:
        raise DeckError("integer %s does not fit %d chars" % (s, width))
    return s


def parse_real(text):
    """Nastran real incl. implicit exponent (1.5-3, 2.+5) and D exponent."""
    t = text.strip().upper().replace("D", "E")
    if not t:
        return 0.0
    m = re.match(r"^([+-]?[0-9]*\.?[0-9]*)([+-][0-9]+)$", t)
    if m and "E" not in t:
        t = m.group(1) + "E" + m.group(2)
    return float(t)


def same_value(text, want):
    t = text.strip()
    try:
        return abs(parse_real(t) - float(want)) <= 1e-9 * max(1.0, abs(float(want)))
    except (ValueError, TypeError):
        return t.upper() == str(want).strip().upper()


# ----------------------------------------------------------------------------- line model
class Line(object):
    """One physical line of a card: fmt small|large|free, data field slots (text, width)."""

    def __init__(self, text, first):
        self.raw = text
        body = text.rstrip("\r\n")
        self.eol = text[len(body):]
        if "\t" in body:
            raise DeckError("TAB in card line - cannot edit columns safely: %r" % body)
        self.comment = ""
        if "," in body.split("$", 1)[0]:
            self.fmt = "free"
            if "$" in body:
                body, c = body.split("$", 1)
                self.comment = "$" + c
            self.tokens = body.split(",")
            head = self.tokens[0].strip()
            self.large = head.endswith("*")
            n = 4 if self.large else 8
            self.data_idx = list(range(1, min(len(self.tokens), 1 + n)))
            self.ndata = n
        else:
            head = body[:8]
            self.large = head.strip().endswith("*") or (not first and head.startswith("*"))
            self.fmt = "large" if self.large else "small"
            w = 16 if self.large else 8
            n = 4 if self.large else 8
            padded = body.ljust(72)
            self.head = padded[:8]
            self.fields = [padded[8 + w * i: 8 + w * (i + 1)] for i in range(n)]
            self.tail = body[72:]
            self.width = w
            self.ndata = n
            self.orig_len = len(body)

    def get(self, k):
        """k-th data field text (0-based) on this line."""
        if self.fmt == "free":
            return self.tokens[k + 1] if k + 1 < len(self.tokens) else ""
        return self.fields[k]

    def width_for(self, free_width):
        if self.fmt == "free":
            return 16 if self.large else free_width
        return self.width

    def set(self, k, text):
        if self.fmt == "free":
            while len(self.tokens) < k + 2:
                self.tokens.append("")
            self.tokens[k + 1] = text
            return
        if len(text) > self.width:
            raise DeckError("value %r longer than field width %d" % (text, self.width))
        self.fields[k] = text.rjust(self.width)

    def render(self):
        if self.fmt == "free":
            return ",".join(self.tokens) + self.comment + self.eol
        out = self.head + "".join(self.fields)
        if self.tail:
            return out + self.tail + self.eol
        return out.rstrip() + self.eol


def is_card_start(text):
    s = text.rstrip("\r\n")
    if not s.strip() or s.lstrip().startswith("$"):
        return False
    return s[0] not in CONT_START and s[0] != " "


def is_continuation(text):
    s = text.rstrip("\r\n")
    if not s.strip():
        return False
    if s.lstrip().startswith("$"):
        return False
    return s[0] in CONT_START or s[:8].strip() == ""


def card_name(text):
    s = text.split("$", 1)[0]
    s = s.split(",", 1)[0] if "," in s else s[:8]
    return s.strip().upper().rstrip("*")


def iter_cards(lines, start, end):
    """Yield (name, [line indices]) for bulk cards in lines[start:end]. Comment lines inside a card
    are skipped but do not end it."""
    i = start
    while i < end:
        if is_card_start(lines[i]) and not INCLUDE_RE.match(lines[i]):
            idx = [i]
            j = i + 1
            while j < end:
                if lines[j].lstrip().startswith("$"):
                    j += 1
                    continue
                if is_continuation(lines[j]):
                    idx.append(j)
                    j += 1
                    continue
                break
            yield card_name(lines[i]), idx
            i = j
        else:
            i += 1


def locate(card_lines, field_no):
    """Map logical field number (>=2) -> (line position, data slot)."""
    if field_no < 2:
        raise DeckError("field number must be >= 2 (1 is the card name)")
    k = field_no - 2
    for pos, ln in enumerate(card_lines):
        if k < ln.ndata:
            return pos, k
        k -= ln.ndata
    return None, None


def bulk_range(lines):
    """(start, end) of bulk section. Files without BEGIN BULK (include files) are all bulk."""
    start, end = None, len(lines)
    for i, t in enumerate(lines):
        u = t.strip().upper()
        if start is None and u.startswith("BEGIN") and "BULK" in u:
            start = i + 1
        elif start is not None and u.startswith("ENDDATA"):
            end = i
            break
    if start is None:
        return 0, end, False
    return start, end, True


# ----------------------------------------------------------------------------- rule application
def apply_card_rule(files, key, rule, value, free_width, log):
    want = rule["card"].upper()
    match = dict((int(k), v) for k, v in (rule.get("match") or {}).items())
    if "id" in rule:
        match[2] = rule["id"]
    field_no = int(rule["field"])
    mode = rule.get("mode", "set")
    ftype = rule.get("type", "real")
    hits = 0
    for path, lines in files:
        start, end, _ = bulk_range(lines)
        for name, idx in iter_cards(lines, start, end):
            if name != want:
                continue
            objs = [Line(lines[n], n == idx[0]) for n in idx]
            ok = True
            for fno, v in match.items():
                pos, k = locate(objs, fno)
                if pos is None or not same_value(objs[pos].get(k), v):
                    ok = False
                    break
            if not ok:
                continue
            pos, k = locate(objs, field_no)
            if pos is None:
                raise DeckError("%s: field %d not present on %s card (line %d) - add the continuation "
                                "line in the template first" % (key, field_no, want, idx[0] + 1))
            ln = objs[pos]
            old = ln.get(k)
            if mode == "set":
                new = float(value)
            elif mode == "scale":
                new = parse_real(old) * float(value)
            elif mode == "add":
                new = parse_real(old) + float(value)
            else:
                raise DeckError("unknown mode %r" % mode)
            w = ln.width_for(free_width)
            text = nastran_int(new, w) if ftype == "int" else nastran_real(new, w)
            if ftype != "int" and new != 0.0:
                rel = abs(parse_real(text) - new) / abs(new)
                if rel > 1e-5:
                    log("[DDS] warning: %s rounded to %s (rel err %.2e, %s format)" % (key, text, rel, ln.fmt))
            ln.set(k, text)
            lines[idx[pos]] = ln.render()
            log("[DDS] %s: %s line %d field %d (%s) '%s' -> '%s' [%s]"
                % (key, want, idx[pos] + 1, field_no, os.path.basename(path), old.strip(), text, ln.fmt))
            hits += 1
    if hits == 0:
        raise DeckError("rule '%s': no %s card matched %s" % (key, want, match))
    if rule.get("count") is not None and hits != int(rule["count"]):
        raise DeckError("rule '%s': expected %s %s cards, matched %d" % (key, rule["count"], want, hits))
    return hits


def apply_regex_rule(files, key, rule, value, log):
    pat = re.compile(rule["pattern"], re.I | re.M)
    v = float(value)
    repl = rule["replace"].replace("{value}", nastran_real(v, 16)).replace("{ivalue}", str(int(round(v))))
    section = rule.get("section", "control")
    hits = 0
    for n, (path, lines) in enumerate(files):
        start, end, has_bulk = bulk_range(lines)
        if section == "control":
            if not has_bulk:
                continue
            a, b = 0, start
        elif section == "bulk":
            a, b = start, end
        else:
            a, b = 0, len(lines)
        for i in range(a, b):
            body = lines[i].rstrip("\r\n")
            eol = lines[i][len(body):]
            new, c = pat.subn(repl, body)
            if c:
                lines[i] = new + eol
                hits += c
                log("[DDS] %s: line %d '%s' -> '%s'" % (key, i + 1, body.strip(), new.strip()))
    if hits == 0:
        raise DeckError("regex rule '%s' matched nothing: %s" % (key, rule["pattern"]))
    return hits


def apply_placeholders(files, params, free_width, log):
    hits = 0
    for path, lines in files:
        start, end, has_bulk = bulk_range(lines)
        for i, t in enumerate(lines):
            if "{{" not in t:
                continue
            in_bulk = start <= i < end if has_bulk else True
            if in_bulk and "," not in t.split("$", 1)[0]:
                raise DeckError("placeholder in fixed-field bulk line %d (convert the card to free field): %r"
                                % (i + 1, t))
            for k, v in params.items():
                tag = "{{%s}}" % k
                if tag in t:
                    if in_bulk:
                        s = nastran_real(v, free_width)
                    else:
                        f = float(v)
                        s = str(int(f)) if f == int(f) else repr(f)
                    t = t.replace(tag, s)
                    hits += 1
            if "{{" in t:
                raise DeckError("unresolved placeholder at line %d: %r" % (i + 1, t))
            lines[i] = t
    log("[DDS] placeholders replaced: %d" % hits)
    return hits


# ----------------------------------------------------------------------------- files
def read_lines(path):
    with io.open(path, "r", encoding="latin-1", newline="") as f:
        return f.read().splitlines(True)


def write_lines(path, lines):
    with io.open(path, "w", encoding="latin-1", newline="") as f:
        f.write("".join(lines))


def collect_includes(path, seen=None):
    """[(path, lines)] for the deck and its single-line INCLUDE 'file' entries (recursive)."""
    seen = seen if seen is not None else []
    path = os.path.abspath(path)
    if path in [p for p, _ in seen]:
        return seen
    lines = read_lines(path)
    seen.append((path, lines))
    base = os.path.dirname(path)
    for t in lines:
        m = INCLUDE_RE.match(t.rstrip("\r\n"))
        if m:
            inc = m.group(1)
            inc = inc if os.path.isabs(inc) else os.path.join(base, inc)
            if os.path.exists(inc):
                collect_includes(inc, seen)
            else:
                raise DeckError("INCLUDE file not found: %s" % inc)
        elif t.strip().upper().startswith("INCLUDE") and "'" in t and not m:
            print("[DDS] warning: multi-line INCLUDE not followed: %r" % t.strip())
    return seen


def edit_deck(deck, out, params, rules=None, placeholders=False, free_width=8, follow_includes=True,
              log=print):
    """Apply rules/placeholders and write `out` (main deck). Edited INCLUDE files are rewritten in
    place and must live under the current folder (the case folder copies). Returns {key: hits}."""
    rules = DEFAULT_RULES if rules is None else rules
    files = collect_includes(deck) if follow_includes else [(os.path.abspath(deck), read_lines(deck))]
    originals = [list(l) for _, l in files]
    report = {}
    if placeholders:
        report["_placeholders"] = apply_placeholders(files, params, free_width, log)
    for key, rule in rules.items():
        if key not in params:
            continue
        if rule.get("kind") == "regex":
            report[key] = apply_regex_rule(files, key, rule, params[key], log)
        else:
            report[key] = apply_card_rule(files, key, rule, params[key], free_width, log)
    unused = [k for k in params if not k.startswith("_") and k not in rules]
    if unused and not placeholders:
        log("[DDS] note: params without rule (ignored here): %s" % ", ".join(sorted(unused)))
    cwd = os.path.abspath(os.getcwd())
    for n, (path, lines) in enumerate(files):
        if n == 0:
            write_lines(out, lines)
        elif lines != originals[n]:
            if not path.startswith(cwd):
                raise DeckError("refusing to edit include outside the case folder: %s" % path)
            write_lines(path, lines)
            log("[DDS] include rewritten: %s" % path)
    return report


def verify_with_pynastran(path):
    from pyNastran.bdf.bdf import read_bdf
    model = read_bdf(path, xref=False, debug=None)
    print("[DDS] pyNastran read ok: %d nodes, %d elements, %d properties, %d materials"
          % (len(model.nodes), len(model.elements), len(model.properties), len(model.materials)))
    return model


def main():
    ap = argparse.ArgumentParser(description="Safe Nastran BDF card editor")
    ap.add_argument("--deck", required=True, help="template deck (copied into the case folder)")
    ap.add_argument("--out", default="case.bdf")
    ap.add_argument("--params", default="params.json")
    ap.add_argument("--rules", default=None, help="rules JSON (default: card_rules.json if present, else built-in)")
    ap.add_argument("--placeholders", action="store_true", help="also replace {{key}} in free-field lines")
    ap.add_argument("--free-width", type=int, default=8, help="max chars for free-field values (8 = safe)")
    ap.add_argument("--no-includes", action="store_true")
    ap.add_argument("--verify", action="store_true", help="re-read the result with pyNastran")
    a = ap.parse_args()
    try:
        with io.open(a.params, "r", encoding="utf-8") as f:
            params = json.load(f)
        rules_path = a.rules or ("card_rules.json" if os.path.exists("card_rules.json") else None)
        rules = None
        if rules_path:
            with io.open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
        rep = edit_deck(a.deck, a.out, params, rules, a.placeholders, a.free_width, not a.no_includes)
        print("[DDS] edits: %s -> %s" % (json.dumps(rep), a.out))
        if a.verify:
            verify_with_pynastran(a.out)
    except (DeckError, ValueError, KeyError, IOError, OSError) as e:
        print("[DDS] ERROR: %s" % e)
        sys.exit(2)


if __name__ == "__main__":
    main()
