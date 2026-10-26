# -*- coding: utf-8 -*-
"""
nCode 손상 분포 그래프 -> images/*.png  (tool 키: python, 플랫폼 venv / Python 3 + matplotlib)

  python ncode_plots.py --csv "results/*.csv" [--top 20]

  images/damage_hist.png : log10(손상) 히스토그램 (손상 0 은 제외)
  images/damage_top.png  : 손상 상위 N 엔티티 막대그래프 (손상 1 기준선 표시)
- ncode_extract.py --plot 에서도 make_plots() 를 호출한다. 이미지 실패는 케이스 실패가 아니다.
"""
import argparse
import glob
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_plots(rows, out_dir, top=20):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    n = 0
    logs = [math.log10(r[1]) for r in rows if r[1] > 0]
    if logs:
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
        ax.hist(logs, bins=40, color="#4c72b0")
        ax.axvline(0.0, color="#c44e52", linestyle="--", linewidth=1.2, label="damage = 1")
        ax.set_xlabel("log10(damage)")
        ax.set_ylabel("entities")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "damage_hist.png"))
        plt.close(fig)
        print("plot -> images/damage_hist.png")
        n += 1
    worst = sorted(rows, key=lambda r: r[1], reverse=True)[:top]
    if worst:
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
        labels = ["%d" % r[0] if r[0] is not None else "?" for r in worst]
        ax.bar(range(len(worst)), [r[1] for r in worst], color="#4c72b0")
        ax.axhline(1.0, color="#c44e52", linestyle="--", linewidth=1.2)
        ax.set_xticks(range(len(worst)))
        ax.set_xticklabels(labels, rotation=60, fontsize=8)
        ax.set_xlabel("entity id")
        ax.set_ylabel("damage")
        ax.set_title("top %d damage" % len(worst))
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "damage_top.png"))
        plt.close(fig)
        print("plot -> images/damage_top.png")
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", required=True)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    from ncode_extract import read_damage_table
    rows = []
    for p in a.csv:
        for f in sorted(glob.glob(p, recursive=True)):
            rows.extend(read_damage_table(f))
    if not rows:
        raise RuntimeError("no rows")
    make_plots(rows, os.path.join(os.getcwd(), "images"), a.top)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[ncode_plots] FAILED: " + str(e))
        sys.exit(1)
