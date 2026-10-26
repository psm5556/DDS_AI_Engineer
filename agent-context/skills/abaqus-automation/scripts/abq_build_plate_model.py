# -*- coding: utf-8 -*-
"""
파라메트릭 모델 재구성 템플릿 (tool 키: abaqus_cae)
  실행: abaqus cae noGUI=abq_build_plate_model.py

구멍 뚫린 직사각 판(솔리드 돌출)을 매번 새로 만든다.
  - x=0 면 고정(Set 'FIX'), x=L 면에 인장 압력(Surface 'LOAD')
  - Set/Surface 를 좌표(getByBoundingBox)로 선택하므로 형상이 바뀌어도 안전하다.
params.json 키(모두 선택, 단위 mm-N-MPa-tonne):
  length, width, thickness, hole_radius, mesh_size, pressure, youngs_modulus, poisson, density, _ncpus
출력: results.json {mass, max_mises, max_u, rf_sum_1}, images/mises_iso.png
py2/py3 겸용.
"""
from __future__ import print_function

import json
import os
import sys

from abaqus import *            # noqa: F401,F403
from abaqusConstants import *   # noqa: F401,F403
from caeModules import *        # noqa: F401,F403
import mesh
from odbAccess import openOdb

CASE_DIR = os.getcwd()
JOB_NAME = 'plate_job'
BIG = 1.0e6


def log(msg):
    print('[abq_build_plate] ' + str(msg))
    sys.stdout.flush()


def read_params():
    p = os.path.join(CASE_DIR, 'params.json')
    if not os.path.exists(p):
        return {}
    f = open(p, 'r')
    try:
        return json.load(f)
    finally:
        f.close()


def bbox(seq, xmin=-BIG, xmax=BIG, ymin=-BIG, ymax=BIG, zmin=-BIG, zmax=BIG):
    return seq.getByBoundingBox(xMin=xmin, yMin=ymin, zMin=zmin, xMax=xmax, yMax=ymax, zMax=zmax)


def build(prm):
    L = float(prm.get('length', 200.0))
    W = float(prm.get('width', 100.0))
    T = float(prm.get('thickness', 5.0))
    R = float(prm.get('hole_radius', 15.0))
    h = float(prm.get('mesh_size', 5.0))
    pr = float(prm.get('pressure', 100.0))
    E = float(prm.get('youngs_modulus', 210000.0))
    nu = float(prm.get('poisson', 0.3))
    rho = float(prm.get('density', 7.85e-9))
    if not (0.0 < R < 0.5 * min(L, W)):
        raise ValueError('hole_radius out of range')

    Mdb()  # 새 모델 데이터베이스
    m = mdb.Model(name='Plate')
    if 'Model-1' in mdb.models.keys():
        del mdb.models['Model-1']

    # 형상
    s = m.ConstrainedSketch(name='__profile__', sheetSize=2.0 * max(L, W))
    s.rectangle(point1=(0.0, 0.0), point2=(L, W))
    s.CircleByCenterPerimeter(center=(0.5 * L, 0.5 * W), point1=(0.5 * L + R, 0.5 * W))
    p = m.Part(name='Plate', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=T)
    del m.sketches['__profile__']

    # 재료, 단면
    mat = m.Material(name='Steel')
    mat.Elastic(table=((E, nu),))
    mat.Density(table=((rho,),))
    m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)
    p.SectionAssignment(region=p.Set(cells=p.cells, name='ALL'), sectionName='Sec')

    # 메쉬 (2차 사면체)
    p.seedPart(size=h, deviationFactor=0.1, minSizeFactor=0.1)
    p.setMeshControls(regions=p.cells, elemShape=TET, technique=FREE)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D10, elemLibrary=STANDARD),))
    p.generateMesh()
    if len(p.elements) == 0:
        raise RuntimeError('mesh failed')

    # 어셈블리, Set/Surface (좌표 기반)
    a = m.rootAssembly
    a.DatumCsysByDefault(CARTESIAN)
    inst = a.Instance(name='Plate-1', part=p, dependent=ON)
    tol = 1.0e-3
    a.Set(faces=bbox(inst.faces, xmin=-tol, xmax=tol), name='FIX')
    a.Surface(side1Faces=bbox(inst.faces, xmin=L - tol, xmax=L + tol), name='LOAD')

    # 스텝, 하중, BC
    m.StaticStep(name='Load', previous='Initial', timePeriod=1.0, initialInc=1.0, maxNumInc=100)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'E'))
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FIX'])
    m.Pressure(name='Tension', createStepName='Load', region=a.surfaces['LOAD'], magnitude=-pr)  # 음수 = 인장
    return m


def status_of(job_name):
    path = os.path.join(CASE_DIR, job_name + '.sta')
    if not os.path.exists(path):
        return 'unknown'
    f = open(path, 'rb')
    try:
        txt = f.read().decode('latin-1')
    finally:
        f.close()
    if 'HAS COMPLETED SUCCESSFULLY' in txt:
        return 'completed'
    return 'failed'


def main():
    res_path = os.path.join(CASE_DIR, 'results.json')
    if os.path.exists(res_path):
        os.remove(res_path)
    prm = read_params()
    m = build(prm)
    results = {}
    mp = m.rootAssembly.getMassProperties()
    if mp.get('mass') is not None:
        results['mass'] = float(mp['mass'])

    ncpu = int(prm.get('_ncpus', 1))
    job = mdb.Job(name=JOB_NAME, model='Plate', numCpus=ncpu, numDomains=ncpu)
    job.submit(consistencyChecking=OFF)
    job.waitForCompletion()
    st = status_of(JOB_NAME)
    log('status: ' + st)
    if st != 'completed':
        sys.exit(1)

    odb_path = os.path.join(CASE_DIR, JOB_NAME + '.odb')
    odb = openOdb(path=odb_path, readOnly=True)
    try:
        frame = odb.steps['Load'].frames[-1]
        results['max_mises'] = max(v.mises for v in frame.fieldOutputs['S'].values)
        results['max_u'] = max(v.magnitude for v in frame.fieldOutputs['U'].values)
        rf = frame.fieldOutputs['RF'].getSubset(region=odb.rootAssembly.nodeSets['FIX'])
        results['rf_sum_1'] = sum(float(v.data[0]) for v in rf.values)
    finally:
        odb.close()

    # 이미지 (실패해도 결과는 저장)
    try:
        img_dir = os.path.join(CASE_DIR, 'images')
        if not os.path.isdir(img_dir):
            os.makedirs(img_dir)
        o = session.openOdb(name=odb_path)
        vp = session.viewports[list(session.viewports.keys())[0]]
        vp.setValues(displayedObject=o)
        vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
        vp.odbDisplay.setPrimaryVariable(variableLabel='S', outputPosition=INTEGRATION_POINT,
                                         refinement=(INVARIANT, 'Mises'))
        vp.view.setValues(session.views['Iso'])
        vp.view.fitView()
        session.printOptions.setValues(vpDecorations=OFF, reduceColors=False)
        session.pngOptions.setValues(imageSize=(1600, 1000))
        session.printToFile(fileName=os.path.join(img_dir, 'mises_iso'), format=PNG, canvasObjects=(vp,))
        o.close()
    except Exception as e:
        log('image skipped: ' + str(e))

    f = open(res_path, 'w')
    try:
        json.dump(dict((k, float(v)) for k, v in results.items()), f, indent=2, sort_keys=True)
    finally:
        f.close()
    log('results: ' + json.dumps(results))


try:
    main()
except SystemExit:
    raise
except Exception as _e:
    import traceback
    traceback.print_exc()
    log('FAILED: ' + str(_e))
    sys.exit(1)
