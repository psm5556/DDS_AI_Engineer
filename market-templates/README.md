# 마켓 시작 틀

마켓에 올릴 스킬의 예제입니다. 규격은 [docs/market-package-spec.md](../docs/market-package-spec.md)에 있습니다.

| 폴더 | 종류 | 내용 |
|---|---|---|
| `skill-text-example` | 스킬 | 지침만 있는 항목. 설치 때 확인 창이 없습니다 |
| `skill-code-example` | 스킬 · 코드 있음 | 스크립트가 있는 항목. `plugin.json` 예시 포함 |

폴더를 복사해 이름과 내용을 바꾼 뒤 마켓 > 등록에 놓거나 아래처럼 올립니다.

```powershell
python scripts/market_publish.py market-templates/skill-text-example --server http://127.0.0.1:7014 --private
```

이 폴더는 PC Agent 배포에 들어가지 않습니다. `server/tests/test_market_check.py`가 두 예제가 검사를 통과하는지 확인합니다.
