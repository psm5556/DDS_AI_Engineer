# -*- coding: utf-8 -*-
"""ts_*.csv (time,value) -> images/<key>.png  (플랫폼 venv 파이썬, tool: python).

run_case.py 가 AMEPython 에 matplotlib 이 없어 그래프를 못 그렸을 때, 같은 케이스 폴더(cwd)에서 실행한다.
기존 results.json 은 보존하고 n_plots 만 추가한다(없으면 새로 만든다).
"""
from __future__ import print_function

import csv
import glob
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def main():
    case_dir = os.getcwd()
    files = sorted(glob.glob(os.path.join(case_dir, "ts_*.csv")))
    if not files:
        print("ts_*.csv 없음")
        return 1
    images_dir = os.path.join(case_dir, "images")
    if not os.path.isdir(images_dir):
        os.makedirs(images_dir)

    n = 0
    for path in files:
        key = os.path.splitext(os.path.basename(path))[0][3:]
        t, y = [], []
        with open(path) as f:
            for row in csv.DictReader(f):
                t.append(float(row["time"]))
                y.append(float(row["value"]))
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
        ax.plot(t, y, linewidth=1.5)
        ax.set_xlabel("time [s]")
        ax.set_ylabel(key)
        ax.set_title(key)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(images_dir, key + ".png"))
        plt.close(fig)
        n += 1
        print("plot -> images/%s.png" % key)

    results_path = os.path.join(case_dir, "results.json")
    results = {}
    if os.path.isfile(results_path):
        with open(results_path) as f:
            results = json.load(f)
    results["n_plots"] = n
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
