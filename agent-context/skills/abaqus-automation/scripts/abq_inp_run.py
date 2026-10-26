# -*- coding: utf-8 -*-
"""
.inp 템플릿 렌더 + Abaqus 솔버 실행 + 완료 판정 (tool 키: python, 플랫폼 venv)
  실행: python abq_inp_run.py [--template plate_template.inp] [--abaqus abaqus] [--cpus 4] [--job case]

결과 추출은 하지 않는다. 스터디 어댑터의 post 단계에서
  abaqus python abq_odb_extract.py --odb case.odb
를 실행해 results.json 을 만든다. 이 스크립트가 실패하면(종료코드 1) post 는 실행되지 않는다.

템플릿 치환 두 가지(둘 다 적용됨):
  1) {{name}} 플레이스홀더 -> params[name]
  2) *PARAMETER 블록의 'name = value' 줄 -> params[name] (본문에서 <name> 으로 참조)
params 키 중 '_' 로 시작하는 키는 제어용(_ncpus 등)으로 치환에서 제외.
py2/py3 겸용으로 작성(abaqus python 에서도 렌더 부분은 동작).
"""
from __future__ import print_function

import argparse
import glob
import json
import os
import re
import subprocess
import sys

CASE_DIR = os.getcwd()
PLACEHOLDER = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')


def log(msg):
    print('[abq_inp_run] ' + str(msg))
    sys.stdout.flush()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    f = open(path, 'r')
    try:
        return json.load(f)
    finally:
        f.close()


def fmt(v):
    if isinstance(v, bool):
        return '1' if v else '0'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)          # 전체 정밀도 유지 (inp 는 자유형식이라 폭 제약 없음)
    return str(v)


def render(text, params):
    values = dict((k, v) for k, v in params.items() if not str(k).startswith('_'))

    # 1) 플레이스홀더
    def rep(mo):
        key = mo.group(1)
        if key not in values:
            raise KeyError('placeholder {{' + key + '}} has no value in params.json')
        return fmt(values[key])
    text = PLACEHOLDER.sub(rep, text)

    # 2) *PARAMETER 블록
    out, in_param, replaced = [], False, set()
    assign = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$')
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('*') and not s.startswith('**'):
            in_param = s.upper().startswith('*PARAMETER')
            out.append(line)
            continue
        if in_param:
            mo = assign.match(line)
            if mo and mo.group(2) in values:
                line = mo.group(1) + mo.group(2) + mo.group(3) + fmt(values[mo.group(2)])
                replaced.add(mo.group(2))
        out.append(line)
    if replaced:
        log('*PARAMETER replaced: ' + ', '.join(sorted(replaced)))
    return '\n'.join(out) + '\n'


def find_template(arg, case):
    if arg:
        return os.path.abspath(arg)
    for name in (case or {}).get('inputs', []):
        if str(name).lower().endswith('.inp'):
            return os.path.join(CASE_DIR, name)
    cands = [c for c in glob.glob(os.path.join(CASE_DIR, '*.inp')) if 'template' in os.path.basename(c).lower()]
    if len(cands) == 1:
        return cands[0]
    raise RuntimeError('template .inp not found (use --template)')


def tail_text(path, nbytes=200000):
    if not os.path.exists(path):
        return ''
    f = open(path, 'rb')
    try:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - nbytes))
        return f.read().decode('latin-1')
    finally:
        f.close()


def check_job_status(job):
    sta = tail_text(os.path.join(CASE_DIR, job + '.sta'))
    lg = tail_text(os.path.join(CASE_DIR, job + '.log'))
    if 'HAS COMPLETED SUCCESSFULLY' in sta:
        return 'completed'
    status = 'unknown'
    if 'HAS NOT BEEN COMPLETED' in sta or 'exited with error' in lg or 'exited with an error' in lg:
        status = 'failed'
    elif 'COMPLETED' in lg:
        status = 'completed'
    if status != 'completed':
        for ext in ('.msg', '.dat'):
            for line in tail_text(os.path.join(CASE_DIR, job + ext)).splitlines():
                if '***ERROR' in line:
                    log(ext + ': ' + line.strip())
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--template', default=None)
    ap.add_argument('--abaqus', default=os.environ.get('ABAQUS_CMD', 'abaqus'))
    ap.add_argument('--cpus', type=int, default=None)
    ap.add_argument('--job', default='case')
    ap.add_argument('--render-only', action='store_true')
    a = ap.parse_args()

    res_path = os.path.join(CASE_DIR, 'results.json')
    if os.path.exists(res_path):
        os.remove(res_path)
    params = read_json(os.path.join(CASE_DIR, 'params.json'), {})
    case = read_json(os.path.join(CASE_DIR, 'case.json'), {})

    tpl = find_template(a.template, case)
    f = open(tpl, 'r')
    try:
        text = f.read()
    finally:
        f.close()
    inp = os.path.join(CASE_DIR, a.job + '.inp')
    if os.path.abspath(inp) == os.path.abspath(tpl):
        raise RuntimeError('job name collides with template name')
    f = open(inp, 'w')
    try:
        f.write(render(text, params))
    finally:
        f.close()
    log('rendered ' + inp)
    if a.render_only:
        return

    cpus = a.cpus or int(params.get('_ncpus', 1))
    cmd = [a.abaqus, 'job=' + a.job, 'input=' + a.job + '.inp', 'cpus=' + str(cpus),
           'interactive', 'ask_delete=OFF']
    log('run: ' + ' '.join(cmd))
    # Windows 의 abaqus 는 .bat 이므로 셸 경유
    rc = subprocess.call(cmd, cwd=CASE_DIR, shell=(os.name == 'nt'))
    log('abaqus return code: ' + str(rc) + ' (informational; status is judged from .sta/.log)')
    st = check_job_status(a.job)
    log('job status: ' + st)
    if st != 'completed':
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log('FAILED: ' + str(e))
        sys.exit(1)
