"""変換ツールの確認用の曲を作る（乱数で作る練習曲。著作権の問題がない）。

右手：C4〜C6 のメロディ（黒鍵、同じ音の連打、16分音符の速い動き、3度の和音を含む）
左手：C2〜B3 のベースと和音（分散和音、黒鍵を含む和音）

使い方: python make_test_song.py out.mid [--seed 1] [--bars 12] [--bpm 100]
"""
import argparse
import random

from midi_util import Note, write_notes


def make_song(seed=1, bars=12, bpm=100, gap=0.12):
    rnd = random.Random(seed)
    beat = 60.0 / bpm
    notes = []

    def add(p, start_beat, len_beats, part):
        s = start_beat * beat
        e = (start_beat + len_beats * (1 - gap)) * beat
        notes.append(Note(p, s, e, part, 70 + rnd.randint(0, 40)))

    # 右手
    scale = [0, 2, 4, 5, 7, 9, 11]
    p = 72
    b = 0.0
    end = bars * 4
    while b < end - 0.01:
        r = rnd.random()
        if r < 0.15:        # 同じ音の連打（8分 x 3）
            for k in range(3):
                add(p, b + k * 0.5, 0.5, 0)
            b += 1.5
            continue
        if r < 0.30:        # 16分音符の音階
            step = rnd.choice([1, -1])
            for k in range(4):
                add(p, b + k * 0.25, 0.25, 0)
                p = _step_scale(p, step, scale, 60, 84)
            b += 1.0
            continue
        length = rnd.choice([0.5, 0.5, 1.0, 1.0, 1.5, 2.0])
        length = min(length, end - b)
        pp = p + (1 if rnd.random() < 0.18 and (p + 1) % 12 in (1, 3, 6, 8, 10) else 0)   # 黒鍵
        add(pp, b, length, 0)
        if rnd.random() < 0.2:   # 3度下を重ねる
            add(pp - rnd.choice([3, 4]), b, length, 0)
        b += length
        p = _step_scale(p, rnd.choice([-2, -1, 1, 2, 3]), scale, 60, 84)

    # 左手（1小節ごと）
    roots = [48, 43, 45, 41, 50, 44, 46, 39]   # C3 G2 A2 F2 D3 G#2 A#2 D#2
    for bar in range(bars):
        root = rnd.choice(roots)
        third = root + rnd.choice([3, 4])
        fifth = root + 7
        kind = rnd.random()
        b0 = bar * 4
        if kind < 0.35:          # 全音符ベース＋2分の和音
            add(root - 12 if root - 12 >= 36 else root, b0, 4, 1)
            add(third, b0, 2, 1); add(fifth, b0, 2, 1)
            add(third, b0 + 2, 2, 1); add(fifth, b0 + 2, 2, 1)
        elif kind < 0.7:         # 分散和音（8分）
            seqn = [root, fifth, third + 12 if third + 12 <= 59 else third, fifth]
            for k in range(8):
                add(seqn[k % 4], b0 + k * 0.5, 0.5, 1)
        else:                    # 4分の和音
            for k in range(4):
                add(root, b0 + k, 1, 1); add(third, b0 + k, 1, 1); add(fifth, b0 + k, 1, 1)
    notes.sort(key=lambda n: (n.s, n.p))
    return notes, ['右手', '左手']


def _step_scale(p, step, scale, lo, hi):
    for _ in range(abs(step)):
        q = p + (1 if step > 0 else -1)
        while q % 12 not in scale:
            q += 1 if step > 0 else -1
        p = q
    if p < lo or p > hi:
        p = 72
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('out')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--bars', type=int, default=12)
    ap.add_argument('--bpm', type=float, default=100)
    a = ap.parse_args()
    notes, names = make_song(a.seed, a.bars, a.bpm)
    write_notes(a.out, notes, names)
    print(f'{a.out}: {len(notes)}音')


if __name__ == '__main__':
    main()
