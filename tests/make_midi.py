"""Generate test MIDI files covering parser edge cases."""
import mido, sys, os

out = sys.argv[1]
os.makedirs(out, exist_ok=True)

# 1) format 1, 2 tracks, tempo change, Shift-JIS track name, note-on vel 0 as note-off
mid = mido.MidiFile(type=1, ticks_per_beat=480)
t0 = mido.MidiTrack(); mid.tracks.append(t0)
t0.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))      # 120 bpm
t0.append(mido.MetaMessage('set_tempo', tempo=1000000, time=1920))  # 60 bpm after 4 beats (2.0s)
rh = mido.MidiTrack(); mid.tracks.append(rh)
rh.append(mido.MetaMessage('track_name', name='x', time=0))
lh = mido.MidiTrack(); mid.tracks.append(lh)
# RH: 8 quarter notes C5.. (4 at 120bpm = 0.5s each, 4 at 60bpm = 1.0s each)
for i, p in enumerate([72, 74, 76, 77, 79, 77, 76, 74]):
    rh.append(mido.Message('note_on', note=p, velocity=80, time=0))
    rh.append(mido.Message('note_on', note=p, velocity=0, time=480))   # vel0 = off
# LH: two whole notes
lh.append(mido.Message('note_on', note=48, velocity=70, time=0))
lh.append(mido.Message('note_off', note=48, velocity=0, time=1920))
lh.append(mido.Message('note_on', note=43, velocity=70, time=0))
lh.append(mido.Message('note_off', note=43, velocity=0, time=1920))
mid.save(os.path.join(out, 'two_tracks.mid'))

# patch track name of RH to Shift-JIS bytes "右手"
data = bytearray(open(os.path.join(out, 'two_tracks.mid'), 'rb').read())
sj = '右手'.encode('shift_jis')
idx = data.find(b'\xff\x03\x01x')
assert idx > 0
# rebuild: replace meta (len 1 'x') with sj bytes and fix MTrk length
new = bytes(data[:idx]) + b'\xff\x03' + bytes([len(sj)]) + sj + bytes(data[idx + 4:])
# find the MTrk header that contains idx and fix its length
pos = 14
buf = bytearray(new)
while pos < len(buf):
    assert buf[pos:pos + 4] == b'MTrk'
    ln = int.from_bytes(buf[pos + 4:pos + 8], 'big')
    if pos < idx < pos + 8 + ln + 8:
        buf[pos + 4:pos + 8] = (ln + len(sj) - 1).to_bytes(4, 'big')
        break
    pos += 8 + ln
open(os.path.join(out, 'two_tracks.mid'), 'wb').write(buf)

# 2) format 0, single track, two channels, running status, retrigger same note
mid0 = mido.MidiFile(type=0, ticks_per_beat=96)
tr = mido.MidiTrack(); mid0.tracks.append(tr)
tr.append(mido.MetaMessage('set_tempo', tempo=600000, time=0))  # 100 bpm
for k in range(4):
    tr.append(mido.Message('note_on', channel=0, note=60 + k, velocity=90, time=0))
    tr.append(mido.Message('note_on', channel=1, note=36 + k, velocity=90, time=0))
    tr.append(mido.Message('note_off', channel=0, note=60 + k, velocity=0, time=96))
    tr.append(mido.Message('note_off', channel=1, note=36 + k, velocity=0, time=0))
# retrigger: on, on (same tick as off of previous written after)
tr.append(mido.Message('note_on', channel=0, note=67, velocity=90, time=0))
tr.append(mido.Message('note_on', channel=0, note=67, velocity=90, time=96))
tr.append(mido.Message('note_off', channel=0, note=67, velocity=0, time=0))
tr.append(mido.Message('note_off', channel=0, note=67, velocity=0, time=96))
# drums on ch10
tr.append(mido.Message('note_on', channel=9, note=36, velocity=100, time=0))
tr.append(mido.Message('note_off', channel=9, note=36, velocity=0, time=48))
mid0.save(os.path.join(out, 'format0.mid'))
print('ok', os.listdir(out))
