"""MIDI ファイルの読み書き（秒単位のノート列 <-> SMF）"""
import mido

TPQ = 480
TEMPO_US = 500000  # 120 BPM 固定（ゲームは秒で扱うため）


class Note:
    __slots__ = ('p', 's', 'e', 'part', 'v')

    def __init__(self, p, s, e, part=0, v=80):
        self.p, self.s, self.e, self.part, self.v = int(p), float(s), float(e), int(part), int(v)

    def __repr__(self):
        return f'Note({self.p}, {self.s:.3f}-{self.e:.3f}, part={self.part})'


def load_notes(path):
    """MIDI を読み、(notes, part_names) を返す。パートの単位は（トラック, チャンネル）。ch10 は除く"""
    mid = mido.MidiFile(path)  # トラック名は latin1 で読み、あとで UTF-8 / Shift-JIS に直す
    tpq = mid.ticks_per_beat
    tempos = []
    raw = []  # (tick, track, msg)
    names = {}
    for ti, tr in enumerate(mid.tracks):
        tick = 0
        for msg in tr:
            tick += msg.time
            if msg.type == 'set_tempo':
                tempos.append((tick, msg.tempo))
            elif msg.type == 'track_name' and ti not in names:
                names[ti] = _fix_text(msg.name)
            elif msg.type in ('note_on', 'note_off'):
                raw.append((tick, ti, msg))
    tempos.sort()
    segs = [(0, 0.0, TEMPO_US)]
    for tick, us in tempos:
        t0, s0, u0 = segs[-1]
        if tick == t0:
            segs[-1] = (t0, s0, us)
        else:
            segs.append((tick, s0 + (tick - t0) * u0 / 1e6 / tpq, us))

    def t2s(t):
        seg = segs[0]
        for sg in segs:
            if sg[0] <= t:
                seg = sg
            else:
                break
        return seg[1] + (t - seg[0]) * seg[2] / 1e6 / tpq

    parts = {}
    part_names = []
    notes = []
    opened = {}
    raw.sort(key=lambda x: (x[0], x[1], 0 if (x[2].type == 'note_off' or x[2].velocity == 0) else 1))
    for tick, ti, msg in raw:
        if msg.channel == 9:
            continue
        key = (ti, msg.channel, msg.note)
        on = msg.type == 'note_on' and msg.velocity > 0
        if key in opened:
            st, v = opened.pop(key)
            pk = (ti, msg.channel)
            if pk not in parts:
                parts[pk] = len(parts)
                part_names.append(names.get(ti) or f'トラック{ti + 1}')
            notes.append(Note(msg.note, t2s(st), t2s(tick), parts[pk], v))
        if on:
            opened[key] = (tick, msg.velocity)
    notes.sort(key=lambda n: (n.s, n.p))
    return notes, part_names


def _fix_text(s):
    b = s.encode('latin1', errors='replace')
    for enc in ('utf-8', 'shift_jis'):
        try:
            return b.decode(enc).replace('\0', '').strip()
        except UnicodeDecodeError:
            pass
    return s


def write_notes(path, notes, part_names):
    """パートごとに別トラック（チャンネルも別）の SMF format 1 を書く。テンポは 120 固定"""
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ, charset='utf-8')
    t0 = mido.MidiTrack()
    t0.append(mido.MetaMessage('set_tempo', tempo=TEMPO_US, time=0))
    t0.append(mido.MetaMessage('end_of_track', time=0))
    mid.tracks.append(t0)
    sec2tick = TPQ * 1e6 / TEMPO_US
    for pi, name in enumerate(part_names):
        ch = pi if pi < 9 else pi + 1
        ch = min(ch, 15)
        ev = []
        mine = sorted((n for n in notes if n.part == pi), key=lambda n: (n.p, n.s))
        for i, n in enumerate(mine):
            a = max(0, round(n.s * sec2tick))
            b = max(a + 1, round(n.e * sec2tick))
            if i + 1 < len(mine) and mine[i + 1].p == n.p:   # 同じ音程の次の音と重ねない
                b = max(a + 1, min(b, round(mine[i + 1].s * sec2tick)))
            ev.append((a, 1, n.p, n.v))
            ev.append((b, 0, n.p, 0))
        ev.sort(key=lambda x: (x[0], x[1]))   # 同じ時刻なら note-off を先に
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage('track_name', name=name, time=0))
        tr.append(mido.Message('program_change', channel=ch, program=0, time=0))
        last = 0
        for tick, on, p, v in ev:
            if on:
                tr.append(mido.Message('note_on', channel=ch, note=p, velocity=v, time=tick - last))
            else:
                tr.append(mido.Message('note_off', channel=ch, note=p, velocity=0, time=tick - last))
            last = tick
        tr.append(mido.MetaMessage('end_of_track', time=0))
        mid.tracks.append(tr)
    mid.save(path)
