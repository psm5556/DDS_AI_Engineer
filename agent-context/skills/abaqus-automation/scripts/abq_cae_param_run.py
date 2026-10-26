# -*- coding: utf-8 -*-
"""
Abaqus/CAE 케이스 스크립트 템플릿 (tool 키: abaqus_cae)
  실행: abaqus cae noGUI=abq_cae_param_run.py -- [model.cae]

처리 순서
  1) params.json 읽기
  2) .cae 열기 (인자 > case.json inputs > params['_model'] 순서로 찾음)
  3) 형상/재료/두께/하중/BC/메쉬/스텝/CPU 변경  <- apply_params() 를 모델에 맞게 수정
  4) 질량 계산 (getMassProperties)
  5) 잡 제출, 대기, .sta/.log 로 완료 판정
  6) ODB에서 결과 추출
  7) images/*.png 저장
  8) results.json 작성 (실패 시 작성하지 않고 종료코드 1)

py2/py3 겸용: f-string 금지, keys()는 list()로 감싼다.
"""
from __future__ import print_function

import glob
import json
import os
import sys
import time

from abaqus import *            # noqa: F401,F403  (mdb, session, openMdb)
from abaqusConstants import *   # noqa: F401,F403
from caeModules import *        # noqa: F401,F403
import mesh                     # noqa: F401  (mesh.ElemType 사용 시)
from odbAccess import openOdb

# ============================ 사용자 설정 ============================
MODEL_NAME = 'Model-1'
PART_NAME = 'Part-1'
STEP_NAME = 'Step-1'           # 하중/BC를 바꿀 스텝, 결과 추출 스텝(None 이면 마지막 스텝)
JOB_NAME = 'case_job'          # 영문/숫자/밑줄만
RF_NODE_SET = 'FIX'            # 반력 합을 구할 어셈블리 노드셋 이름 (None 이면 생략)
MAKE_IMAGES = True
SAVE_CAE = False               # 디버깅용으로 수정된 모델을 case_model.cae 로 저장
# ====================================================================

CASE_DIR = os.getcwd()


def log(msg):
    print('[abq_cae_param_run] ' + str(msg))
    sys.stdout.flush()


def script_args():
    """abaqus cae noGUI=x.py -- a b  ->  ['a', 'b']
    TODO(사내검증): 버전에 따라 sys.argv 에 '--' 가 남지 않을 수 있다. 그 경우 params['_model'] 사용."""
    argv = list(sys.argv)
    if '--' in argv:
        return argv[argv.index('--') + 1:]
    return []


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    f = open(path, 'r')
    try:
        return json.load(f)
    finally:
        f.close()


def write_json(path, data):
    f = open(path, 'w')
    try:
        json.dump(data, f, indent=2, sort_keys=True)
    finally:
        f.close()


def find_cae_path(params, case):
    for a in script_args():
        if a.lower().endswith('.cae'):
            return os.path.abspath(a)
    for name in (case or {}).get('inputs', []):
        if str(name).lower().endswith('.cae'):
            return os.path.join(CASE_DIR, name)
    if params.get('_model'):
        return os.path.abspath(params['_model'])
    cands = glob.glob(os.path.join(CASE_DIR, '*.cae'))
    if len(cands) == 1:
        return cands[0]
    raise RuntimeError('Cannot find .cae model (arg / case.json inputs / params _model)')


def num(params, key, default=None):
    v = params.get(key, default)
    return None if v is None else float(v)


# --------------------------------------------------------------------
# 모델 수정: 이 함수를 대상 모델에 맞게 고친다.
# 각 블록은 params.json 에 해당 키가 있을 때만 실행된다.
# --------------------------------------------------------------------
def apply_params(model, params):
    part = model.parts[PART_NAME]
    geom_changed = False

    # (1) 피처 값 변경: 돌출 깊이
    if 'depth' in params:
        part.features['Solid extrude-1'].setValues(depth=num(params, 'depth'))
        geom_changed = True

    # (2) 스케치 치수 변경: 치수 인덱스는 GUI 기록(abaqus.rpy)에서 확인
    if 'width' in params:
        edit_sketch_dimensions(model, part, 'Solid extrude-1', {0: num(params, 'width')})
        geom_changed = True

    if geom_changed:
        part.regenerate()
        model.rootAssembly.regenerate()

    # (3) 재료
    if 'youngs_modulus' in params or 'poisson' in params:
        mat = model.materials['Steel']
        e_old, nu_old = mat.elastic.table[0][0], mat.elastic.table[0][1]
        mat.elastic.setValues(table=((num(params, 'youngs_modulus', e_old), num(params, 'poisson', nu_old)),))
    if 'density' in params:
        model.materials['Steel'].density.setValues(table=((num(params, 'density'),),))

    # (4) 쉘 두께 (HomogeneousShellSection)
    if 'thickness' in params:
        model.sections['Section-1'].setValues(thickness=num(params, 'thickness'))

    # (5) 하중: 집중하중은 cf1/cf2/cf3, 압력은 magnitude
    if 'force' in params:
        model.loads['Load-1'].setValues(cf2=-num(params, 'force'))
    if 'pressure' in params:
        model.loads['Pressure-1'].setValues(magnitude=num(params, 'pressure'))

    # (6) 변위 BC
    if 'disp' in params:
        model.boundaryConditions['BC-Disp'].setValues(u2=-num(params, 'disp'))

    # (7) 스텝 시간/증분
    if 'max_inc' in params:
        model.steps[STEP_NAME].setValues(maxInc=num(params, 'max_inc'))

    # (8) 메쉬 (형상이 바뀌면 반드시 재메쉬)
    if 'mesh_size' in params or geom_changed:
        size = num(params, 'mesh_size', None)
        part.deleteMesh()
        if size is not None:
            part.seedPart(size=size, deviationFactor=0.1, minSizeFactor=0.1)
        part.generateMesh()
        model.rootAssembly.regenerate()
        if len(part.elements) == 0:
            raise RuntimeError('Mesh generation failed (0 elements)')

    # (9) 형상 변경 후 Set 연결 확인 (지오메트리 기반 Set은 끊길 수 있음)
    a = model.rootAssembly
    if RF_NODE_SET and RF_NODE_SET in a.sets.keys():
        if len(a.sets[RF_NODE_SET].nodes) == 0:
            raise RuntimeError('Set %s lost its nodes after regenerate' % RF_NODE_SET)


def edit_sketch_dimensions(model, part, feature_name, dim_values):
    """GUI 'Edit Sketch'가 기록하는 패턴. dim_values = {치수인덱스: 값}"""
    feat = part.features[feature_name]
    model.ConstrainedSketch(name='__edit__', objectToCopy=feat.sketch)
    s = model.sketches['__edit__']
    part.projectReferencesOntoSketch(sketch=s, upToFeature=feat, filter=COPLANAR_EDGES)
    for idx, val in dim_values.items():
        s.dimensions[idx].setValues(value=val)
    feat.setValues(sketch=s)
    del model.sketches['__edit__']


def setup_job(model, params):
    ncpu = int(params.get('_ncpus', params.get('ncpus', 1)))
    if JOB_NAME in mdb.jobs.keys():
        del mdb.jobs[JOB_NAME]
    job = mdb.Job(name=JOB_NAME, model=model.name, numCpus=ncpu, numDomains=ncpu,
                  memory=90, memoryUnits=PERCENTAGE)
    return job


# --------------------------------------------------------------------
# 완료 판정
# --------------------------------------------------------------------
def tail_text(path, nbytes=20000):
    if not os.path.exists(path):
        return ''
    f = open(path, 'rb')
    try:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        data = f.read()
    finally:
        f.close()
    return data.decode('latin-1')


def print_errors(job_name):
    for ext in ('.msg', '.dat'):
        txt = tail_text(os.path.join(CASE_DIR, job_name + ext), 200000)
        for line in txt.splitlines():
            if '***ERROR' in line:
                log(ext + ': ' + line.strip())


def check_job_status(job_name):
    """returns 'completed' | 'failed' | 'unknown'"""
    sta = tail_text(os.path.join(CASE_DIR, job_name + '.sta'))
    if 'HAS COMPLETED SUCCESSFULLY' in sta:
        return 'completed'
    if 'HAS NOT BEEN COMPLETED' in sta:
        print_errors(job_name)
        return 'failed'
    lg = tail_text(os.path.join(CASE_DIR, job_name + '.log'))
    if 'exited with error' in lg or 'exited with an error' in lg:
        print_errors(job_name)
        return 'failed'
    if 'COMPLETED' in lg:
        return 'completed'
    print_errors(job_name)
    return 'unknown'


# --------------------------------------------------------------------
# ODB 추출
# --------------------------------------------------------------------
def open_odb_retry(path, tries=10):
    last = None
    for _ in range(tries):
        try:
            return openOdb(path=path, readOnly=True)
        except Exception as e:  # .lck 남아 있는 경우 등
            last = e
            time.sleep(3)
    raise last


def find_node_set(odb, name):
    ra = odb.rootAssembly
    for n in (name, name.upper()):
        if n in ra.nodeSets.keys():
            return ra.nodeSets[n]
    for inst in ra.instances.values():
        for n in (name, name.upper()):
            if n in inst.nodeSets.keys():
                return inst.nodeSets[n]
    raise KeyError('Node set not found in ODB: ' + name)


def extract_results(odb_path):
    res = {}
    odb = open_odb_retry(odb_path)
    try:
        step_name = STEP_NAME if STEP_NAME in odb.steps.keys() else list(odb.steps.keys())[-1]
        frame = odb.steps[step_name].frames[-1]
        fos = frame.fieldOutputs
        if 'S' in fos.keys():
            res['max_mises'] = max(v.mises for v in fos['S'].values)   # 적분점 기준
        if 'U' in fos.keys():
            res['max_u'] = max(v.magnitude for v in fos['U'].values)
        if RF_NODE_SET and 'RF' in fos.keys():
            sub = fos['RF'].getSubset(region=find_node_set(odb, RF_NODE_SET))
            tot = [0.0, 0.0, 0.0]
            for v in sub.values:
                for i, c in enumerate(v.data):
                    tot[i] += float(c)
            res['rf_sum_1'], res['rf_sum_2'], res['rf_sum_3'] = tot
        res['step_time'] = float(frame.frameValue)
    finally:
        odb.close()
    return res


# --------------------------------------------------------------------
# 이미지
# --------------------------------------------------------------------
def save_images(odb_path):
    img_dir = os.path.join(CASE_DIR, 'images')
    if not os.path.isdir(img_dir):
        os.makedirs(img_dir)
    odb = session.openOdb(name=odb_path)
    vp = session.viewports[list(session.viewports.keys())[0]]
    vp.setValues(displayedObject=odb)
    step_idx = len(odb.steps) - 1
    last_step = odb.steps[list(odb.steps.keys())[-1]]
    vp.odbDisplay.setFrame(step=step_idx, frame=len(last_step.frames) - 1)
    vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
    vp.odbDisplay.commonOptions.setValues(deformationScaling=UNIFORM, uniformScaleFactor=1.0)
    vp.viewportAnnotationOptions.setValues(triad=ON, legend=ON, title=OFF, state=ON, compass=OFF)
    session.printOptions.setValues(vpDecorations=OFF, reduceColors=False)
    session.pngOptions.setValues(imageSize=(1600, 1000))

    shots = [
        ('mises_iso', 'S', INTEGRATION_POINT, (INVARIANT, 'Mises'), 'Iso'),
        ('umag_iso', 'U', NODAL, (INVARIANT, 'Magnitude'), 'Iso'),
    ]
    for fname, label, pos, refine, view in shots:
        try:
            vp.odbDisplay.setPrimaryVariable(variableLabel=label, outputPosition=pos, refinement=refine)
            vp.view.setValues(session.views[view])
            vp.view.fitView()
            session.printToFile(fileName=os.path.join(img_dir, fname), format=PNG, canvasObjects=(vp,))
            log('image saved: ' + fname + '.png')
        except Exception as e:
            log('image failed (' + fname + '): ' + str(e))   # 이미지는 선택 출력이므로 계속 진행
    try:
        odb.close()
    except Exception:
        pass


# --------------------------------------------------------------------
def main():
    res_path = os.path.join(CASE_DIR, 'results.json')
    if os.path.exists(res_path):
        os.remove(res_path)
    params = read_json(os.path.join(CASE_DIR, 'params.json'), {})
    case = read_json(os.path.join(CASE_DIR, 'case.json'), {})
    log('params: ' + json.dumps(params))

    cae = find_cae_path(params, case)
    log('open ' + cae)
    openMdb(pathName=cae)
    model = mdb.models[MODEL_NAME]

    apply_params(model, params)

    results = {}
    try:
        mp = model.rootAssembly.getMassProperties()
        if mp.get('mass') is not None:
            results['mass'] = float(mp['mass'])
    except Exception as e:
        log('mass failed: ' + str(e))

    if SAVE_CAE:
        mdb.saveAs(pathName=os.path.join(CASE_DIR, 'case_model.cae'))

    job = setup_job(model, params)
    log('submit ' + JOB_NAME)
    job.submit(consistencyChecking=OFF)
    job.waitForCompletion()

    status = check_job_status(JOB_NAME)
    log('job status: ' + status)
    if status != 'completed':
        sys.exit(1)

    odb_path = os.path.join(CASE_DIR, JOB_NAME + '.odb')
    results.update(extract_results(odb_path))
    if MAKE_IMAGES:
        try:
            save_images(odb_path)
        except Exception as e:
            log('images skipped: ' + str(e))

    results = dict((k, float(v)) for k, v in results.items() if v is not None)
    if not results:
        log('no results extracted')
        sys.exit(1)
    write_json(res_path, results)
    log('results: ' + json.dumps(results))


# noGUI 실행 시 __name__ 값에 의존하지 않도록 바로 호출한다.
try:
    main()
except SystemExit:
    raise
except Exception as _e:
    import traceback
    traceback.print_exc()
    log('FAILED: ' + str(_e))
    sys.exit(1)
