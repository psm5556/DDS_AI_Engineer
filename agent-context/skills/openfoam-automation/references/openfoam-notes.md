# OpenFOAM 참고 (함수 객체, foamDictionary, WSL)

## controlDict 함수 객체 예시 (ESI 문법 기준)
결과값은 해석 중에 함수 객체로 기록한다. 아래는 `system/controlDict`의 `functions { ... }` 안에 넣는다.
```
forces
{
    type            forces;
    libs            (forces);            // 구버전: ("libforces.so")
    writeControl    timeStep;
    writeInterval   1;
    patches         (body);
    rho             rhoInf;              // 비압축 해석은 rhoInf 로 밀도 지정
    rhoInf          1.225;
    CofR            (0 0 0);
}
forceCoeffs
{
    type            forceCoeffs;
    libs            (forces);
    writeControl    timeStep;
    patches         (body);
    rho rhoInf;  rhoInf 1.225;
    CofR (0 0 0);  liftDir (0 1 0);  dragDir (1 0 0);  pitchAxis (0 0 1);
    magUInf 10;  lRef 1;  Aref 1;
}
outletAvg
{
    type            surfaceFieldValue;
    libs            (fieldFunctionObjects);
    writeControl    timeStep;
    writeFields     false;
    regionType      patch;
    name            outlet;
    operation       areaAverage;         // sum, average, weightedAreaAverage, areaIntegrate ...
    fields          (p T);
}
wallSurface
{
    type            surfaces;
    libs            (sampling);
    writeControl    writeTime;
    surfaceFormat   raw;                 // of_extract.py --surface-raw 로 fields/*.npz
    fields          (p);
    interpolationScheme cellPoint;
    surfaces
    {
        wall { type patch; patches (body); interpolate true; }
    }
}
```
- Foundation(openfoam.org) 계열은 `#includeFunc patchAverage(name=outlet, p)` 같은 짧은 형식과 다른 키 이름을 쓴다. TODO(사내검증): 사내 배포판으로 위 블록을 한 번 실행해 `log.<solver>`에 경고가 없는지 확인한다.
- 출력: `postProcessing/forces/0/force.dat`(ESI v2006+) 또는 `forces.dat`(구형), `postProcessing/forceCoeffs/0/coefficient.dat`, `postProcessing/outletAvg/0/surfaceFieldValue.dat`.
- 해석 후에 따로 계산하려면 `postProcess -func "patchAverage(name=outlet,p)" -latestTime`(org v11은 `foamPostProcess`). 힘처럼 물성이 필요한 함수는 `simpleFoam -postProcess -func forces -latestTime`(ESI) 형식을 쓴다.

## foamDictionary
```
foamDictionary system/controlDict -entry endTime -value            # 값 읽기
foamDictionary system/controlDict -entry endTime -set 1000         # 값 쓰기
foamDictionary 0/U -entry boundaryField/inlet/value -set "uniform (5 0 0)"
foamDictionary 0/U -entry boundaryField -keywords                  # 하위 키 목록
foamDictionary system/fvSolution -entry SIMPLE/residualControl -add "{ p 1e-4; U 1e-5; }"   # TODO(사내검증): -add 문법
```
- 엔트리 구분자는 `/`이다. 값에 공백·괄호가 있으면 따옴표로 감싼다(`of_run.sh`가 처리).

## WSL
| 작업 | 명령 |
|---|---|
| Windows → WSL 경로 | `wslpath -u 'C:\work\case1'` → `/mnt/c/work/case1` |
| WSL → Windows 경로 | `wslpath -w /home/u/case1` → `\\wsl.localhost\Ubuntu\home\u\case1` |
| 배포판 지정 실행 | `wsl -d Ubuntu-22.04 bash of_run.sh --np 4` |
| 환경변수 전달 | Windows 쪽 `WSLENV=DDS_CASE_DIR/p` (경로 변환 포함) |
| 자원 제한 | `%USERPROFILE%\.wslconfig`의 `[wsl2] memory=32GB processors=16` |

- `wsl`은 Windows cwd를 `/mnt/<드라이브>/...`로 바꿔 시작한다. UNC 네트워크 경로(`\\server\share`)는 변환되지 않을 수 있으니 로컬 디스크에서 실행한다.
- 설치 예(Ubuntu, ESI): `curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash` 후 `sudo apt install openfoam2312-default`. Foundation: `sudo sh -c "wget -O - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"` 등 공식 안내를 따른다. TODO(사내검증): 사내망 설치 방법.

## 솔버 로그 형식 (of_extract.py가 읽는 줄)
```
Time = 100            (org v11: Time = 100s)
smoothSolver:  Solving for Ux, Initial residual = 1.2e-03, Final residual = 5e-05, No Iterations 3
GAMG:  Solving for p, Initial residual = 3.4e-03, Final residual = 2e-05, No Iterations 12
SIMPLE solution converged in 287 iterations
End
```
- 시간 스텝마다 필드별 **첫** 초기 잔차를 쓴다(비직교 보정 반복의 두 번째 이후 값은 무시).
- 어떤 필드가 중간에 풀리지 않으면 `final_residual_<필드>`는 마지막으로 풀린 시점 값이다.
