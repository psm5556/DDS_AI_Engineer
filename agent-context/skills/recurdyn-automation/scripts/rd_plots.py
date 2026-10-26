# -*- coding: utf-8 -*-
"""
RecurDyn 곡선 그래프 -> images/*.png  (tool 키: python, 플랫폼 venv / Python 3 + matplotlib)

  python rd_plots.py Body1_Pos_Z Spring1_Force [--csv "plots/*.csv"] [--overlay]

- rd_extract.py --plot 에서도 make_plots() 를 호출한다. 키 이름은 rd_extract.py --list 로 확인한다.
- 이미지 실패는 케이스 실패가 아니다.
"""
import argparse
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _snake(s):
    return re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower() or "series"


def make_plots(series, keys, out_dir, overlay=False):
    from rd_extract import find_series
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    if overlay:
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
        for k in keys:
            t, y = find_series(series, k)
            ax.plot(t, y, linewidth=1.4, label=k)
        ax.set_xlabel("time")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "overlay.png"))
        plt.close(fig)
        print("plot -> images/overlay.png")
        return 1
    n = 0
    for k in keys:
        t, y = find_series(series, k)
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
        ax.plot(t, y, linewidth=1.4)
        ax.set_xlabel("time")
        ax.set_ylabel(k)
        ax.set_title(k)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        name = _snake(k) + ".png"
        fig.savefig(os.path.join(out_dir, name))
        plt.close(fig)
        print("plot -> images/" + name)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--csv", action="append", default=[])
    ap.add_argument("--overlay", action="store_true")
    a = ap.parse_args()
    from rd_extract import load_series
    series = load_series(a.csv or ["*.csv"])
    make_plots(series, a.keys, os.path.join(os.getcwd(), "images"), a.overlay)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[rd_plots] FAILED: " + str(e))
        sys.exit(1)
