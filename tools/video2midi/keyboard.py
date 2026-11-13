"""鍵盤の配置と、動画の画面からの鍵盤の自動検出。

鍵盤の配置は index.html の R.layout と同じ考え方：
白鍵を等間隔に並べ、黒鍵は幅 0.6 倍で、音ごとに少し左右へずらす。
"""
import re

import cv2
import numpy as np

PC_EN = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
BLACK = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
BLACK_OFF = {1: -0.12, 3: 0.12, 6: -0.15, 8: 0.0, 10: 0.15}  # 黒鍵幅に対するずれ（R.layout と同じ）
BW_RATIO = 0.6            # 黒鍵の幅 / 白鍵の幅
BK_RATIO = 0.62           # 黒鍵の長さ / 鍵盤の高さ（自動検出できないときの既定値）
LETTERS = 'CDEFGAB'
WHITE_PC = [0, 2, 4, 5, 7, 9, 11]
HAS_BLACK_AFTER = [1, 1, 0, 1, 1, 1, 0]  # C D E F G A B の右隣に黒鍵があるか


def is_black(p):
    return BLACK[p % 12] == 1


def name_of(p):
    return PC_EN[p % 12] + str(p // 12 - 1)


def name_to_midi(s):
    m = re.fullmatch(r'([A-Ga-g])([#b]?)(-?\d)', s.strip())
    if not m:
        raise ValueError(f'音名が読めません: {s}（例: A0, C4, F#3）')
    pc = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[m.group(1).upper()]
    pc += {'#': 1, 'b': -1, '': 0}[m.group(2)]
    return (int(m.group(3)) + 1) * 12 + pc


def parse_range(s):
    """'A0-C8' -> (21, 108)"""
    m = re.fullmatch(r'\s*([A-Ga-g][#b]?-?\d)\s*[-~〜]\s*([A-Ga-g][#b]?-?\d)\s*', s)
    if not m:
        raise ValueError(f'鍵盤の範囲が読めません: {s}（例: A0-C8）')
    lo, hi = name_to_midi(m.group(1)), name_to_midi(m.group(2))
    if lo >= hi:
        raise ValueError('鍵盤の範囲は「低い音-高い音」の順に書いてください')
    if is_black(lo) or is_black(hi):
        raise ValueError('鍵盤の両端は白鍵にしてください')
    return lo, hi


class Keyboard:
    """画面上の鍵盤。座標はピクセル（x は左から、y は上から）。

    x0, x1: 鍵盤の左端・右端 / top, bottom: 鍵盤の上端・下端 / bk_bottom: 黒鍵の下端
    """

    def __init__(self, lo, hi, x0, x1, top, bottom, bk_bottom=None):
        if is_black(lo) or is_black(hi):
            raise ValueError('鍵盤の両端は白鍵にしてください')
        self.lo, self.hi = lo, hi
        self.x0, self.x1, self.top, self.bottom = float(x0), float(x1), float(top), float(bottom)
        self.bk_bottom = float(bk_bottom) if bk_bottom is not None else self.top + BK_RATIO * (self.bottom - self.top)
        self.whites = [p for p in range(lo, hi + 1) if not is_black(p)]
        self.ww = (self.x1 - self.x0) / len(self.whites)
        self.bw = self.ww * BW_RATIO
        self.kx, self.kw = {}, {}
        for i, p in enumerate(self.whites):
            self.kx[p] = self.x0 + i * self.ww
            self.kw[p] = self.ww
        for p in range(lo, hi + 1):
            if is_black(p):
                self.kx[p] = self.kx[p - 1] + self.ww - self.bw / 2 + BLACK_OFF[p % 12] * self.bw
                self.kw[p] = self.bw
        self.keys = list(range(lo, hi + 1))

    def center(self, p):
        return self.kx[p] + self.kw[p] / 2

    def describe(self):
        return (f'{name_of(self.lo)}〜{name_of(self.hi)}（{len(self.keys)}鍵）、'
                f'x={self.x0:.0f}〜{self.x1:.0f}, y={self.top:.0f}〜{self.bottom:.0f}, 黒鍵の下端 y={self.bk_bottom:.0f}')

    def white_zone(self, p, margin=0.06):
        """白鍵のうち、上から見て黒鍵と重ならない横の範囲（バー方式で使う）"""
        a, b = self.kx[p], self.kx[p] + self.ww
        lo_cut, hi_cut = a, b
        if p - 1 >= self.lo and is_black(p - 1):
            lo_cut = max(lo_cut, self.kx[p - 1] + self.kw[p - 1])
        if p + 1 <= self.hi and is_black(p + 1):
            hi_cut = min(hi_cut, self.kx[p + 1])
        pad = (hi_cut - lo_cut) * margin + 1
        return lo_cut + pad, hi_cut - pad


# ---------------------------------------------------------------- 自動検出

def _runs(mask):
    """1次元の bool 配列の True の連続区間 [(start, end), ...]（end は含まない）"""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def _longest_run(mask, max_gap=2):
    runs = _runs(mask)
    if not runs:
        return None
    merged = [list(runs[0])]
    for s, e in runs[1:]:
        if s - merged[-1][1] <= max_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return max(merged, key=lambda r: r[1] - r[0])


def detect_keyboard(frame_bgr, rng=None, region=None):
    """鍵盤の位置と音域を推定する。

    frame_bgr: 鍵盤が押されていない（または押された鍵が少ない）フレーム。動画の中央値フレームがよい
    rng: (lo, hi) を指定すると音域の推定を省く
    region: (x0, y0, x1, y1) を指定すると位置の推定を省く（黒鍵の下端と音域だけ推定する）
    戻り値: (Keyboard, 情報 dict)
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    thr, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = float(np.clip(thr, 70, 190))
    bright = gray > thr
    info = {'threshold': thr}

    if region is None:
        # 黒鍵の段：明るい（白鍵）と暗い（黒鍵）が細かく入れ替わる行
        trans = np.count_nonzero(bright[:, 1:] != bright[:, :-1], axis=1)
        frac = bright.mean(axis=1)
        cand = (trans >= 10) & (frac >= 0.2) & (frac <= 0.9)
        run = _longest_run(cand)
        if run is None or run[1] - run[0] < 4:
            raise RuntimeError('鍵盤が見つかりませんでした。--region で鍵盤の位置を指定してください')
        bz_top, bz_bot = int(run[0]), int(run[1])  # 黒鍵の段 [bz_top, bz_bot)
        # 左右の端：黒鍵の段の中ほどの行で、明るい部分の範囲
        mid = bright[bz_top + (bz_bot - bz_top) // 4: bz_bot - (bz_bot - bz_top) // 4]
        colb = mid.mean(axis=0) > 0.5
        xs = np.nonzero(colb)[0]
        x0, x1 = int(xs[0]), int(xs[-1]) + 1
        # 白鍵だけの段：黒鍵の段の下で、鍵盤の幅の大部分が明るい行
        frac_k = bright[:, x0:x1].mean(axis=1)
        y = bz_bot
        while y < H and frac_k[y] >= 0.6:
            y += 1
        bottom = y
        if bottom - bz_bot < 3:
            raise RuntimeError('鍵盤の下端が見つかりませんでした。--region で鍵盤の位置を指定してください')
        # 白鍵の段で左右の端を取り直す（左右端の鍵の上が黒っぽい場合に備える）
        wz = bright[bz_bot + (bottom - bz_bot) // 3: bottom - (bottom - bz_bot) // 4]
        colw = _longest_run(wz.mean(axis=0) > 0.5, max_gap=4)
        if colw and colw[1] - colw[0] > (x1 - x0) * 0.9:
            x0, x1 = int(colw[0]), int(colw[1])
        top = bz_top
        bk_bottom = bz_bot
    else:
        x0, top, x1, bottom = region
        x0, x1, top, bottom = int(x0), int(x1), int(top), int(bottom)
        # 黒鍵の下端：鍵盤の中で、暗い部分の割合が急に減る行
        frac_k = bright[top:bottom, x0:x1].mean(axis=1)
        below = np.nonzero(frac_k >= 0.85)[0]
        bk_bottom = top + int(below[0]) if len(below) and below[0] > (bottom - top) * 0.3 else None
        bz_top, bz_bot = top, bk_bottom if bk_bottom is not None else int(top + BK_RATIO * (bottom - top))

    # 黒鍵の位置：黒鍵の段の中ほどの行で、暗い部分のかたまり
    y_a = bz_top + (bz_bot - bz_top) // 4
    y_b = max(y_a + 1, bz_bot - (bz_bot - bz_top) // 4)
    prof = bright[y_a:y_b, x0:x1].mean(axis=0) < 0.5
    blacks = [(s, e) for s, e in _runs(prof)]
    if blacks:
        widths = np.array([e - s for s, e in blacks], float)
        medw = np.median(widths)
        blacks = [(s, e) for s, e in blacks if 0.4 * medw <= e - s <= 2.0 * medw]
    centers = np.array([x0 + (s + e) / 2 for s, e in blacks], float)
    info['black_keys_found'] = len(centers)

    if rng is not None:
        lo, hi = rng
    else:
        lo, hi, score = _guess_range(centers, x0, x1, len(blacks) and np.median([e - s for s, e in blacks]))
        info['range_score'] = score
    kb = Keyboard(lo, hi, x0, x1, top, bottom, bk_bottom)
    return kb, info


def _guess_range(centers, x0, x1, black_w):
    """黒鍵の並び（2つ組・3つ組）から、白鍵の数と左端の音を決める"""
    if len(centers) < 2:
        raise RuntimeError('黒鍵が見つからず、音域を推定できませんでした。--range で指定してください（例: A0-C8）')
    best = None
    width = x1 - x0
    for n_white in range(5, 76):
        ww = width / n_white
        if black_w and not (0.3 <= black_w / ww <= 0.95):
            continue
        j = np.round((centers - x0) / ww).astype(int) - 1     # 白鍵 j と j+1 の境目
        dev = np.abs(centers - (x0 + (j + 1) * ww)) / ww
        ok = (dev <= 0.3) & (j >= 0) & (j < n_white - 1)
        found = set(j[ok].tolist())
        dup = int(ok.sum()) - len(found)
        unmatched = int((~ok).sum())
        for r in range(7):
            exp = {jj for jj in range(n_white - 1) if HAS_BLACK_AFTER[(r + jj) % 7]}
            score = len(exp & found) - len(exp - found) - len(found - exp) - 2 * unmatched - dup
            if best is None or score > best[0]:
                best = (score, n_white, r)
    score, n_white, r = best
    pc = WHITE_PC[r]
    if n_white == 52 and r == 5:          # 88鍵（A0〜C8）
        lo = 21
    else:                                  # 音域の真ん中がレ（D4）付近になるオクターブを選ぶ
        cands = []
        for octv in range(0, 8):
            lo_c = (octv + 1) * 12 + pc
            whites = _whites_from(lo_c, n_white)
            cands.append((abs((whites[0] + whites[-1]) / 2 - 62), lo_c))
        lo = min(cands)[1]
    whites = _whites_from(lo, n_white)
    return whites[0], whites[-1], score


def _whites_from(lo, n):
    out, p = [], lo
    while len(out) < n:
        if not is_black(p):
            out.append(p)
        p += 1
    return out


def draw_overlay(frame_bgr, kb, band=None, white_rows=None, black_rows=None):
    """位置合わせの確認画像：鍵の境目、判定に使う範囲、C の音名を重ねる"""
    img = frame_bgr.copy()
    H, W = img.shape[:2]
    yt, yb, ykb = int(kb.top), int(kb.bottom), int(kb.bk_bottom)
    cv2.rectangle(img, (int(kb.x0), yt), (int(kb.x1) - 1, yb - 1), (0, 255, 255), 1)
    cv2.line(img, (int(kb.x0), ykb), (int(kb.x1), ykb), (255, 0, 255), 1)
    for p in kb.keys:
        x = kb.kx[p]
        if is_black(p):
            cv2.rectangle(img, (int(round(x)), yt), (int(round(x + kb.kw[p])), ykb), (255, 128, 0), 1)
            if black_rows:
                a, b = sample_span(kb, p)
                cv2.rectangle(img, (int(a), black_rows[0]), (int(b), black_rows[1]), (0, 0, 255), -1)
        else:
            cv2.line(img, (int(round(x)), ykb), (int(round(x)), yb), (0, 200, 0), 1)
            if white_rows:
                a, b = sample_span(kb, p)
                cv2.rectangle(img, (int(a), white_rows[0]), (int(b), white_rows[1]), (0, 0, 255), -1)
            if p % 12 == 0:
                fs = max(0.35, min(0.7, kb.ww / 40))
                cv2.putText(img, name_of(p), (int(x + 2), yt - 6), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 255), 1, cv2.LINE_AA)
    if band:
        cv2.rectangle(img, (int(kb.x0), band[0]), (int(kb.x1) - 1, band[1] - 1), (255, 255, 0), 1)
    return img


def sample_span(kb, p, frac=0.5):
    """鍵の色を取る横の範囲（鍵の幅の中央 frac）"""
    c, w = kb.center(p), kb.kw[p] * frac
    return c - w / 2, c + w / 2
