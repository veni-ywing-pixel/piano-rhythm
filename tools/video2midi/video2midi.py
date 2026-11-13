"""鍵盤＋ノーツが降ってくる動画（Synthesia 風）から MIDI を作る。

使い方:
    python video2midi.py 動画.mp4 --check check.png      # 鍵盤の位置合わせを確認する画像だけ作る
    python video2midi.py 動画.mp4 -o 曲.mid              # 変換する（鍵盤が光る動画）
    python video2midi.py 動画.mp4 -o 曲.mid --mode bars  # 鍵盤が光らない動画（降ってくるバーを読む）

方式:
    keys（既定）: 鍵盤の色の変化で押鍵を検出する。白鍵は黒鍵より下の部分、黒鍵は黒鍵の部分の色を見る。
                  押されていない色は、動画全体での各鍵の色の中央値を使う。
    bars        : 鍵盤の少し上の帯で、降ってくるバーの下端（音の始まり）と上端（音の終わり）を追う。
                  1つの端を何フレームも見るので、時刻はフレームの間隔より細かく求まる。
    どちらも、押されたときの色を k-means で分けてパート（右手・左手）にする。

入力は自分で用意した動画ファイルを使うこと（YouTube からのダウンロード機能はない）。
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

from keyboard import Keyboard, detect_keyboard, draw_overlay, is_black, name_of, parse_range, sample_span
from midi_util import Note, write_notes


SAMPLES = 25                                         # 中央値フレームを作る枚数
LAB8 = np.array([100 / 255, 1.0, 1.0], np.float32)   # 8bit の Lab の L を 0〜100 に


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 動画の読み込み

class Video:
    def __init__(self, path):
        if not os.path.exists(path):
            raise SystemExit(f'動画が見つかりません: {path}')
        self.path = path
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise SystemExit(f'動画を開けませんでした: {path}')
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        self._cache = {}

    def frames(self, f0, f1):
        """f0 から f1（含まない）までのフレームを順に返す"""
        cap = cv2.VideoCapture(self.path)
        if f0 > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
        f = f0
        while f < f1:
            ok, img = cap.read()
            if not ok:
                break
            yield f, img
            f += 1
        cap.release()

    def sample(self, f0, f1, count):
        """f0〜f1 から等間隔に count 枚（一度読んだものは使い回す）"""
        key = (f0, f1, count)
        if key not in self._cache:
            self._cache[key] = self._sample(f0, f1, count)
        return self._cache[key]

    def _sample(self, f0, f1, count):
        cap = cv2.VideoCapture(self.path)
        out = []
        for f in np.linspace(f0, max(f0, f1 - 1), count).round().astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
            ok, img = cap.read()
            if ok:
                out.append(img)
        cap.release()
        if not out:
            raise SystemExit('動画のフレームを読めませんでした')
        return out


def median_image(imgs):
    st = np.stack(imgs)
    out = np.empty(st.shape[1:], np.uint8)
    for r in range(0, st.shape[1], 64):
        out[r:r + 64] = np.median(st[:, r:r + 64], axis=0).round().astype(np.uint8)
    return out


def to_lab(bgr):
    """BGR（0〜255 の float/uint8、形は自由）を CIELAB（L 0〜100）に"""
    a = np.asarray(bgr, np.float32)
    shp = a.shape
    lab = cv2.cvtColor((a.reshape(1, -1, 3) / 255.0).astype(np.float32), cv2.COLOR_BGR2Lab)
    return lab.reshape(shp)


def lab_to_hex(lab):
    bgr = cv2.cvtColor(np.asarray(lab, np.float32).reshape(1, 1, 3), cv2.COLOR_Lab2BGR).reshape(3)
    b, g, r = (np.clip(bgr, 0, 1) * 255).round().astype(int)
    return f'#{r:02x}{g:02x}{b:02x}'


# ---------------------------------------------------------------- 鍵盤方式（keys）

def key_rows(kb):
    """色を取る縦の範囲（白鍵用, 黒鍵用）"""
    wz = kb.bottom - kb.bk_bottom
    bz = kb.bk_bottom - kb.top
    white = (int(round(kb.bk_bottom + 0.2 * wz)), int(round(kb.bottom - 0.2 * wz)))
    black = (int(round(kb.top + 0.25 * bz)), int(round(kb.top + 0.8 * bz)))
    if white[1] <= white[0]:
        white = (white[0], white[0] + 1)
    if black[1] <= black[0]:
        black = (black[0], black[0] + 1)
    return white, black


def keys_pass(video, kb, f0, f1):
    """各フレーム・各鍵の色（Lab）を集める。戻り値 (フレーム番号 (F,), 色 (F, K, 3))"""
    (wy0, wy1), (by0, by1) = key_rows(kb)
    X0, X1 = int(np.floor(kb.x0)), int(np.ceil(kb.x1))
    X1 = min(X1, video.W)
    xa, xb, isb = [], [], []
    for p in kb.keys:
        a, b = sample_span(kb, p)
        a, b = int(round(a)) - X0, int(round(b)) - X0
        a = int(np.clip(a, 0, X1 - X0 - 1))
        b = int(np.clip(max(b, a + 1), a + 1, X1 - X0))
        xa.append(a), xb.append(b), isb.append(is_black(p))
    xa, xb, isb = np.array(xa), np.array(xb), np.array(isb)
    cols, idx = [], []
    t0 = last = time.time()
    total = f1 - f0
    for f, img in video.frames(f0, f1):
        wrow = img[wy0:wy1, X0:X1].astype(np.float32).mean(axis=0)
        brow = img[by0:by1, X0:X1].astype(np.float32).mean(axis=0)
        cw = np.vstack([np.zeros((1, 3), np.float32), np.cumsum(wrow, axis=0)])
        cb = np.vstack([np.zeros((1, 3), np.float32), np.cumsum(brow, axis=0)])
        mw = (cw[xb] - cw[xa]) / (xb - xa)[:, None]
        mb = (cb[xb] - cb[xa]) / (xb - xa)[:, None]
        cols.append(np.where(isb[:, None], mb, mw))
        idx.append(f)
        if time.time() - last > 3:
            last = time.time()
            log(f'  読み込み中 {f - f0}/{total} フレーム（{(f - f0) / (last - t0):.0f} fps）')
    if not cols:
        raise SystemExit('フレームを読めませんでした')
    bgr = np.stack(cols)
    return np.array(idx), to_lab(bgr)


def keys_segments(lab, kb, thr_on, thr_off):
    """各鍵の「押されている区間」を求める。戻り値 [(鍵の番号, 開始, 終了（含まない）)]、基準色、色差"""
    ref = np.median(lab, axis=0)                     # 押されていない色（各鍵）
    # 動画の半分以上押されたままの鍵は、中央値が「押された色」になってしまう。
    # 同じ種類の鍵（白鍵どうし・黒鍵どうし）の標準の色から外れていたら、標準に近いフレームだけで取り直す
    blk = np.array([is_black(p) for p in kb.keys])
    for sel in (blk, ~blk):
        if not sel.any():
            continue
        g = np.median(ref[sel], axis=0)
        for k in np.nonzero(sel)[0]:
            if np.linalg.norm(ref[k] - g) > thr_off:
                close = np.linalg.norm(lab[:, k] - g, axis=1) < thr_off
                ref[k] = np.median(lab[close, k], axis=0) if close.sum() >= 5 else g
    d = np.linalg.norm(lab - ref[None], axis=2)      # (F, K)
    segs = []
    for k in range(d.shape[1]):
        cand = d[:, k] > thr_off
        m = np.concatenate([[False], cand, [False]])
        dd = np.diff(m.astype(np.int8))
        for s, e in zip(np.nonzero(dd == 1)[0], np.nonzero(dd == -1)[0]):
            on = np.nonzero(d[s:e, k] > thr_on)[0]
            if len(on):
                segs.append((k, s + int(on[0]), e))
    return segs, ref, d


# ---------------------------------------------------------------- バー方式（bars）

def bar_columns(kb, X0, X1):
    """バーを見る横の範囲（帯の左端からの画素の番号）。

    白鍵：黒鍵と重ならない部分 [xa, xb)
    黒鍵：黒鍵の中央 40% の幅（どの黒鍵も同じ画素数）の画素の番号 (黒鍵の数, 幅)
    """
    Wb = X1 - X0
    xa, xb, wkeys, bkeys, bidx = [], [], [], [], []
    wb = max(2, int(round(kb.bw * 0.4)))
    for k, p in enumerate(kb.keys):
        if is_black(p):
            a = int(round(kb.center(p) - wb / 2)) - X0
            bidx.append(np.clip(np.arange(a, a + wb), 0, Wb - 1))
            bkeys.append(k)
        else:
            a, b = kb.white_zone(p)
            a = int(np.clip(round(a) - X0, 0, Wb - 1))
            b = int(np.clip(round(b) - X0, a + 1, Wb))
            xa.append(a), xb.append(b), wkeys.append(k)
    return (np.array(wkeys), np.array(xa), np.array(xb),
            np.array(bkeys, int), np.array(bidx, int).reshape(len(bkeys), wb))


def bars_pass(video, kb, f0, f1, band, de_thr=18.0, min_run=3):
    """帯の中のバーの下端・上端を集める"""
    y0, y1 = band
    X0, X1 = int(np.floor(kb.x0)), min(video.W, int(np.ceil(kb.x1)))
    wkeys, wxa, wxb, bkeys, bidx = bar_columns(kb, X0, X1)
    K = len(kb.keys)
    xa = np.zeros(K, int)
    xb = np.zeros(K, int)
    xa[wkeys], xb[wkeys] = wxa, wxb
    if len(bkeys):
        xa[bkeys], xb[bkeys] = bidx[:, 0], bidx[:, -1] + 1
    log('  背景を作っています…')
    bg = cv2.cvtColor(median_image([im[y0:y1, X0:X1] for im in video.sample(f0, f1, SAMPLES)]), cv2.COLOR_BGR2Lab)
    bg = bg.astype(np.float32)
    wts = np.array([[(100 / 255) ** 2, 1.0, 1.0]], np.float32)   # 8bit の Lab を ΔE の2乗に直す重み
    Hb = y1 - y0
    on_obs, off_obs = [], []   # (f, k, y_abs, color) / (f, k, y_abs)
    t0 = last = time.time()
    total = f1 - f0
    for f, img in video.frames(f0, f1):
        lab = cv2.cvtColor(img[y0:y1, X0:X1], cv2.COLOR_BGR2Lab).astype(np.float32)
        diff = cv2.subtract(lab, bg)
        de2 = cv2.transform(cv2.multiply(diff, diff), wts)       # ΔE の2乗 (Hb, Wb)
        mask = de2 > de_thr * de_thr
        cs = np.concatenate([np.zeros((Hb, 1), np.int32), np.cumsum(mask, axis=1, dtype=np.int32)], axis=1)
        B = np.zeros((Hb, K), bool)
        # 白鍵：範囲の半分以上が背景と違えばバーがある
        B[:, wkeys] = (cs[:, wxb] - cs[:, wxa]) > 0.5 * (wxb - wxa)[None, :]
        if len(bkeys):
            # 黒鍵：幅全体が一様に背景と違うこと（隣の白鍵2本のバーと、その間のにじみを黒鍵と間違えないため）
            sub = de2[:, bidx]                      # (Hb, 黒鍵の数, 幅)
            mn, mean = sub.min(axis=2), sub.mean(axis=2)
            B[:, bkeys] = (mn > de_thr * de_thr) & (mn >= 0.36 * mean)
        P = np.vstack([np.zeros((1, B.shape[1]), bool), B, np.zeros((1, B.shape[1]), bool)])
        D = np.diff(P.astype(np.int8), axis=0)       # (Hb+1, K)
        sk, sy = np.nonzero(D.T == 1)                # 帯の行で、バーが始まる行（上端）
        ek, ey = np.nonzero(D.T == -1)               # バーが終わった次の行（下端）
        for k, s, e in zip(sk, sy, ey):
            if e - s < min_run:
                continue
            if e < Hb:     # 下端が帯の中にある -> 音の始まり
                ra, rb = max(s, e - 8), max(s + 1, e - 2)
                col = lab[ra:rb, xa[k]:xb[k]].reshape(-1, 3).mean(axis=0) * LAB8
                on_obs.append((f, k, y0 + e, col))
            if s > 0:      # 上端が帯の中にある -> 音の終わり
                off_obs.append((f, k, y0 + s))
        if time.time() - last > 3:
            last = time.time()
            log(f'  読み込み中 {f - f0}/{total} フレーム（{(f - f0) / (last - t0):.0f} fps）')
    return on_obs, off_obs


def estimate_pps(obs_lists, fps, max_step):
    """同じバーの端をフレームごとにつなぎ、動く速さ（px/秒）を求める。

    obs_lists: 端の観測 (フレーム, 鍵, y, …) のリストを、端の種類（下端・上端）ごとに分けたもの
    """
    slopes = []
    for obs in obs_lists:
        by_k = {}
        for o in obs:
            by_k.setdefault(o[1], []).append((o[0], o[2]))
        for lst in by_k.values():
            lst.sort()
            active, done = [], []
            for f, y in lst:
                keep = []
                for ch in active:                 # 1フレーム前に続いている鎖だけを候補にする
                    (keep if ch[-1][0] >= f - 1 else done).append(ch)
                active = keep
                best = None
                for ch in active:
                    lf, ly = ch[-1]
                    if lf == f - 1 and 0 <= y - ly <= max_step and (best is None or y - ly < best[0]):
                        best = (y - ly, ch)
                if best:
                    best[1].append((f, y))
                else:
                    active.append([(f, y)])
            for ch in done + active:
                if len(ch) >= 4:
                    fs = np.array([c[0] for c in ch], float) / fps
                    ys = np.array([c[1] for c in ch], float)
                    sl = np.polyfit(fs, ys, 1)[0]
                    if sl > 0:
                        slopes.append(sl)
    if not slopes:
        return None
    return float(np.median(slopes))


def group_times(ts, tol, min_count):
    """近い時刻をまとめる -> [(代表時刻, 要素の番号のリスト)]"""
    order = np.argsort(ts)
    groups, cur = [], [order[0]] if len(order) else []
    for i in order[1:]:
        if ts[i] - ts[cur[-1]] <= tol:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    if cur:
        groups.append(cur)
    return [(float(np.median(ts[g])), g) for g in groups if len(g) >= min_count]


# ---------------------------------------------------------------- パート（色）の分け方

def color_features(lab, black, lmean):
    L = lab[..., 0] - np.where(black, lmean[1], lmean[0])
    return np.stack([lab[..., 1], lab[..., 2], 0.5 * L], axis=-1).astype(np.float32)


def cluster_parts(feats, n_parts):
    """色の特徴を k 個に分ける。n_parts='auto' なら 1 か 2 を選ぶ。戻り値 (ラベル, 中心)"""
    N = len(feats)
    if N == 0:
        return np.zeros(0, int), np.zeros((1, 3), np.float32)
    if n_parts == 'auto':
        if N < 4:
            return np.zeros(N, int), feats.mean(axis=0, keepdims=True)
        lab2, cen2 = _kmeans(feats, 2)
        sizes = np.bincount(lab2, minlength=2) / N
        spread = np.sqrt(np.mean(np.sum((feats - cen2[lab2]) ** 2, axis=1)))
        sep = np.linalg.norm(cen2[0] - cen2[1])
        if sep >= max(10.0, 3.0 * spread) and sizes.min() >= 0.03:
            return lab2, cen2
        return np.zeros(N, int), feats.mean(axis=0, keepdims=True)
    k = int(n_parts)
    if k <= 1 or N < k:
        return np.zeros(N, int), feats.mean(axis=0, keepdims=True)
    return _kmeans(feats, k)


def _kmeans(x, k):
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.01)
    cv2.setRNGSeed(1)
    _, lab, cen = cv2.kmeans(np.ascontiguousarray(x, np.float32), k, None, crit, 8, cv2.KMEANS_PP_CENTERS)
    return lab.ravel(), cen


def nearest(feats, centers):
    return np.argmin(np.linalg.norm(feats[..., None, :] - centers, axis=-1), axis=-1)


# ---------------------------------------------------------------- 変換の本体

def convert(video, kb, mode='keys', f0=0, f1=None, n_parts='auto', thr=(25.0, 15.0), min_dur=0.05,
            band=None, pps=None, de_thr=18.0, merge_frames=2):
    """戻り値: (notes, part_names, 情報 dict)。時刻は動画の先頭からの秒"""
    f1 = video.n if f1 is None or f1 <= 0 else min(f1, video.n)
    fps = video.fps
    info = {'fps': fps}
    is_blk = np.array([is_black(p) for p in kb.keys])
    raw = []   # dict(k, s, e, lab, [frame_labs])
    if mode == 'keys':
        idx, lab = keys_pass(video, kb, f0, f1)
        segs, ref, d = keys_segments(lab, kb, *thr)
        info['frames'] = len(idx)
        for k, s, e in segs:
            inner = lab[s + 1:e - 1, k] if e - s >= 3 else lab[s:e, k]
            raw.append({'k': k, 'fs': int(idx[s]), 'fe': int(idx[e - 1]), 'lab': np.median(inner, axis=0),
                        'frames': lab[s:e, k]})
    elif mode == 'bars':
        if band is None:
            margin = max(6, int(0.03 * video.H))
            y1 = int(kb.top) - margin
            y0 = max(0, y1 - int(0.3 * kb.top))
            band = (y0, y1)
        info['band'] = band
        on_obs, off_obs = bars_pass(video, kb, f0, f1, band, de_thr)
        if not on_obs:
            raise SystemExit('降ってくるバーが見つかりませんでした。--band で帯の位置を確認してください')
        if pps is None:
            pps = estimate_pps([on_obs, off_obs], fps, (band[1] - band[0]) * 0.5)
            if pps is None:
                raise SystemExit('バーの速さを推定できませんでした。--pps で指定してください')
        info['pps'] = pps
        y_hit = kb.top
        tol = 0.03
        need = 2
        on_by_k, off_by_k = {}, {}
        for f, k, y, col in on_obs:
            col = col - np.array([0, 128, 128], np.float32)       # 8bit の Lab -> CIELAB
            on_by_k.setdefault(k, []).append((f / fps + (y_hit - y) / pps, col))
        for f, k, y in off_obs:
            off_by_k.setdefault(k, []).append(f / fps + (y_hit - y) / pps)
        for k, lst in on_by_k.items():
            ts = np.array([x[0] for x in lst])
            cols = np.array([x[1] for x in lst])
            ons = group_times(ts, tol, need)
            offs = [t for t, g in group_times(np.array(off_by_k.get(k, [])), tol, need)] if k in off_by_k else []
            offs = np.array(sorted(offs))
            for i, (t_on, g) in enumerate(ons):
                t_next = ons[i + 1][0] if i + 1 < len(ons) else np.inf
                cand = offs[(offs > t_on + 0.02) & (offs <= t_next + tol)] if len(offs) else []
                t_off = float(cand[0]) if len(cand) else min(t_next - 0.02, t_on + 0.5)
                t_off = min(t_off, t_next - 0.005)
                raw.append({'k': k, 's': t_on, 'e': t_off, 'lab': np.median(cols[g], axis=0)})
    else:
        raise SystemExit(f'--mode は keys か bars です: {mode}')

    if not raw:
        return [], [], info

    # 押されたときの色でパートに分ける
    labs = np.array([r['lab'] for r in raw])
    blk = np.array([is_blk[r['k']] for r in raw])
    lmean = (labs[~blk, 0].mean() if (~blk).any() else 0.0, labs[blk, 0].mean() if blk.any() else 0.0)
    feats = color_features(labs, blk, lmean)
    labels, centers = cluster_parts(feats, n_parts)

    notes_tmp = []   # (p, s, e, label)
    half = 0.5 / fps
    for r, lb in zip(raw, labels):
        p = kb.keys[r['k']]
        if mode == 'keys':
            fr = r['frames']
            if len(centers) > 1 and len(fr) >= 2 * merge_frames:
                # 押している途中で色（手）が変わったら、そこで別の音に分ける
                fl = nearest(color_features(fr, np.full(len(fr), is_blk[r['k']]), lmean), centers)
                runs = _label_runs(fl, merge_frames)
            else:
                runs = [(0, len(fr), lb)]
            for a, b, l in runs:
                s = (r['fs'] + a) / fps - half
                e = (r['fs'] + b - 1) / fps + half
                notes_tmp.append((p, s, e, l))
        else:
            notes_tmp.append((p, r['s'], r['e'], lb))

    notes_tmp = [n for n in notes_tmp if n[2] - n[1] >= min_dur]
    # パートの順番：平均の音が高い順（2パートなら 右手, 左手）
    k = len(centers)
    avg = [np.mean([n[0] for n in notes_tmp if n[3] == i]) if any(n[3] == i for n in notes_tmp) else -1
           for i in range(k)]
    order = sorted(range(k), key=lambda i: -avg[i])
    remap = {old: new for new, old in enumerate(order) if avg[old] >= 0}
    notes = [Note(p, s, e, remap[l]) for p, s, e, l in notes_tmp if l in remap]
    nparts = len(remap)
    names = ['右手', '左手'] if nparts == 2 else ['ピアノ'] if nparts == 1 else [f'パート{i + 1}' for i in range(nparts)]
    info['part_colors'] = [None] * nparts
    for old, new in remap.items():
        sel = labels == old
        if sel.any():
            info['part_colors'][new] = lab_to_hex(labs[sel].mean(axis=0))
    notes.sort(key=lambda n: (n.s, n.p))
    return notes, names, info


def _label_runs(fl, min_len):
    """フレームごとのラベル列を、短いゆらぎをならして区間に分ける -> [(開始, 終了, ラベル)]"""
    runs = []
    s = 0
    for i in range(1, len(fl) + 1):
        if i == len(fl) or fl[i] != fl[s]:
            runs.append([s, i, int(fl[s])])
            s = i
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for j, (a, b, l) in enumerate(runs):
            if b - a < min_len:          # 短い区間は前（先頭なら後ろ）の区間に吸収する
                if j > 0:
                    runs[j - 1][1] = b
                else:
                    runs[j + 1][0] = a
                runs.pop(j)
                changed = True
                break
        for j in range(len(runs) - 1, 0, -1):
            if runs[j][2] == runs[j - 1][2]:
                runs[j - 1][1] = runs[j][1]
                runs.pop(j)
    return [tuple(r) for r in runs]


# ---------------------------------------------------------------- コマンド

def setup_keyboard(video, a, f0, f1):
    log(f'動画: {video.W}x{video.H}, {video.fps:.2f} fps, {video.n} フレーム（{video.n / video.fps:.1f} 秒）')
    med = median_image(video.sample(f0, f1, SAMPLES))
    rng = parse_range(a.range) if a.range else None
    region = tuple(int(v) for v in a.region.split(',')) if a.region else None
    if region and len(region) != 4:
        raise SystemExit('--region は 左,上,右,下 の4つの数で指定してください')
    try:
        kb, info = detect_keyboard(med, rng, region)
    except RuntimeError as e:
        raise SystemExit(str(e))
    if a.black_bottom:
        kb = Keyboard(kb.lo, kb.hi, kb.x0, kb.x1, kb.top, kb.bottom, a.black_bottom)
    log('鍵盤: ' + kb.describe() + ('' if rng else '（音域は自動推定。違うときは --range で指定）'))
    return kb, med


def main(argv=None):
    ap = argparse.ArgumentParser(description='鍵盤＋ノーツが降ってくる動画から MIDI を作る',
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('video', help='入力する動画ファイル（mp4 など）')
    ap.add_argument('-o', '--out', help='出力する MIDI ファイル（.mid）')
    ap.add_argument('--check', metavar='PNG', help='位置合わせの確認画像を書き出して終わる')
    ap.add_argument('--mode', choices=['keys', 'bars'], default='keys', help='keys=鍵盤の点灯（既定）, bars=降ってくるバー')
    ap.add_argument('--region', help='鍵盤の位置 左,上,右,下（ピクセル）。省略すると自動検出')
    ap.add_argument('--range', help='鍵盤の音域（例 A0-C8）。省略すると黒鍵の並びから推定')
    ap.add_argument('--black-bottom', type=float, help='黒鍵の下端の y（ピクセル）')
    ap.add_argument('--start', type=float, default=0, help='この秒から処理する')
    ap.add_argument('--end', type=float, default=0, help='この秒まで処理する（0 なら最後まで）')
    ap.add_argument('--parts', default='auto', help='パート（手）の数。auto なら色から 1 か 2 を選ぶ')
    ap.add_argument('--threshold', default='25,15', help='keys: 押鍵とみなす色差（ΔE）の 入り,切れ（既定 25,15）')
    ap.add_argument('--min-dur', type=float, default=0.05, help='これより短い音は捨てる（秒）')
    ap.add_argument('--band', help='bars: バーを見る帯の 上,下 の y（ピクセル）')
    ap.add_argument('--pps', type=float, help='bars: バーの落ちる速さ（px/秒）。省略すると自動推定')
    ap.add_argument('--keep-time', action='store_true', help='動画の時刻のまま出力する（既定は最初の音を 1 秒にそろえる）')
    a = ap.parse_args(argv)
    if not a.out and not a.check:
        ap.error('-o（出力する MIDI）か --check（確認画像）を指定してください')

    video = Video(a.video)
    f0 = int(round(a.start * video.fps))
    f1 = int(round(a.end * video.fps)) if a.end > 0 else video.n
    kb, med = setup_keyboard(video, a, f0, f1)

    if a.check:
        band = tuple(int(v) for v in a.band.split(',')) if a.band else None
        if a.mode == 'bars' and band is None:
            margin = max(6, int(0.03 * video.H))
            band = (max(0, int(kb.top) - margin - int(0.3 * kb.top)), int(kb.top) - margin)
        wr, br = key_rows(kb)
        img = draw_overlay(med, kb, band, wr, br)
        cv2.imwrite(a.check, img)
        log(f'確認画像を書き出しました: {a.check}')
        log('  黄色の枠＝鍵盤、緑の線＝白鍵の境目、青い枠＝黒鍵、赤＝色を取る場所' + ('、水色の枠＝バーを見る帯' if band else ''))
        if not a.out:
            return

    thr = tuple(float(v) for v in a.threshold.split(','))
    if len(thr) == 1:
        thr = (thr[0], thr[0] * 0.6)
    band = tuple(int(v) for v in a.band.split(',')) if a.band else None
    t_start = time.time()
    notes, names, info = convert(video, kb, a.mode, f0, f1, a.parts, thr, a.min_dur, band, a.pps)
    if not notes:
        raise SystemExit('音が見つかりませんでした。--check の画像で位置を確認するか、--mode を変えてみてください')
    shift = 0.0
    if not a.keep_time:
        shift = 1.0 - notes[0].s
        for n in notes:
            n.s += shift
            n.e += shift
    write_notes(a.out, notes, names)
    log(f'書き出しました: {a.out}（{time.time() - t_start:.1f} 秒）')
    if 'pps' in info:
        log(f'  バーの速さ: {info["pps"]:.1f} px/秒')
    for i, nm in enumerate(names):
        ns = [n for n in notes if n.part == i]
        log(f'  {nm}: {len(ns)}音（{name_of(min(n.p for n in ns))}〜{name_of(max(n.p for n in ns))}、色 {info["part_colors"][i]}）')
    if not a.keep_time:
        log(f'  時刻を {shift:+.2f} 秒ずらしました（動画の {notes[0].s - shift:.2f} 秒 = MIDI の 1.00 秒）')


if __name__ == '__main__':
    main()
