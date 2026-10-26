# DDS template - HyperMesh batch: change property thickness / material / (load) and export OptiStruct deck
# Run   : tool key "altair_hm" -> hmbatch -tcl hm_update_export.tcl   (cwd = case dir)
# In    : params.json (flat numeric dict), MODEL_FILE copied into the case dir by study "inputs"
# Out   : OUT_FEM (OptiStruct deck). When run standalone (env DDS_HM_STANDALONE != 0) also
#         results.json {"hm_export_ok": 1}. os_case_driver.py sets DDS_HM_STANDALONE=0 and runs the solver.
#
# IMPORTANT: attribute IDs (95 = PSHELL T, 1 = MAT1 E ...) and some command signatures depend on the
# HyperMesh version / solver template. Do the same edit once in the GUI and copy the exact lines from
# command.tcl (Documents folder or HyperMesh start dir). Lines marked TODO(사내검증) must be checked.
# Save as UTF-8. If hmbatch reports encoding errors, remove non-ASCII characters from comments.

# ------------------------------------------------------------------ settings
set MODEL_FILE "plate.hm"
set OUT_FEM    "model.fem"
# params.json key -> property name (PSHELL thickness)
set PROP_MAP   [dict create thickness "shell_prop"]
# params.json key -> material name (MAT1 Young's modulus)
set MAT_E_MAP  [dict create e_mod "steel"]

# ------------------------------------------------------------------ helpers
proc dds_log {msg} { puts "\[DDS\] $msg" }

# Minimal reader for a FLAT json object {"a": 1.5, "b": "x"} (no nesting).
proc dds_read_params {path} {
    set fh [open $path r]
    set txt [read $fh]
    close $fh
    set result [dict create]
    foreach {all k v} [regexp -all -inline {"([^"]+)"\s*:\s*("[^"]*"|[-+0-9.eE]+)} $txt] {
        dict set result $k [string trim $v "\""]
    }
    return $result
}

proc dds_write_json {path d} {
    set parts {}
    dict for {k v} $d { lappend parts "\"$k\": $v" }
    set fh [open $path w]
    puts $fh "\{[join $parts {, }]\}"
    close $fh
}

proc dds_fail {msg} {
    dds_log "FAILED: $msg"
    exit 1
}

# ------------------------------------------------------------------ main
set case_dir [pwd]
set res_path [file join $case_dir results.json]
if {[file exists $res_path]} { file delete $res_path }

if {[catch {set params [dds_read_params [file join $case_dir params.json]]} err]} {
    dds_fail "cannot read params.json: $err"
}
dds_log "params: $params"

# Open model. TODO(사내검증): some versions record '*readfile "<path>" 0'
if {[catch {*readfile [file join $case_dir $MODEL_FILE]} err]} {
    dds_fail "cannot open model: $err"
}

# --- property thickness (OptiStruct PSHELL T = attribute 95)  TODO(사내검증) via command.tcl
dict for {key pname} $PROP_MAP {
    if {[dict exists $params $key]} {
        set val [dict get $params $key]
        if {[catch {*setvalue props name=$pname STATUS=1 95=$val} err]} {
            dds_fail "thickness $pname: $err"
        }
        dds_log "PSHELL $pname T = $val"
    }
}

# --- material Young's modulus (OptiStruct MAT1 E = attribute 1)  TODO(사내검증) via command.tcl
dict for {key mname} $MAT_E_MAP {
    if {[dict exists $params $key]} {
        set val [dict get $params $key]
        if {[catch {*setvalue mats name=$mname STATUS=1 1=$val} err]} {
            dds_fail "material $mname: $err"
        }
        dds_log "MAT1 $mname E = $val"
    }
}

# --- load magnitude: TODO(사내검증) - record the edit of a force in the GUI and paste the command here,
#     or (recommended) scale FORCE/PLOAD cards in the exported .fem with os_case_driver.py (CARD_RULES).

# --- morphing (HyperMorph shapes): TODO(사내검증) - create shapes in the GUI, apply one with a scale
#     factor while recording, then copy the command from command.tcl. Typical DOE: one shape per param.

# --- export OptiStruct deck with the standard template
set tpl_dir [hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]
set tpl [file join $tpl_dir feoutput optistruct optistruct]
set out_path [file join $case_dir $OUT_FEM]
hm_answernext yes
# args: template file reserved1 reserved2 export_type(1=all) string_array number_of_strings
# TODO(사내검증): argument values differ between versions - compare with an exported command.tcl
if {[catch {*feoutputwithdata $tpl $out_path 0 0 1 1 0} err]} {
    dds_fail "export failed: $err"
}
if {![file exists $out_path]} { dds_fail "deck not written: $out_path" }
dds_log "deck written: $out_path"

# optional: keep the modified .hm (TODO(사내검증) '*writefile <path> 1')
# hm_answernext yes
# *writefile [file join $case_dir case_modified.hm] 1

set standalone 1
if {[info exists ::env(DDS_HM_STANDALONE)] && $::env(DDS_HM_STANDALONE) eq "0"} { set standalone 0 }
if {$standalone} {
    dds_write_json $res_path [dict create hm_export_ok 1]
}
dds_log "done"
# hmbatch exits when the script ends; *quit 1 forces exit without saving prompts.
*quit 1
