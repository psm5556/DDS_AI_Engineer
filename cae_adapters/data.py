"""불러온 데이터 어댑터: 표나 기존 해석 폴더에서 가져온 스터디. 해석 방법이 없으므로 돌릴 수 없고, 나중에 연결할 수 있다.

mock이나 script를 빌려 쓰면 가짜 솔버가 돌거나 'tool' 오류가 나므로 따로 둔다. 원본 해석 폴더가 기록된 케이스는
Output 추가 때 추출 단계를 그 폴더에 돌릴 수 있어야 해서 extract는 스크립트 어댑터의 것을 그대로 쓴다.
"""
from .base import CaseOutcome
from .script import ScriptAdapter

NOT_RUNNABLE = "불러온 데이터 스터디라 해석을 돌릴 수 없습니다 → 해석 방법을 연결하세요"


class DataAdapter(ScriptAdapter):
    name = "data"
    label = "불러온 데이터"
    description = "이미 가진 표·해석 결과로 만든 스터디. 해석을 돌리지 않고 학습·Simulator·보고서에 쓴다. 해석을 이어 하려면 해석 방법을 연결한다."
    config_example = {}

    def run_case(self, params, run_dir, config, runner, tools, dry_run=False, **_):
        return CaseOutcome("failed", error=NOT_RUNNABLE)
