#!/usr/bin/env bash
# of_run.sh - OpenFOAM case runner (tool key: openfoam -> "wsl bash of_run.sh <args>")
# Runs in the case folder (WSL translates the Windows cwd to /mnt/<drive>/...).
#   options: --np N             parallel run with N ranks (decomposePar/mpirun/reconstructPar)
#            --bashrc PATH      OpenFOAM etc/bashrc (default: search /opt/openfoam*, /usr/lib/openfoam/*)
#            --mesh auto|block|snappy|none   (auto: skip if polyMesh exists, snappy if snappyHexMeshDict exists)
#            --solver NAME      default: 'application' in system/controlDict (foamRun if 'solver' entry)
#            --post "f1 f2"     extra postProcess -func after the run (-latestTime)
#            --vtk              foamToVTK -latestTime
#            --linux-copy       run in $HOME/dds_runs (fast ext4), copy logs/postProcessing/VTK back
# Steps: env -> foam_set.txt (foamDictionary -set) -> mesh -> solve -> check log -> post.
# Exit code != 0 on any failure. Messages are ASCII.

NP=1; BASHRC=""; MESH="auto"; SOLVER=""; POSTFUNCS=""; VTK=0; LINUX_COPY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --np) NP="$2"; shift 2 ;;
    --bashrc) BASHRC="$2"; shift 2 ;;
    --mesh) MESH="$2"; shift 2 ;;
    --solver) SOLVER="$2"; shift 2 ;;
    --post) POSTFUNCS="$2"; shift 2 ;;
    --vtk) VTK=1; shift ;;
    --linux-copy) LINUX_COPY=1; shift ;;
    *) echo "[of_run] unknown option: $1"; exit 2 ;;
  esac
done

log() { echo "[of_run] $*"; }

# case folder: cwd, or DDS_CASE_DIR passed through WSLENV (DDS_CASE_DIR/p)
if [ ! -d system ] && [ -n "${DDS_CASE_DIR:-}" ] && [ -d "$DDS_CASE_DIR" ]; then cd "$DDS_CASE_DIR" || exit 1; fi
# a folder input is copied as a subfolder (e.g. ./duct_case/system): use the single subfolder with system/
if [ ! -d system ]; then
  SUBS=$(ls -d */system 2>/dev/null)
  if [ "$(echo "$SUBS" | grep -c system)" = "1" ]; then cd "$(dirname "$SUBS")" || exit 1; fi
fi
[ -d system ] || { log "FAILED: system/ not found in $(pwd) or a single subfolder"; exit 1; }
log "case root: $(pwd)"
ORIG_DIR="$(pwd)"
WORK_DIR="$ORIG_DIR"

copy_back() {
  if [ "$LINUX_COPY" = "1" ] && [ "$WORK_DIR" != "$ORIG_DIR" ]; then
    for item in postProcessing VTK; do
      [ -e "$WORK_DIR/$item" ] && cp -r "$WORK_DIR/$item" "$ORIG_DIR/"
    done
    cp "$WORK_DIR"/log.* "$ORIG_DIR/" 2>/dev/null
    log "copied results back to $ORIG_DIR"
  fi
}
fail() { log "FAILED: $*"; copy_back; exit 1; }

# 1) OpenFOAM environment (non-interactive bash does not read ~/.bashrc)
if [ -z "${WM_PROJECT_DIR:-}" ]; then
  for f in "$BASHRC" ${FOAM_BASHRC:-} /opt/openfoam*/etc/bashrc /usr/lib/openfoam/openfoam*/etc/bashrc "$HOME"/OpenFOAM/OpenFOAM-*/etc/bashrc; do
    if [ -n "$f" ] && [ -f "$f" ]; then log "source $f"; . "$f"; break; fi
  done
fi
command -v foamDictionary >/dev/null 2>&1 || fail "OpenFOAM environment not found (use --bashrc /path/etc/bashrc)"
log "OpenFOAM: ${WM_PROJECT:-?} ${WM_PROJECT_VERSION:-?}"

if [ "$LINUX_COPY" = "1" ]; then
  WORK_DIR="$HOME/dds_runs/$(basename "$ORIG_DIR")_$$"
  mkdir -p "$WORK_DIR" && cp -r "$ORIG_DIR"/. "$WORK_DIR"/ || fail "copy to $WORK_DIR"
  cd "$WORK_DIR" || fail "cd $WORK_DIR"
  log "working in $WORK_DIR"
fi

run() {  # run <logname> <command...>
  local name="$1"; shift
  log "run: $*"
  "$@" > "log.$name" 2>&1
  local rc=$?
  if [ $rc -ne 0 ] || grep -q "FOAM FATAL" "log.$name"; then
    tail -n 30 "log.$name"
    fail "$name (rc=$rc)"
  fi
}

# 2) dictionary changes prepared by of_prepare.py (file<TAB>entry<TAB>value)
if [ -s foam_set.txt ]; then
  while IFS=$'\t' read -r file entry value; do
    [ -z "$file" ] && continue
    foamDictionary "$file" -entry "$entry" -set "$value" > /dev/null || fail "foamDictionary $file -entry $entry"
    log "set $file : $entry = $value"
  done < foam_set.txt
fi

# 3) mesh
if [ "$MESH" = "auto" ]; then
  if [ -f constant/polyMesh/faces ] || [ -f constant/polyMesh/faces.gz ]; then MESH="none"
  elif [ -f system/snappyHexMeshDict ]; then MESH="snappy"
  else MESH="block"; fi
fi
if [ "$MESH" = "block" ] || [ "$MESH" = "snappy" ]; then
  run blockMesh blockMesh
fi
if [ "$MESH" = "snappy" ]; then
  if [ -f system/surfaceFeatureExtractDict ]; then run surfaceFeatureExtract surfaceFeatureExtract
  elif [ -f system/surfaceFeaturesDict ]; then run surfaceFeatures surfaceFeatures; fi
  run snappyHexMesh snappyHexMesh -overwrite
fi
checkMesh > log.checkMesh 2>&1
grep -q "Mesh OK" log.checkMesh && log "checkMesh: Mesh OK" || log "WARN: checkMesh reported problems (see log.checkMesh)"

# 4) solver
if [ -z "$SOLVER" ]; then
  SOLVER=$(foamDictionary -entry application -value system/controlDict 2>/dev/null)
  if [ -z "$SOLVER" ] && foamDictionary -entry solver -value system/controlDict > /dev/null 2>&1; then SOLVER="foamRun"; fi
fi
[ -n "$SOLVER" ] || fail "solver unknown (use --solver)"
command -v "$SOLVER" > /dev/null 2>&1 || fail "solver not found: $SOLVER"

if [ "$NP" -gt 1 ]; then
  [ -f system/decomposeParDict ] || fail "system/decomposeParDict missing for --np $NP"
  foamDictionary system/decomposeParDict -entry numberOfSubdomains -set "$NP" > /dev/null
  run decomposePar decomposePar -force
  run "$SOLVER" mpirun -np "$NP" "$SOLVER" -parallel
  run reconstructPar reconstructPar -latestTime
else
  run "$SOLVER" "$SOLVER"
fi
tail -n 5 "log.$SOLVER" | grep -q "^End" || fail "$SOLVER log has no 'End' line (diverged or killed?)"
grep -qiE "floating point exception|(^|[^a-z])-?nan([^a-z]|$)" "log.$SOLVER" && log "WARN: 'nan' or FPE text found in log.$SOLVER"

# 5) post
PP=postProcess; command -v postProcess > /dev/null 2>&1 || PP=foamPostProcess
for func in $POSTFUNCS; do
  run "post_$func" "$PP" -func "$func" -latestTime
done
if [ "$VTK" = "1" ]; then run foamToVTK foamToVTK -latestTime; fi

copy_back
log "done: solver=$SOLVER np=$NP mesh=$MESH"
exit 0
