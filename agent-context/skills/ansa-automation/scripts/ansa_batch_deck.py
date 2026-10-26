# -*- coding: utf-8 -*-
"""DDS template - ANSA batch pre-processing step (tool key: ansa).

  ansa64.bat -nogui -exec "load_script: 'ansa_batch_deck.py'" -exec "main()"

Runs inside ANSA's own Python (do not import numpy etc.). In the case folder it
  1. reads params.json (+ optional ansa_config.json, else DEFAULT_CONFIG below)
  2. opens the model: .ansa database, or imports a Nastran/Abaqus/LS-DYNA deck
  3. (optional) applies morphing parameters        -> TODO(사내검증), see apply_morph()
  4. (optional) re-runs Batch Mesh                  -> TODO(사내검증), see run_batchmesh()
  5. changes property/material card values (PSHELL T, MAT1 E ...) and reads them back
  6. exports the solver deck (OutputNastran / OutputAbaqus / OutputLSDyna) and optionally saves .ansa
  7. writes ansa_status.json. results.json is NOT written here: the next study step (solver run +
     extraction, e.g. msc-nastran-automation/scripts/nastran_run.py) writes it.
On any error the output deck is removed, so the next step fails cleanly (ANSA's own exit code is not
trusted, TODO(사내검증)).
Console messages are ASCII.
"""
import json
import os
import sys
import traceback

import ansa
from ansa import base, constants

DEFAULT_CONFIG = {
    # model file in the case folder (copied by study "inputs"); None = first model file found
    "model": None,
    # solver deck used for card access and export: NASTRAN | ABAQUS | LSDYNA
    "deck": "NASTRAN",
    # params.json key -> card value change. "field" = ANSA card field label (as shown in the card editor)
    "cards": {
        "thickness": {"type": "PSHELL", "id": 1, "field": "T"},
        "e_mod": {"type": "MAT1", "id": 1, "field": "E"},
    },
    # params.json key -> morph parameter name (only used when present in params)
    "morph": {},
    "batchmesh": False,
    "output": "case.bdf",
    "save_ansa": None,          # e.g. "case.ansa" to keep the modified database for debugging
}

MODEL_EXT = (".ansa", ".bdf", ".nas", ".dat", ".fem", ".inp", ".k", ".key", ".dyn")
DECKS = {"NASTRAN": "NASTRAN", "ABAQUS": "ABAQUS", "LSDYNA": "LSDYNA"}


def log(msg):
    print("[DDS] " + msg)
    sys.stdout.flush()


def case_dir():
    # TODO(사내검증): ANSA keeps the launch cwd; DDS_CASE_DIR is set by the platform as a fallback
    return os.environ.get("DDS_CASE_DIR") or os.getcwd()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def find_model(cfg, cdir):
    if cfg.get("model"):
        p = os.path.join(cdir, cfg["model"])
        if not os.path.exists(p):
            raise RuntimeError("model not found: %s" % p)
        return p
    meta = load_json(os.path.join(cdir, "case.json"), {}) or {}
    names = list(meta.get("inputs", [])) + sorted(os.listdir(cdir))
    for n in names:
        if n.lower().endswith(MODEL_EXT) and n != cfg.get("output") and os.path.exists(os.path.join(cdir, n)):
            return os.path.join(cdir, n)
    raise RuntimeError("no model file (%s) in %s" % (", ".join(MODEL_EXT), cdir))


def deck_const(cfg):
    name = DECKS.get(str(cfg.get("deck", "NASTRAN")).upper())
    if name is None:
        raise RuntimeError("unsupported deck %r" % cfg.get("deck"))
    return getattr(constants, name)


def open_model(path):
    ext = os.path.splitext(path)[1].lower()
    log("open model %s" % os.path.basename(path))
    if ext == ".ansa":
        base.Open(path)
    elif ext in (".bdf", ".nas", ".dat", ".fem"):
        base.InputNastran(filename=path)   # TODO(사내검증): extra keyword options per ANSA version
    elif ext == ".inp":
        base.InputAbaqus(filename=path)
    elif ext in (".k", ".key", ".dyn"):
        base.InputLSDyna(filename=path)
    else:
        # CAD (.stp/.igs/.CATPart ...) needs translation + cleanup + meshing first.
        # TODO(사내검증): use the in-house CAD translation call (or prepare an .ansa db once in GUI)
        raise RuntimeError("unsupported model type %s" % ext)


def apply_morph(params, cfg, deck):
    """Morphing (shape) parameters. Disabled until verified.
    Recommended practice: build the morph parameters / design variables once in the ANSA GUI, save the
    .ansa, then look up the exact ansa.morph functions in the ANSA scripting help (Help > Script API,
    module ansa.morph) for 'set parameter value' and 'apply/update'.
    TODO(사내검증): implement with the verified ansa.morph calls; keep the check below."""
    todo = [k for k in cfg.get("morph", {}) if k in params]
    if not todo:
        return {}
    raise NotImplementedError("morph parameters %s requested but apply_morph() is not implemented "
                              "(see TODO in ansa_batch_deck.py)" % todo)


def run_batchmesh(cfg):
    """Re-mesh with the Batch Mesh sessions stored in the .ansa database.
    TODO(사내검증): confirm the batchmesh API name (e.g. batchmesh.RunAllMeshingScenarios) in the
    ANSA scripting help of the installed version before enabling 'batchmesh' in the config."""
    if not cfg.get("batchmesh"):
        return
    from ansa import batchmesh
    fn = getattr(batchmesh, "RunAllMeshingScenarios", None)
    if fn is None:
        raise RuntimeError("batchmesh.RunAllMeshingScenarios not available in this ANSA version")
    log("batch mesh start")
    fn()
    log("batch mesh done")


def set_card_values(params, cfg, deck):
    changed = {}
    for key, rule in cfg.get("cards", {}).items():
        if key not in params:
            continue
        ent = base.GetEntity(deck, rule["type"], int(rule["id"]))
        if ent is None:
            raise RuntimeError("%s id %s not found (rule %s)" % (rule["type"], rule["id"], key))
        field = rule["field"]
        value = float(params[key])
        if rule.get("mode") == "scale":
            old = base.GetEntityCardValues(deck, ent, (field,)).get(field)
            value = float(old) * value
        base.SetEntityCardValues(deck, ent, {field: value})
        back = base.GetEntityCardValues(deck, ent, (field,)).get(field)
        try:
            ok = abs(float(back) - value) <= 1e-6 * max(1.0, abs(value))
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise RuntimeError("%s %s field %s: set %r, read back %r (check field label)"
                               % (rule["type"], rule["id"], field, value, back))
        log("%s: %s %s %s = %s" % (key, rule["type"], rule["id"], field, back))
        changed[key] = float(back)
    return changed


def export_deck(cfg, deck, out):
    name = str(cfg.get("deck", "NASTRAN")).upper()
    log("export %s deck -> %s" % (name, os.path.basename(out)))
    # TODO(사내검증): output options (include handling, format, 'mode' of exported entities)
    if name == "NASTRAN":
        base.OutputNastran(filename=out)
    elif name == "ABAQUS":
        base.OutputAbaqus(filename=out)
    else:
        base.OutputLSDyna(filename=out)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError("deck export produced no file: %s" % out)


def finish(code):
    """Leave ANSA. TODO(사내검증): confirm session.Quit() and whether the exit code reaches the platform."""
    try:
        from ansa import session
        session.Quit()
    except Exception:
        pass
    sys.exit(code)


def main():
    cdir = case_dir()
    os.chdir(cdir)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(os.path.join(cdir, "ansa_config.json"), {}) or {})
    out = os.path.join(cdir, cfg["output"])
    status = {"ok": False, "deck": cfg["output"]}
    for p in (out, os.path.join(cdir, "ansa_status.json"), os.path.join(cdir, "results.json")):
        if os.path.exists(p):
            os.remove(p)
    code = 0
    try:
        params = load_json(os.path.join(cdir, "params.json"), None)
        if params is None:
            raise RuntimeError("params.json missing in %s" % cdir)
        deck = deck_const(cfg)
        base.SetCurrentDeck(deck)
        open_model(find_model(cfg, cdir))
        status["morph"] = apply_morph(params, cfg, deck)
        run_batchmesh(cfg)
        status["cards"] = set_card_values(params, cfg, deck)
        export_deck(cfg, deck, out)
        if cfg.get("save_ansa"):
            base.SaveAs(os.path.join(cdir, cfg["save_ansa"]))
        status["ok"] = True
        log("done")
    except Exception as e:
        traceback.print_exc()
        log("FAILED: %s" % e)
        status["error"] = str(e)
        if os.path.exists(out):
            os.remove(out)
        code = 1
    with open(os.path.join(cdir, "ansa_status.json"), "w") as f:
        json.dump(status, f, indent=2)
    finish(code)
