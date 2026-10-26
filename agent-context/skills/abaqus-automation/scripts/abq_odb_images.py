# -*- coding: utf-8 -*-
"""
ODB 이미지 저장 템플릿 (tool 키: abaqus_cae)
  실행: abaqus cae noGUI=abq_odb_images.py -- case.odb
  (인자가 없으면 케이스 폴더의 유일한 .odb 사용)

images/<name>.png 를 SHOTS 목록대로 저장한다. results.json 은 건드리지 않는다
(이미지는 선택 출력이므로, 하나라도 실패해도 종료코드 0 으로 끝낸다. 전부 실패하면 1).
py2/py3 겸용.
"""
from __future__ import print_function

import os
import sys

from abaqus import *            # noqa: F401,F403
from abaqusConstants import *   # noqa: F401,F403
from caeModules import *        # noqa: F401,F403

CASE_DIR = os.getcwd()

# (파일명, 변수, 위치, refinement, 뷰, 범례 (min, max) 또는 None)
# DOE 케이스끼리 비교하려면 범례 범위를 고정한다.
SHOTS = [
    ('mises_iso', 'S', INTEGRATION_POINT, (INVARIANT, 'Mises'), 'Iso', None),
    ('mises_front', 'S', INTEGRATION_POINT, (INVARIANT, 'Mises'), 'Front', None),
    ('umag_iso', 'U', NODAL, (INVARIANT, 'Magnitude'), 'Iso', None),
]
IMAGE_SIZE = (1600, 1000)
DEFORM_SCALE = 1.0      # 실제 변형. 작은 변형을 강조하려면 키운다.


def log(msg):
    print('[abq_odb_images] ' + str(msg))
    sys.stdout.flush()


def script_args():
    argv = list(sys.argv)
    return argv[argv.index('--') + 1:] if '--' in argv else []   # TODO(사내검증): '--' 유지 여부


def find_odb():
    for a in script_args():
        if a.lower().endswith('.odb'):
            return os.path.abspath(a)
    odbs = [f for f in os.listdir(CASE_DIR) if f.lower().endswith('.odb')]
    if len(odbs) == 1:
        return os.path.join(CASE_DIR, odbs[0])
    raise RuntimeError('specify odb path after --')


def main():
    odb_path = find_odb()
    img_dir = os.path.join(CASE_DIR, 'images')
    if not os.path.isdir(img_dir):
        os.makedirs(img_dir)

    odb = session.openOdb(name=odb_path)
    names = list(session.viewports.keys())
    vp = session.viewports[names[0]] if names else session.Viewport(name='Viewport: 1')
    vp.setValues(displayedObject=odb)

    last_step = odb.steps[list(odb.steps.keys())[-1]]
    vp.odbDisplay.setFrame(step=len(odb.steps) - 1, frame=len(last_step.frames) - 1)
    vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
    vp.odbDisplay.commonOptions.setValues(deformationScaling=UNIFORM, uniformScaleFactor=DEFORM_SCALE)
    vp.viewportAnnotationOptions.setValues(triad=ON, legend=ON, title=OFF, state=ON, compass=OFF)
    session.printOptions.setValues(vpDecorations=OFF, reduceColors=False)
    session.pngOptions.setValues(imageSize=IMAGE_SIZE)

    ok = 0
    for fname, label, pos, refine, view, rng in SHOTS:
        try:
            vp.odbDisplay.setPrimaryVariable(variableLabel=label, outputPosition=pos, refinement=refine)
            if rng is None:
                vp.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)
            else:
                vp.odbDisplay.contourOptions.setValues(minAutoCompute=OFF, minValue=rng[0],
                                                       maxAutoCompute=OFF, maxValue=rng[1])
            vp.view.setValues(session.views[view])
            vp.view.fitView()
            session.printToFile(fileName=os.path.join(img_dir, fname), format=PNG, canvasObjects=(vp,))
            ok += 1
            log('saved images/' + fname + '.png')
        except Exception as e:
            log('failed ' + fname + ': ' + str(e))
    odb.close()
    if ok == 0:
        sys.exit(1)


try:
    main()
except SystemExit:
    raise
except Exception as _e:
    import traceback
    traceback.print_exc()
    log('FAILED: ' + str(_e))
    sys.exit(1)
