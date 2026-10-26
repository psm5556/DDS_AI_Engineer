# -*- coding: utf-8 -*-
"""
ODB 결과 추출 템플릿 (tool 키: abaqus_python)
  실행: abaqus python abq_odb_extract.py --odb case.odb [옵션]

옵션
  --step NAME           추출 스텝 (기본: 마지막 스텝, 마지막 프레임)
  --elset NAME          max_mises 를 이 요소셋으로 한정 (어셈블리 또는 인스턴스 셋)
  --rf-nset NAME        반력 합을 구할 노드셋 (여러 번 지정 가능) -> rf_<set>_1/2/3
  --u-nset NAME         변위 최대값을 이 노드셋으로 한정 -> max_u_<set>
  --history REGION:VAR  히스토리 출력의 마지막 값/최대 절대값 (예: "Assembly ASSEMBLY:ALLIE")
  --density RHO         EVOL 필드가 있으면 mass = sum(EVOL) * RHO (단일 재료 모델용)
  --list                스텝/프레임/필드/셋/히스토리 영역 목록만 출력 (디버깅)

pre_results.json 이 있으면 합쳐서 results.json 을 쓴다(앞 단계에서 구한 mass 등).
py2/py3 겸용.
"""
from __future__ import print_function

import argparse
import json
import os
import re
import sys

from odbAccess import openOdb

CASE_DIR = os.getcwd()


def log(msg):
    print('[abq_odb_extract] ' + str(msg))
    sys.stdout.flush()


def snake(s):
    return re.sub(r'[^0-9a-z]+', '_', str(s).lower()).strip('_')


def find_set(odb, name, kind):
    """kind: 'nodeSets' | 'elementSets'. 어셈블리 -> 인스턴스 순서로 찾는다. ODB 셋 이름은 보통 대문자."""
    ra = odb.rootAssembly
    names = (name, name.upper())
    repo = getattr(ra, kind)
    for n in names:
        if n in repo.keys():
            return repo[n]
    for inst in ra.instances.values():
        repo = getattr(inst, kind)
        for n in names:
            if n in repo.keys():
                return repo[n]
    raise KeyError('%s %s not found' % (kind, name))


def list_contents(odb):
    ra = odb.rootAssembly
    log('instances: ' + ', '.join(ra.instances.keys()))
    log('assembly nodeSets: ' + ', '.join(ra.nodeSets.keys()))
    log('assembly elementSets: ' + ', '.join(ra.elementSets.keys()))
    for iname, inst in ra.instances.items():
        log('  %s nodeSets: %s' % (iname, ', '.join(inst.nodeSets.keys())))
    for sname in odb.steps.keys():
        st = odb.steps[sname]
        log('step %s: %d frames' % (sname, len(st.frames)))
        if len(st.frames):
            log('  fields: ' + ', '.join(st.frames[-1].fieldOutputs.keys()))
        for rname in st.historyRegions.keys():
            log('  history region "%s": %s' % (rname, ', '.join(st.historyRegions[rname].historyOutputs.keys())))


def max_attr(field, attr):
    best = None
    for v in field.values:
        x = getattr(v, attr)
        if x is not None and (best is None or x > best):
            best = x
    return best


def vec_sum(field):
    tot = [0.0, 0.0, 0.0]
    for v in field.values:
        for i, c in enumerate(v.data):
            tot[i] += float(c)
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--odb', default=None)
    ap.add_argument('--step', default=None)
    ap.add_argument('--elset', default=None)
    ap.add_argument('--rf-nset', action='append', default=[])
    ap.add_argument('--u-nset', action='append', default=[])
    ap.add_argument('--history', action='append', default=[])
    ap.add_argument('--density', type=float, default=None)
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()

    res_path = os.path.join(CASE_DIR, 'results.json')
    if os.path.exists(res_path) and not a.list:
        os.remove(res_path)

    odb_path = a.odb
    if not odb_path:
        odbs = [f for f in os.listdir(CASE_DIR) if f.lower().endswith('.odb')]
        if len(odbs) != 1:
            raise RuntimeError('specify --odb (found %d .odb files)' % len(odbs))
        odb_path = odbs[0]
    odb_path = os.path.abspath(odb_path)

    odb = openOdb(path=odb_path, readOnly=True)
    res = {}
    try:
        if a.list:
            list_contents(odb)
            return
        step_name = a.step or list(odb.steps.keys())[-1]
        step = odb.steps[step_name]
        frame = step.frames[-1]
        fos = frame.fieldOutputs
        res['step_time'] = float(frame.frameValue)

        if 'S' in fos.keys():
            s = fos['S']
            if a.elset:
                s = s.getSubset(region=find_set(odb, a.elset, 'elementSets'))
            # 적분점 값 기준. 절점 평균 값이 필요하면 s.getSubset(position=ELEMENT_NODAL) 후 직접 평균.
            res['max_mises'] = max_attr(s, 'mises')

        if 'U' in fos.keys():
            res['max_u'] = max_attr(fos['U'], 'magnitude')
            for ns in a.u_nset:
                sub = fos['U'].getSubset(region=find_set(odb, ns, 'nodeSets'))
                res['max_u_' + snake(ns)] = max_attr(sub, 'magnitude')

        for ns in a.rf_nset:
            if 'RF' not in fos.keys():
                raise KeyError('RF not in field output')
            tot = vec_sum(fos['RF'].getSubset(region=find_set(odb, ns, 'nodeSets')))
            for i in range(3):
                res['rf_%s_%d' % (snake(ns), i + 1)] = tot[i]

        if 'PEEQ' in fos.keys():
            res['max_peeq'] = max_attr(fos['PEEQ'], 'data')

        if a.density is not None and 'EVOL' in fos.keys():
            vol = sum(float(v.data) for v in fos['EVOL'].values)
            res['volume'] = vol
            res['mass'] = vol * a.density

        for spec in a.history:
            region, var = spec.rsplit(':', 1)
            data = step.historyRegions[region].historyOutputs[var].data   # ((t, v), ...)
            key = snake(var) + '_' + snake(region.split()[-1])
            res[key + '_last'] = float(data[-1][1])
            res[key + '_absmax'] = max(abs(float(d[1])) for d in data)
    finally:
        odb.close()

    pre = os.path.join(CASE_DIR, 'pre_results.json')
    if os.path.exists(pre):
        f = open(pre, 'r')
        try:
            base = json.load(f)
        finally:
            f.close()
        base.update(res)
        res = base

    res = dict((k, float(v)) for k, v in res.items() if v is not None)
    if not res:
        raise RuntimeError('no results extracted')
    f = open(res_path, 'w')
    try:
        json.dump(res, f, indent=2, sort_keys=True)
    finally:
        f.close()
    log('results: ' + json.dumps(res, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        log('FAILED: ' + str(e))
        sys.exit(1)
