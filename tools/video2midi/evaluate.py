"""元の MIDI と変換した MIDI を比べて、精度を出す。

使い方: python evaluate.py 元.mid 変換結果.mid [--tol 0.05]

- 全体の時刻のずれ（動画の頭の長さなど）は、対応する音の開始時刻の差の中央値として差し引く
- 同じ音程で、開始時刻の差が tol 秒以内のものを対応づける（近いものから順に 1対1）
- 出すもの：適合率・再現率・F1、開始時刻の誤差、長さの誤差、パート（右手・左手）の正解率
"""
import argparse

import numpy as np

from midi_util import load_notes


def match(ref, est, tol, shift=0.0):
    """[(i_ref, i_est)]。est の時刻から shift を引いて比べる"""
    by_p = {}
    for j, n in enumerate(est):
        by_p.setdefault(n.p, []).append(j)
    cand = []
    for i, r in enumerate(ref):
        for j in by_p.get(r.p, []):
            d = abs((est[j].s - shift) - r.s)
            if d <= tol:
                cand.append((d, i, j))
    cand.sort()
    used_r, used_e, pairs = set(), set(), []
    for d, i, j in cand:
        if i in used_r or j in used_e:
            continue
        used_r.add(i)
        used_e.add(j)
        pairs.append((i, j))
    return pairs


def estimate_shift(ref, est, tol, max_shift=60.0):
    """全体のずれ：同じ音程の開始時刻の差をすべて集め、いちばん多くの差が集まる値（幅 2*tol の窓）"""
    by_p = {}
    for n in ref:
        by_p.setdefault(n.p, []).append(n.s)
    diffs = []
    for n in est:
        if n.p in by_p:
            d = n.s - np.array(by_p[n.p])
            diffs.append(d[np.abs(d) <= max_shift])
    if not diffs:
        return 0.0
    d = np.sort(np.concatenate(diffs))
    if not len(d):
        return 0.0
    hi = np.searchsorted(d, d + 2 * tol, side='right')
    i = int(np.argmax(hi - np.arange(len(d))))
    return float(np.median(d[i:hi[i]]))


def evaluate(ref, est, tol=0.05):
    if not ref or not est:
        return {'precision': 0, 'recall': 0, 'f1': 0, 'matched': 0, 'ref': len(ref), 'est': len(est)}
    shift = estimate_shift(ref, est, tol)
    pairs = match(ref, est, tol, shift)
    m = len(pairs)
    prec, rec = m / len(est), m / len(ref)
    f1 = 2 * prec * rec / (prec + rec) if m else 0.0
    on = np.array([(est[j].s - shift) - ref[i].s for i, j in pairs]) if pairs else np.zeros(1)
    off = np.array([(est[j].e - shift) - ref[i].e for i, j in pairs]) if pairs else np.zeros(1)
    # パートの対応：変換結果の各パートを、いちばん多く重なる元のパートに当てる
    conf = {}
    for i, j in pairs:
        conf.setdefault(est[j].part, {}).setdefault(ref[i].part, 0)
        conf[est[j].part][ref[i].part] += 1
    part_map = {ep: max(d, key=d.get) for ep, d in conf.items()}
    part_ok = sum(1 for i, j in pairs if part_map.get(est[j].part) == ref[i].part)
    return {
        'ref': len(ref), 'est': len(est), 'matched': m,
        'precision': prec, 'recall': rec, 'f1': f1,
        'shift': shift,
        'onset_mean_ms': float(np.mean(on) * 1000), 'onset_abs_mean_ms': float(np.mean(np.abs(on)) * 1000),
        'onset_p95_ms': float(np.percentile(np.abs(on), 95) * 1000),
        'offset_abs_median_ms': float(np.median(np.abs(off)) * 1000),
        'part_accuracy': part_ok / m if m else 0.0,
        'part_map': part_map,
        'missed': [ref[i] for i in range(len(ref)) if i not in {a for a, _ in pairs}],
        'extra': [est[j] for j in range(len(est)) if j not in {b for _, b in pairs}],
    }


def report(r):
    lines = [
        f'元の音 {r["ref"]} / 変換結果 {r["est"]} / 一致 {r["matched"]}',
        f'適合率 {r["precision"] * 100:.1f}%  再現率 {r["recall"] * 100:.1f}%  F1 {r["f1"] * 100:.1f}%',
    ]
    if r['matched']:
        lines += [
            f'全体のずれ {r["shift"]:+.3f} 秒（差し引いて比較）',
            f'開始時刻の誤差：平均 {r["onset_mean_ms"]:+.1f}ms、絶対値の平均 {r["onset_abs_mean_ms"]:.1f}ms、95% は {r["onset_p95_ms"]:.1f}ms 以内',
            f'終了時刻の誤差（中央値）{r["offset_abs_median_ms"]:.1f}ms',
            f'パートの正解率 {r["part_accuracy"] * 100:.1f}%',
        ]
    if r.get('missed'):
        lines.append('見落とし（最初の10個）: ' + ', '.join(map(str, r['missed'][:10])))
    if r.get('extra'):
        lines.append('余分（最初の10個）: ' + ', '.join(map(str, r['extra'][:10])))
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='元の MIDI と変換結果を比べる')
    ap.add_argument('ref')
    ap.add_argument('est')
    ap.add_argument('--tol', type=float, default=0.05, help='同じ音とみなす開始時刻の差（秒）')
    a = ap.parse_args()
    ref, _ = load_notes(a.ref)
    est, _ = load_notes(a.est)
    print(report(evaluate(ref, est, a.tol)))


if __name__ == '__main__':
    main()
