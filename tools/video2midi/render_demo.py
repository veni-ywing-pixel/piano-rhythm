"""MIDI から「鍵盤＋ノーツが降ってくる」動画（Synthesia 風）を描く。変換ツールの精度の確認用。

使い方:
    python render_demo.py song.mid -o demo.mp4
    python render_demo.py song.mid -o demo.mp4 --size 1920x1080 --fps 60 --range A0-C8
    python render_demo.py song.mid -o bars.mp4 --no-key-light      # 鍵盤が光らない動画（バー方式の確認用）

見た目は index.html の描画（R.draw）に近づけている。ffmpeg があれば H.264 で書き出す（YouTube の圧縮に近い）。
"""
import argparse
import shutil
import subprocess
import sys

import cv2
import numpy as np

from keyboard import Keyboard, is_black, name_of, parse_range
from midi_util import load_notes

PALETTE = ['#7ee081', '#5ec8ff', '#ffb84d', '#c792ea', '#ff7aa2', '#ffe066', '#4dd6c9', '#f7a072']


def hex2bgr(h):
    n = int(h.lstrip('#'), 16)
    return np.array([n & 255, (n >> 8) & 255, n >> 16], float)


def shade(bgr, amt):
    f = 0.0 if amt < 0 else 255.0
    return bgr + (f - bgr) * abs(amt)


def auto_range(notes):
    """index.html の「曲に合わせる」と同じ決め方"""
    mn, mx = min(n.p for n in notes), max(n.p for n in notes)
    lo, hi = (mn // 12) * 12, ((mx + 1 + 11) // 12) * 12 - 1
    grow = 0
    while hi - lo + 1 < 36 and grow < 6:
        if grow % 2 == 0 and lo - 12 >= 12:
            lo -= 12
        elif hi + 12 <= 119:
            hi += 12
        elif lo - 12 >= 12:
            lo -= 12
        grow += 1
    return max(21, lo), min(108, hi)


def fill_rect(img, x0, y0, x1, y1, color, aa=True):
    """小数座標の長方形を塗る（端はアンチエイリアス）"""
    if x1 <= x0 or y1 <= y0:
        return
    s = 4
    pts = (np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) * (1 << s)).round().astype(np.int32)
    cv2.fillConvexPoly(img, pts, tuple(float(c) for c in color), cv2.LINE_AA if aa else cv2.LINE_8, s)


class Renderer:
    def __init__(self, notes, size=(1280, 720), look=3.0, rng=None, colors=None, key_light=True, bars=True,
                 labels=True, style='game'):
        """style: 'game' は index.html と同じ見た目。'alt' はわざと違えた見た目
        （黒鍵が等間隔・長め、鍵盤の上に赤いフェルト、左右に余白、灰色の背景、光る鍵にグラデーション、バーに縁取り）"""
        self.notes = sorted(notes, key=lambda n: n.s)
        self.W, self.H = size
        self.style = style
        lo, hi = rng or auto_range(notes)
        whites = sum(1 for p in range(lo, hi + 1) if not is_black(p))
        mx = round(self.W * 0.03) if style == 'alt' else 0
        ww = (self.W - 2 * mx) / whites
        kbH = max(64, min(self.H * 0.2, ww * 7.5))
        top = self.H - kbH
        self.kb = Keyboard(lo, hi, mx, self.W - mx, top, self.H, top + kbH * (0.66 if style == 'alt' else 0.63))
        self.felt = 0
        if style == 'alt':
            for p in self.kb.keys:            # 黒鍵を白鍵の境目のちょうど真ん中に置く
                if is_black(p):
                    self.kb.kx[p] = self.kb.kx[p - 1] + ww - self.kb.bw / 2
            self.felt = max(3, round(self.H * 0.012))
        self.hy = top - self.felt
        self.pps = self.hy / look
        self.key_light, self.bars, self.labels = key_light, bars, labels
        nparts = max(n.part for n in notes) + 1
        avg = []
        for i in range(nparts):
            ps = [n.p for n in notes if n.part == i]
            avg.append(sum(ps) / len(ps) if ps else 0)
        order = sorted(range(nparts), key=lambda i: -avg[i])
        pal = colors or PALETTE
        self.color = {}
        for rank, i in enumerate(order):
            self.color[i] = hex2bgr(pal[rank % len(pal)])
        self.max_dur = max(n.e - n.s for n in notes)
        self._base = self._background()

    def _background(self):
        W, H, hy = self.W, self.H, int(round(self.hy))
        img = np.zeros((H, W, 3), np.float64)
        if self.style == 'alt':
            k = np.linspace(0, 1, hy)[:, None]
            img[:hy] = (np.array([32, 32, 32], float) + 18 * k)[:, None, :]
            img[hy:] = np.array([18, 18, 18], float)
            img[hy:hy + self.felt, int(self.kb.x0):int(self.kb.x1)] = np.array([40, 30, 150], float)   # 赤いフェルト
            return img.astype(np.uint8)
        top, bot = np.array([0x14, 0x09, 0x06], float), np.array([0x3a, 0x1a, 0x11], float)  # BGR of #060914 / #111a3a
        k = np.linspace(0, 1, hy)[:, None]
        img[:hy] = (top + (bot - top) * k)[:, None, :]
        for p in self.kb.whites:
            if p % 12 in (0, 5):
                x = int(round(self.kb.kx[p]))
                a = 0.06 if p % 12 == 0 else 0.025
                img[:hy, x] = img[:hy, x] * (1 - a) + 255 * a
        # 判定ラインの光
        g0 = max(0, hy - 18)
        lc = np.array([0x81, 0xe0, 0x7e], float)
        for y in range(g0, hy):
            a = 0.3 * (y - g0) / max(1, hy - g0)
            img[y] = img[y] * (1 - a) + lc * a
        img[hy - 2:hy] = img[hy - 2:hy] * 0.05 + lc * 0.95
        img[hy:] = np.array([0x0f, 0x07, 0x05], float)
        return img.astype(np.uint8)

    def frame(self, t):
        img = self._base.copy()
        kb, hy, pps = self.kb, self.hy, self.pps
        lit = {}
        t_max = t + hy / pps + 0.1
        for n in self.notes:
            if n.s > t_max:
                break
            if n.e < t - 0.02 or n.p < kb.lo or n.p > kb.hi:
                continue
            bl = is_black(n.p)
            col = self.color[n.part]
            if n.s <= t <= n.e:
                lit[n.p] = col
            if not self.bars:
                continue
            pad = 1 if bl else 2
            x = kb.kx[n.p] + pad
            nw = kb.kw[n.p] - pad * 2
            yb = hy - (n.s - t) * pps
            yt = hy - (n.e - t) * pps
            if yb - yt < 5:
                yt = yb - 5
            yb, yt = min(yb, hy), max(yt, 0)
            if yb <= yt:
                continue
            c = shade(col, -0.22) if bl else col
            if self.style == 'alt':      # 縁取りつきのバー
                fill_rect(img, x, yt, x + nw, yb, shade(c, -0.45))
                fill_rect(img, x + 1.5, yt + 1.5, x + nw - 1.5, yb - 1.5, c)
                continue
            fill_rect(img, x, yt, x + nw, yb, c)
            if yb - yt > 6:   # 上の端の明るい線（index.html と同じ飾り）
                fill_rect(img, x + 2, yt + 2, x + nw - 2, yt + min(5, yb - yt - 3), shade(c, 0.22))
        self._keyboard(img, lit if self.key_light else {})
        return img

    def _keyboard(self, img, lit):
        kb, ky = self.kb, self.kb.top
        kh = self.H - ky
        alt = self.style == 'alt'
        for p in kb.whites:
            x, w = kb.kx[p], kb.kw[p]
            col = lit.get(p)
            if col is not None and alt:   # 上が明るいグラデーション
                for i in range(6):
                    fill_rect(img, x + 0.6, ky + 1 + (kh - 3) * i / 6, x + w - 0.6, ky + 1 + (kh - 3) * (i + 1) / 6,
                              shade(col, 0.3 * (1 - i / 5)), aa=False)
            else:
                fill_rect(img, x + 0.6, ky + 1, x + w - 0.6, ky + kh - 2, col if col is not None else hex2bgr('#eef0f7'))
            if col is None:
                fill_rect(img, x + 0.6, ky + kh - 12, x + w - 0.6, ky + kh - 3, hex2bgr('#e0e2e8'))
            if self.labels and p % 12 == 0 and w >= 14:
                fs = max(0.28, min(0.45, w / 60))
                txt = name_of(p)
                (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
                cv2.putText(img, txt, (int(x + w / 2 - tw / 2), int(ky + kh - 14)), cv2.FONT_HERSHEY_SIMPLEX, fs,
                            (60, 40, 30), 1, cv2.LINE_AA)
        for p in kb.keys:
            if not is_black(p):
                continue
            x, w = kb.kx[p], kb.kw[p]
            col = lit.get(p)
            bh = kb.bk_bottom - ky
            fill_rect(img, x, ky, x + w, ky + bh, shade(col, -0.3 if alt else -0.12) if col is not None else hex2bgr('#161927'))
            if col is None:
                fill_rect(img, x + w * 0.2, ky, x + w * 0.8, ky + bh - 6, hex2bgr('#2d3040'))
        if not alt:
            img[int(ky):int(ky) + 3] = (img[int(ky):int(ky) + 3] * 0.65).astype(np.uint8)


def render(notes, out, size=(1280, 720), fps=30, look=3.0, rng=None, colors=None, key_light=True, bars=True,
           noise=0.0, crf=23, lead=1.0, tail=1.0, seed=0, quiet=False, style='game'):
    r = Renderer(notes, size, look, rng, colors, key_light, bars, style=style)
    t0 = -lead - look
    t1 = max(n.e for n in notes) + tail
    nframes = int(np.ceil((t1 - t0) * fps))
    W, H = size
    rs = np.random.default_rng(seed)
    # ノイズは毎フレーム作ると遅いので、数枚作っておいて位置をずらしながら使い回す
    noises = [rs.normal(0, noise, (H * 2, W * 2, 3)).astype(np.int16) for _ in range(4)] if noise > 0 else []
    ff = shutil.which('ffmpeg')
    if ff and out.lower().endswith('.mp4'):
        proc = subprocess.Popen([ff, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                                 '-s', f'{W}x{H}', '-r', str(fps), '-i', '-', '-c:v', 'libx264', '-preset', 'veryfast',
                                 '-crf', str(crf), '-g', str(int(round(fps * 2))), '-pix_fmt', 'yuv420p', out], stdin=subprocess.PIPE)
        write = proc.stdin.write
    else:
        vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*'mp4v'), fps, (W, H))
        proc = None
        write = lambda b: vw.write(np.frombuffer(b, np.uint8).reshape(H, W, 3))
    for f in range(nframes):
        # 動画の 0 秒 = 曲の t0 秒。変換結果は 0 秒始まりになるので、評価ではずれをまとめて差し引く
        img = r.frame(t0 + f / fps)
        if noises:
            dy, dx = rs.integers(0, H), rs.integers(0, W)
            nz = noises[f % len(noises)][dy:dy + H, dx:dx + W]
            img = np.clip(img.astype(np.int16) + nz, 0, 255).astype(np.uint8)
        write(img.tobytes())
        if not quiet and f % 300 == 0:
            print(f'  描画中 {f}/{nframes}', file=sys.stderr)
    if proc:
        proc.stdin.close()
        proc.wait()
    else:
        vw.release()
    return {'t0': t0, 'kb': r.kb, 'pps': r.pps, 'frames': nframes}


def main():
    ap = argparse.ArgumentParser(description='MIDI から鍵盤＋降ってくるノーツの動画を描く（検証用）')
    ap.add_argument('midi')
    ap.add_argument('-o', '--out', required=True, help='出力する動画（.mp4）')
    ap.add_argument('--size', default='1280x720')
    ap.add_argument('--fps', type=float, default=30)
    ap.add_argument('--look', type=float, default=3.0, help='画面に表示する秒数')
    ap.add_argument('--range', help='鍵盤の範囲（例 A0-C8）。省略すると曲に合わせる')
    ap.add_argument('--colors', help='パートの色（例 #7ee081,#5ec8ff）。高い音のパートから順')
    ap.add_argument('--no-key-light', action='store_true', help='鍵盤を光らせない')
    ap.add_argument('--no-bars', action='store_true', help='降ってくるバーを描かない')
    ap.add_argument('--noise', type=float, default=0, help='画素ノイズの強さ（0〜255）')
    ap.add_argument('--crf', type=int, default=23, help='H.264 の画質（大きいほど粗い）')
    ap.add_argument('--style', choices=['game', 'alt'], default='game',
                    help='見た目。game=ゲームと同じ, alt=黒鍵が等間隔・赤いフェルト・余白などわざと違えたもの')
    a = ap.parse_args()
    notes, names = load_notes(a.midi)
    W, H = (int(v) for v in a.size.lower().split('x'))
    info = render(notes, a.out, (W, H), a.fps, a.look, parse_range(a.range) if a.range else None,
                  a.colors.split(',') if a.colors else None, not a.no_key_light, not a.no_bars, a.noise, a.crf,
                  style=a.style)
    print(f'書き出しました: {a.out}（{info["frames"]}フレーム、{info["kb"].describe()}、'
          f'曲の0秒は動画の{-info["t0"]:.2f}秒）')


if __name__ == '__main__':
    main()
