"""動画 -> MIDI 変換のテスト。

MIDI から検証用の動画を描き（render_demo.py）、それを変換して（video2midi.py）、元の MIDI と比べる（evaluate.py）。

使い方:
    pip install -r tools/video2midi/requirements.txt    # ffmpeg もあると H.264 で圧縮した動画で試せる
    python tools/video2midi/test_video2midi.py          # 2〜3分。動画と結果は tools/video2midi/out/ に残る
    QUICK=1 python tools/video2midi/test_video2midi.py  # 重いケース（1080p60）を省く
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mido  # noqa: E402

from evaluate import evaluate  # noqa: E402
from keyboard import name_of, name_to_midi, parse_range  # noqa: E402
from make_test_song import make_song  # noqa: E402
from midi_util import Note, load_notes, write_notes  # noqa: E402
from render_demo import render  # noqa: E402

OUT = HERE / 'out'
OUT.mkdir(exist_ok=True)
QUICK = os.environ.get('QUICK') == '1'

results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + ('  ' + str(detail) if detail else ''), flush=True)


def run_convert(video, mid, *args):
    cmd = [sys.executable, str(HERE / 'video2midi.py'), str(video), '-o', str(mid), *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    log = p.stderr
    return p.returncode, log


def short(r):
    keys = ('ref', 'est', 'matched', 'f1', 'onset_abs_mean_ms', 'onset_p95_ms', 'offset_abs_median_ms', 'part_accuracy')
    return {k: (round(r[k], 3) if isinstance(r.get(k), float) else r.get(k)) for k in keys}


def case(name, notes, names, render_kw, conv_args=(), expect=None):
    """描画 -> 変換 -> 比較。expect: dict(f1=, p95=, part=, range=, parts=)"""
    expect = expect or {}
    src = OUT / f'{name}.src.mid'
    vid = OUT / f'{name}.mp4'
    est = OUT / f'{name}.out.mid'
    write_notes(str(src), notes, names)
    t = time.time()
    info = render(notes, str(vid), quiet=True, **render_kw)
    t_r = time.time() - t
    t = time.time()
    code, log = run_convert(vid, est, *conv_args)
    t_c = time.time() - t
    if code != 0:
        check(f'{name}: 変換が終わる', False, log.strip().splitlines()[-1:] if log else code)
        return None, log
    ref, _ = load_notes(str(src))
    got, got_names = load_notes(str(est))
    r = evaluate(ref, got, 0.05)
    print(f'  ({name}: 描画 {t_r:.1f}s, 変換 {t_c:.1f}s)  {short(r)}')
    if 'range' in expect:
        kb = info['kb']
        want = f'{name_of(kb.lo)}〜{name_of(kb.hi)}'
        check(f'{name}: 音域の自動推定 {want}', want in log, [ln for ln in log.splitlines() if ln.startswith('鍵盤')])
    if 'f1' in expect:
        check(f'{name}: F1 >= {expect["f1"]}', r['f1'] >= expect['f1'], f'{r["f1"]:.3f}')
    if 'p95' in expect:
        check(f'{name}: 開始時刻の誤差 95% <= {expect["p95"]}ms', r.get('onset_p95_ms', 1e9) <= expect['p95'],
              f'{r.get("onset_p95_ms", 0):.1f}ms')
    if 'part' in expect:
        check(f'{name}: パートの正解率 >= {expect["part"]}', r.get('part_accuracy', 0) >= expect['part'],
              f'{r.get("part_accuracy", 0):.3f}')
    if 'parts' in expect:
        check(f'{name}: パート名 {expect["parts"]}', got_names == expect['parts'], got_names)
    return r, log


# ---------- 単体：音名と範囲 ----------
check('name_to_midi', [name_to_midi(s) for s in ('A0', 'C4', 'F#3', 'Bb2', 'C8')] == [21, 60, 54, 46, 108])
check('parse_range', parse_range('A0-C8') == (21, 108) and parse_range('C2〜B6') == (36, 95))
try:
    parse_range('C#2-C6')
    check('parse_range: 黒鍵の端は拒否', False)
except ValueError:
    check('parse_range: 黒鍵の端は拒否', True)

# ---------- 書き出した MIDI の形 ----------
song, names = make_song(seed=2, bars=12, bpm=97)
tmp = OUT / 'format_check.mid'
write_notes(str(tmp), [Note(60, 0, 0.5, 0), Note(60, 0.4, 1.0, 0), Note(48, 0, 1, 1)], ['右手', '左手'])
mf = mido.MidiFile(str(tmp))
tn = [m.name.encode('latin1').decode('utf-8') for tr in mf.tracks for m in tr if m.type == 'track_name']
check('MIDI: パートごとのトラックと UTF-8 のトラック名', tn == ['右手', '左手'], tn)
back, _ = load_notes(str(tmp))
same = sorted((n.s, n.e) for n in back if n.p == 60)
check('MIDI: 同じ音程の音は重ならないよう詰める', same == [(0.0, 0.4), (0.4, 1.0)], same)

# ---------- 鍵盤方式：720p 30fps、曲に合わせた音域 ----------
case('keys_720p', song, names, dict(size=(1280, 720), fps=30),
     expect=dict(range=1, f1=0.99, p95=20, part=0.98, parts=['右手', '左手']))

# ---------- 鍵盤方式：低解像度（640x360）、61鍵 ----------
case('keys_360p_61', song, names, dict(size=(640, 360), fps=30, rng=(36, 96), crf=26),
     expect=dict(range=1, f1=0.98, p95=20, part=0.97))

# ---------- 鍵盤方式：1080p 60fps、88鍵、ノイズと強い圧縮 ----------
if not QUICK:
    short_song, _ = make_song(seed=3, bars=6, bpm=131)
    case('keys_1080p60_88_noisy', short_song, names, dict(size=(1920, 1080), fps=60, rng=(21, 108), noise=6, crf=30),
         expect=dict(range=1, f1=0.98, p95=10, part=0.97))

# ---------- わざと違えた見た目（黒鍵が等間隔・赤いフェルト・左右の余白・灰色の背景・グラデーション）----------
case('keys_alt_style', song, names, dict(size=(1280, 720), fps=30, style='alt', colors=['#ffb84d', '#c792ea']),
     expect=dict(range=1, f1=0.99, p95=20, part=0.98))
case('bars_alt_style', song, names, dict(size=(1280, 720), fps=30, style='alt', key_light=False), ['--mode', 'bars'],
     expect=dict(f1=0.98, p95=8, part=0.98))

# ---------- 離したあと光が薄れて消える動画（実際の動画で見つかった）----------
# 薄れている途中で同じ鍵をまた押す連打も、つながらずに分かれること。音の終わりが伸びないこと
r, _ = case('keys_fade', song, names, dict(size=(1280, 720), fps=30, fade=0.15),
            expect=dict(f1=0.99, p95=20, part=0.99))
if r:
    check('keys_fade: 音の終わりが光の消える分だけ伸びない（中央値 <= 20ms）', r['offset_abs_median_ms'] <= 20,
          f'{r["offset_abs_median_ms"]:.1f}ms')

# ---------- 最初に題名の画面、最後にフェードアウトがある動画（--start/--end なしで、その区間を読み飛ばす）----------
r, log = case('title_fadeout', song, names, dict(size=(1280, 720), fps=30, title=2.0, fadeout=1.0, tail=1.5),
              expect=dict(range=1, f1=0.99, p95=20, part=0.99))
check('title_fadeout: 読み飛ばした区間を表示する', '読み飛ばしました' in (log or ''),
      [ln.strip() for ln in (log or '').splitlines() if '読み飛ばし' in ln])

# ---------- 曲の大半で押されたままの鍵（持続音）も、押されていない色を正しく取る ----------
held = [Note(48, 0.2, 7.0, 1), Note(48, 7.3, 12.0, 1)]       # 左手 C3 をほぼずっと
for i in range(24):
    held.append(Note(72 + [0, 2, 4, 5, 7, 5, 4, 2][i % 8], 0.5 * i, 0.5 * i + 0.4, 0))
held.append(Note(43, 3.0, 3.8, 1))
held.sort(key=lambda n: (n.s, n.p))
r, _ = case('held_key', held, names, dict(size=(1280, 720), fps=30), expect=dict(f1=0.99, part=0.99))
if r:
    est, _ = load_notes(str(OUT / 'held_key.out.mid'))
    c3 = [(round(n.s - r['shift'], 2), round(n.e - n.s, 2)) for n in est if n.p == 48]
    check('held_key: 持続音 C3 を2つの長い音として検出', len(c3) == 2 and min(d for _, d in c3) > 4, c3)

# ---------- バー方式：鍵盤が光らない動画 ----------
case('bars_nolight', song, names, dict(size=(1280, 720), fps=30, key_light=False), ['--mode', 'bars'],
     expect=dict(f1=0.98, p95=8, part=0.98))

# ---------- 鍵盤方式を光らない動画に使うと、わかるメッセージで止まる ----------
code, log = run_convert(OUT / 'bars_nolight.mp4', OUT / 'nolight_keys.out.mid')
check('光らない動画を keys で変換 -> 音が見つからないと伝える', code != 0 and '音が見つかりません' in log, log.strip().splitlines()[-1:])

# ---------- 両手が同じ色 -> 1パート ----------
case('same_color', song, names, dict(size=(1280, 720), fps=30, colors=['#7ee081', '#7ee081']),
     expect=dict(f1=0.99, parts=['ピアノ']))

# ---------- 同じ鍵を右手 -> 左手と続けて弾く（間なし）-> 色の変化で 2つの音に分ける ----------
hand = []
for i in range(8):
    b = i * 1.0
    hand.append(Note(76 + (i % 3) * 2, b, b + 0.45, 0))       # 右手 E5..
    hand.append(Note(48 - (i % 2) * 5, b, b + 0.9, 1))        # 左手 C3/G2
hand.append(Note(60, 8.0, 8.6, 0))                            # C4 を右手で
hand.append(Note(60, 8.6, 9.2, 1))                            # 続けて左手で（間なし）
hand.append(Note(64, 8.0, 9.2, 0))
hand.sort(key=lambda n: (n.s, n.p))
r, _ = case('handoff', hand, names, dict(size=(1280, 720), fps=30), expect=dict(f1=0.99, part=0.99))
if r:
    est, _ = load_notes(str(OUT / 'handoff.out.mid'))
    c4 = [(round(n.s - r['shift'], 2), n.part) for n in est if n.p == 60]
    check('handoff: C4 が右手・左手の2音に分かれる', len(c4) == 2 and c4[0][1] != c4[1][1], c4)

fails = [n for n, ok in results if not ok]
print(f'\n{len(results) - len(fails)}/{len(results)} passed')
sys.exit(1 if fails else 0)
