"""ピアノリズム E2E / ロジックテスト

使い方:
    pip install -r tests/requirements.txt
    python -m playwright install chromium
    python tests/run_tests.py

環境変数:
    CHROME_PATH=/path/to/chrome   使うブラウザを指定（未指定なら Playwright の Chromium）
    HEADED=1                      ブラウザ画面を表示して実行
スクリーンショットは tests/shots/ に保存されます（git 管理外）。
"""
import base64, json, os, subprocess, sys, threading, time
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / 'index.html').as_uri()
MID = str(ROOT / 'tests' / 'midi') + os.sep
SHOTS = str(ROOT / 'tests' / 'shots') + os.sep
os.makedirs(SHOTS, exist_ok=True)
if not (Path(MID) / 'two_tracks.mid').exists() or not (Path(MID) / 'format0.mid').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_midi.py'), MID], check=True)

# 動画 -> MIDI 変換ツール（tools/video2midi）が書き出す形の MIDI。書き出しは mido だけで動く
sys.path.insert(0, str(ROOT / 'tools' / 'video2midi'))
from midi_util import Note, write_notes  # noqa: E402
write_notes(MID + 'video2midi_out.mid',
            [Note(72, 1.0, 1.4, 0), Note(76, 1.5, 1.9, 0), Note(48, 1.0, 2.0, 1), Note(43, 2.0, 3.0, 1)],
            ['右手', '左手'])

def b64(path):
    return base64.b64encode(open(path, 'rb').read()).decode()

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

def serve(directory, html=None):
    """directory を配信する HTTP サーバーをランダムポートで起動（html を渡すと / にその内容を返す）"""
    if html is not None:
        class H(QuietHandler):
            def do_GET(self):
                body = html.encode()
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        handler = H
    else:
        handler = partial(QuietHandler, directory=str(directory))
    srv = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

# raw SMF with running status (format 0, tpq 96, default tempo 120)
trk = bytes.fromhex('00903C50' '603C00' '003E50' '603E00' '00FF2F00')
raw = b'MThd' + (6).to_bytes(4, 'big') + bytes.fromhex('0000 0001 0060') + b'MTrk' + len(trk).to_bytes(4, 'big') + trk
RAW_B64 = base64.b64encode(raw).decode()

PARSE_JS = '''(b64)=>{const s=atob(b64);const u=new Uint8Array(s.length);for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);
 const song=__PR.parseMidi(u.buffer,'t');return {parts:song.parts.map(p=>({id:p.id,name:p.name,count:p.count,drum:p.drum,mode:p.mode,hand:p.hand})),
 notes:song.notes.map(n=>[n.p,+n.s.toFixed(3),+n.e.toFixed(3),n.part]),dur:song.dur};}'''

results = []
def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + ('  ' + str(detail) if detail else ''))

with sync_playwright() as pw:
    launch = {'headless': os.environ.get('HEADED') != '1', 'args': ['--autoplay-policy=no-user-gesture-required']}
    if os.environ.get('CHROME_PATH'):
        launch['executable_path'] = os.environ['CHROME_PATH']
    br = pw.chromium.launch(**launch)
    ctx = br.new_context(viewport={'width': 1333, 'height': 800}, device_scale_factor=1.5)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append('console.' + m.type + ': ' + m.text) if m.type == 'error' else None)
    page.goto(PAGE)
    page.wait_for_timeout(800)
    page.screenshot(path=SHOTS + '01_home.png')

    # ---------- parser ----------
    r = page.evaluate(PARSE_JS, b64(MID + 'two_tracks.mid'))
    names = [p['name'] for p in r['parts']]
    check('two_tracks: 2 parts', len(r['parts']) == 2, names)
    check('two_tracks: Shift-JIS track name', '右手' in names, names)
    rh = [n for n in r['notes'] if n[0] >= 72]
    check('two_tracks: tempo map starts', [n[1] for n in rh] == [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0], [n[1] for n in rh])
    check('two_tracks: vel0 note-off ends', [n[2] for n in rh][:5] == [0.5, 1.0, 1.5, 2.0, 3.0], [n[2] for n in rh])
    lh = [n for n in r['notes'] if n[0] < 60]
    check('two_tracks: LH whole notes', lh == [[48, 0, 2.0, lh[0][3]], [43, 2.0, 6.0, lh[1][3]]], lh)
    check('two_tracks: hand guess', {p['name']: p['hand'] for p in r['parts']}.get('右手') == '右手', r['parts'])

    r0 = page.evaluate(PARSE_JS, b64(MID + 'format0.mid'))
    check('format0: 3 parts incl. drum', len(r0['parts']) == 3 and any(p['drum'] and p['mode'] == 'off' for p in r0['parts']), r0['parts'])
    g = [n for n in r0['notes'] if n[0] == 67]
    check('format0: retrigger split', g == [[67, 2.4, 3.0, g[0][3]], [67, 3.0, 3.6, g[0][3]]] if len(g) == 2 else False, g)
    c1 = [n for n in r0['notes'] if 60 <= n[0] <= 63]
    check('format0: ch1 timing (100bpm)', [n[1] for n in c1] == [0, 0.6, 1.2, 1.8], c1)

    rr = page.evaluate(PARSE_JS, RAW_B64)
    check('running status', [n[:3] for n in rr['notes']] == [[60, 0, 0.5], [62, 0.5, 1.0]], rr['notes'])

    rv = page.evaluate(PARSE_JS, b64(MID + 'video2midi_out.mid'))
    check('video2midi output: parts named 右手/左手 with hands',
          [(p['name'], p['hand'], p['count']) for p in rv['parts']] == [('右手', '右手', 2), ('左手', '左手', 2)], rv['parts'])
    check('video2midi output: note times (120bpm fixed)',
          [n[:3] for n in rv['notes']] == [[48, 1.0, 2.0], [72, 1.0, 1.4], [76, 1.5, 1.9], [43, 2.0, 3.0]], rv['notes'])

    bad = page.evaluate('''()=>{try{__PR.parseMidi(new Uint8Array([1,2,3,4,5,6,7,8]).buffer,'x');return 'no error'}catch(e){return e.message}}''')
    check('non-MIDI rejected with message', 'MIDI' in bad, bad)

    # ---------- normal play with a bot ----------
    page.evaluate('''(b64)=>{const s=atob(b64);const u=new Uint8Array(s.length);for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);
       __PR.openSetup(__PR.parseMidi(u.buffer,'テスト曲'));}''', b64(MID + 'two_tracks.mid'))
    page.wait_for_timeout(200)
    page.screenshot(path=SHOTS + '02_setup.png')
    page.evaluate('''()=>{__PR.setSetting('wait',false);__PR.setSetting('speed',100);__PR.setSetting('offset',0);__PR.setSetting('labels','ja');}''')
    page.click('#btnStart')
    # bot: press every RH note exactly on time (timestamp-accurate), skip LH, one wrong key
    page.evaluate('''()=>{const G=__PR.Game,I=__PR.Input;const done=new Set();let wrongDone=false;
      (function bot(){if(G.state==='ended'||G.state==='idle')return;
        if(G.state==='playing'&&!G.frozen){
          for(const n of G.play){if(done.has(n)||n.p<60)continue;if(n.s<=G.t){done.add(n);
            const ts=G.anchorReal+(n.s-G.anchorSong)/G.speed*1000;I.noteOn(n.p,90,ts,'midi');setTimeout(()=>I.noteOff(n.p,'midi'),120);}}
          if(!wrongDone&&G.t>2.5){wrongDone=true;I.noteOn(30,90,performance.now(),'midi');I.noteOff(30,'midi');}}
        requestAnimationFrame(bot);})();}''')
    page.wait_for_timeout(5600)
    page.screenshot(path=SHOTS + '03_play.png')
    page.wait_for_function("__PR.Game.state==='ended'", timeout=20000)
    st = page.evaluate('()=>({state:__PR.Game.state,counts:__PR.Game.counts,wrong:__PR.Game.wrong,maxCombo:__PR.Game.maxCombo,deltas:__PR.Game.deltas.map(d=>Math.round(d*1000))})')
    check('bot run finished', st['state'] == 'ended', st['state'])
    check('8 perfect, 2 miss', st['counts']['perfect'] == 8 and st['counts']['miss'] == 2, st['counts'])
    check('1 wrong touch', st['wrong'] == 1, st['wrong'])
    check('timestamps exact (|delta|<=2ms)', all(abs(d) <= 2 for d in st['deltas']), st['deltas'])
    page.screenshot(path=SHOTS + '04_result.png')

    # ---------- offset: bot presses 60ms late, offset +60 => still perfect ----------
    page.evaluate('''()=>{__PR.setSetting('offset',60);}''')
    page.click('#btnAgain')
    page.evaluate('''()=>{const G=__PR.Game,I=__PR.Input;const done=new Set();
      (function bot(){if(G.state!=='playing'&&G.state!=='paused')return;
        for(const n of G.play){if(done.has(n))continue;if(n.s+0.06*G.speed<=G.t){done.add(n);
          const ts=G.anchorReal+(n.s+0.06*G.speed-G.anchorSong)/G.speed*1000;I.noteOn(n.p,90,ts,'midi');I.noteOff(n.p,'midi');}}
        requestAnimationFrame(bot);})();}''')
    page.wait_for_function("__PR.Game.state==='ended'", timeout=20000)
    st = page.evaluate('()=>({state:__PR.Game.state,counts:__PR.Game.counts})')
    check('offset +60ms compensates late input', st['state'] == 'ended' and st['counts']['perfect'] == 10, st)
    page.evaluate('''()=>__PR.setSetting('offset',0)''')

    # ---------- late input without offset => graded GREAT/GOOD, calibration suggested ----------
    page.click('#btnAgain')
    page.evaluate('''()=>{const G=__PR.Game,I=__PR.Input;const done=new Set();
      (function bot(){if(G.state!=='playing')return;
        for(const n of G.play){if(done.has(n))continue;if(n.s+0.07<=G.t){done.add(n);
          const ts=G.anchorReal+(n.s+0.07-G.anchorSong)/G.speed*1000;I.noteOn(n.p,90,ts,'midi');I.noteOff(n.p,'midi');}}
        requestAnimationFrame(bot);})();}''')
    page.wait_for_function("__PR.Game.state==='ended'", timeout=20000)
    st = page.evaluate('()=>({state:__PR.Game.state,counts:__PR.Game.counts,calib:!!document.querySelector("#btnCalib"),txt:document.querySelector("#rTiming").innerText})')
    check('70ms late => GREAT + calibration button', st['counts']['great'] == 10 and st['calib'], st)
    page.screenshot(path=SHOTS + '05_result_calib.png')

    # ---------- wait (practice) mode ----------
    page.evaluate('''()=>{__PR.setSetting('wait',true);}''')
    page.click('#btnAgain')
    page.wait_for_timeout(4200)  # lead-in ~3.3s, then should freeze at first chord (t=0)
    s1 = page.evaluate('()=>({frozen:__PR.Game.frozen,t:__PR.Game.t})')
    check('wait mode freezes at first chord', s1['frozen'] and abs(s1['t']) < 1e-6, s1)
    page.screenshot(path=SHOTS + '06_wait.png')
    page.evaluate('()=>__PR.Input.noteOn(72,90,performance.now(),"midi")')
    page.wait_for_timeout(300)
    s2 = page.evaluate('()=>({frozen:__PR.Game.frozen,t:__PR.Game.t,ok:__PR.Game.counts.ok})')
    check('chord: still waiting after 1 of 2 notes', s2['frozen'] and s2['ok'] == 1, s2)
    page.evaluate('()=>__PR.Input.noteOn(48,90,performance.now(),"midi")')
    page.wait_for_timeout(250)
    s3 = page.evaluate('()=>({frozen:__PR.Game.frozen,t:__PR.Game.t,ok:__PR.Game.counts.ok})')
    check('chord complete => advances', s3['ok'] == 2 and s3['t'] > 0.1, s3)
    page.wait_for_timeout(600)
    s4 = page.evaluate('()=>({frozen:__PR.Game.frozen,t:__PR.Game.t})')
    check('freezes again at next note (0.5s)', s4['frozen'] and abs(s4['t'] - 0.5) < 1e-6, s4)
    page.evaluate('()=>__PR.Input.noteOn(40,90,performance.now(),"midi")')
    s5 = page.evaluate('()=>({wrong:__PR.Game.wrong,frozen:__PR.Game.frozen})')
    check('wrong key while waiting counted, still frozen', s5['wrong'] == 1 and s5['frozen'], s5)

    # ---------- pause / seek ----------
    page.click('#btnPause')
    page.wait_for_timeout(200)
    page.screenshot(path=SHOTS + '07_pause.png')
    box = page.locator('#progress').bounding_box()
    page.mouse.click(box['x'] + box['width'] * 0.75, box['y'] + box['height'] / 2)
    page.wait_for_timeout(200)
    s6 = page.evaluate('''()=>{const G=__PR.Game;return {state:G.state,t:G.t,skipped:G.play.filter(n=>n.j==='skip').length,pending:G.play.filter(n=>!n.j).length,ok:G.counts.ok}}''')
    check('seek while paused moves position & skips earlier notes', s6['state'] == 'paused' and s6['t'] > 1.0 and s6['skipped'] > 0, s6)
    page.click('#btnResume')
    page.wait_for_timeout(300)
    s7 = page.evaluate('()=>__PR.Game.state')
    check('resume works', s7 == 'playing', s7)
    page.evaluate('()=>{__PR.Game.quit();}')

    # ---------- A-B loop + count-in ----------
    # ドレミ音階: 80bpm (1 beat = 0.75s), RH notes start every 0.75s from 0s
    page.evaluate('''()=>{localStorage.removeItem('pr_loops');__PR.setSetting('wait',false);__PR.setSetting('speed',150);
       __PR.setSetting('countIn',true);__PR.openSetup(__PR.DEMOS[0].make());}''')
    page.evaluate('()=>__PR.commitLoop(3.2,5.1,true)')
    L = page.evaluate('()=>({...__PR.Game.loop})')
    check('loop: A/B snap to note starts (3.2->3.0, 5.1->5.25)', abs(L['a'] - 3.0) < 1e-6 and abs(L['b'] - 5.25) < 1e-6 and L['on'], L)
    info = page.locator('#loopSetup [data-loop=info]').inner_text()
    check('loop: setup shows range and note count', '0:03.0' in info and '0:05.3' in info and '3音' in info, info)
    clicks0 = page.evaluate('__PR.Synth.clickCount')
    page.click('#btnStart')
    ci = page.evaluate('()=>__PR.Game.clicks.map(c=>+c.t.toFixed(3))')
    check('count-in: 4 clicks one beat apart ending a beat before A', ci == [0.0, 0.75, 1.5, 2.25], ci)
    # bot: play every pending note on time; also press a dimmed note (F4 at 2.25s, outside the loop) -> not a mistake
    page.evaluate('''()=>{const G=__PR.Game,I=__PR.Input;let f4=false;
      (function bot(){if(G.state!=='playing'&&G.state!=='paused')return;
        if(G.state==='playing'&&!G.frozen){
          for(const n of G.play){if(n.j===null&&n.s<=G.t){const ts=G.anchorReal+(n.s-G.anchorSong)/G.speed*1000;I.noteOn(n.p,90,ts,'midi');I.noteOff(n.p,'midi');}}
          if(!f4&&G.t>=2.25){f4=true;const ts=G.anchorReal+(2.25-G.anchorSong)/G.speed*1000;I.noteOn(65,90,ts,'midi');I.noteOff(65,'midi');}}
        requestAnimationFrame(bot);})();}''')
    page.wait_for_function('__PR.Game.loopIter>=2', timeout=20000)
    toast_txt = page.locator('#toast').inner_text()
    check('loop: wraps back to A and reports the pass', '1回目' in toast_txt and '100.0%' in toast_txt, toast_txt)
    page.wait_for_timeout(300)
    page.screenshot(path=SHOTS + '12_loop_play.png')
    page.wait_for_function('__PR.Game.loopIter>=3', timeout=20000)
    st = page.evaluate('''()=>{const G=__PR.Game;return {state:G.state,wrong:G.wrong,clicks:__PR.Synth.clickCount,
       outsideSkipped:G.play.filter(n=>n.s<2.99||n.s>=5.25-1e-4).every(n=>n.j==='skip'),
       band:getComputedStyle(document.querySelector('#loopBand')).display,badge:document.querySelector('#loopBadge').textContent}}''')
    check('loop: keeps looping (no result screen), notes outside are dimmed', st['state'] == 'playing' and st['outsideSkipped'], st)
    check('loop: pressing a dimmed note is not a mistake', st['wrong'] == 0, st)
    check('count-in: 4 clicks on each of the first 2 passes', st['clicks'] - clicks0 >= 8, st['clicks'] - clicks0)
    check('loop: HUD band and badge', st['band'] == 'block' and '3回目' in st['badge'], st)

    # loop off while playing -> continues to the end and shows the result
    page.click('#btnPause')
    page.wait_for_timeout(150)
    page.screenshot(path=SHOTS + '13_loop_pause.png')
    page.click('#loopPause .sw')  # switch off
    page.evaluate('()=>__PR.Game.seekFrac(0.93)')
    page.click('#btnResume')
    page.wait_for_function("__PR.Game.state==='ended'", timeout=20000)
    check('loop off: song finishes normally', page.evaluate("document.querySelector('#result').classList.contains('show')"))

    # saved per song
    page.evaluate('()=>{__PR.Game.quit();__PR.openSetup(__PR.DEMOS[0].make());}')
    L2 = page.evaluate('()=>({...__PR.Game.loop})')
    check('loop: range is saved per song', abs(L2['a'] - 3.0) < 1e-6 and abs(L2['b'] - 5.25) < 1e-6 and not L2['on'], L2)

    # drag the A handle on the setup screen
    rg = page.locator('#loopSetup .lrange').bounding_box()
    ha = page.locator('#loopSetup [data-h=a]').bounding_box()
    page.mouse.move(ha['x'] + ha['width'] / 2, ha['y'] + ha['height'] / 2)
    page.mouse.down()
    page.mouse.move(rg['x'] + rg['width'] * 0.04, rg['y'] + rg['height'] / 2, steps=6)
    page.mouse.up()
    L3 = page.evaluate('()=>({...__PR.Game.loop,onset:__PR.snapOnset(__PR.Game.song,__PR.Game.loop.a)})')
    check('loop: dragging A turns the loop on and snaps', L3['on'] and L3['a'] < 3.0 and abs(L3['a'] - L3['onset']) < 1e-9, L3)
    page.screenshot(path=SHOTS + '14_loop_setup.png')

    # practice mode + loop: waits at A, then wraps after the 3 notes are played
    page.evaluate("()=>{__PR.setSetting('wait',true);__PR.commitLoop(3.0,5.25,true);}")
    page.click('#btnStart')
    page.wait_for_function('__PR.Game.frozen', timeout=10000)
    fz = page.evaluate('__PR.Game.t')
    check('practice + loop: waits at A', abs(fz - 3.0) < 1e-6, fz)
    for p in (67, 69, 71):
        page.wait_for_function('__PR.Game.frozen', timeout=5000)
        page.evaluate(f'()=>__PR.Input.noteOn({p},90,performance.now(),"midi")')
    page.wait_for_function('__PR.Game.loopIter>=2', timeout=10000)
    check('practice + loop: wraps after the notes are played', '弾けた音 3' in page.locator('#toast').inner_text(), page.locator('#toast').inner_text())
    page.evaluate("()=>{__PR.Game.quit();__PR.Game.setLoop({on:false});__PR.setSetting('wait',false);__PR.setSetting('speed',100);}")

    # ---------- だんだん速く (progressive speed-up in the loop) ----------
    page.evaluate('''()=>{__PR.setSetting('countIn',false);__PR.openSetup(__PR.DEMOS[0].make());__PR.commitLoop(3.0,5.25,true);}''')
    page.click('#loopSetup .ramp .sw')  # turn on -> options appear
    vis = page.evaluate("getComputedStyle(document.querySelector('#loopSetup .rampopts')).display")
    check('ramp: options shown when on', vis != 'none', vis)
    page.evaluate("()=>{__PR.setSetting('rampFrom',130);__PR.setSetting('rampTo',150);}")
    page.click('#loopSetup [data-set=rampStep] button[data-v="10"]')
    page.click('#loopSetup [data-set=rampPass] button[data-v="90"]')
    st = page.evaluate("()=>({step:__PR.S.rampStep,pass:__PR.S.rampPass,info:document.querySelector('#loopSetup [data-loop=info]').innerText})")
    check('ramp: seg values stored as numbers; setup shows from -> to', st['step'] == 10 and st['pass'] == 90 and '130% から 150%' in st['info'], st)
    page.click('#btnStart')
    sp0 = page.evaluate('__PR.S.speed')
    check('ramp: starts at the starting speed', sp0 == 130, sp0)
    page.evaluate('''()=>{const G=__PR.Game,I=__PR.Input;window.__botOn=true;
      (function bot(){if(G.state!=='playing'&&G.state!=='paused')return;
        if(window.__botOn&&G.state==='playing'&&!G.frozen){for(const n of G.play){if(n.j===null&&n.s<=G.t){
          const ts=G.anchorReal+(n.s-G.anchorSong)/G.speed*1000;I.noteOn(n.p,90,ts,'midi');I.noteOff(n.p,'midi');}}}
        requestAnimationFrame(bot);})();}''')
    page.wait_for_function('__PR.Game.loopIter>=2', timeout=15000)
    t1, sp1 = page.locator('#toast').inner_text(), page.evaluate('__PR.S.speed')
    check('ramp: pass -> next pass is +10%', sp1 == 140 and '合格！次は 140%' in t1, (sp1, t1))
    page.wait_for_function('__PR.Game.loopIter>=3', timeout=15000)
    page.wait_for_function('__PR.Game.loopIter>=4', timeout=15000)
    t3, sp3 = page.locator('#toast').inner_text(), page.evaluate('__PR.S.speed')
    check('ramp: stays at the target once reached', sp3 == 150 and '目標の 150% で合格' in t3, (sp3, t3))
    badge = page.locator('#loopBadge').inner_text()
    check('ramp: badge shows the current speed', '150%' in badge, badge)
    page.evaluate('()=>{window.__botOn=false;}')  # stop playing -> fail
    page.wait_for_function('__PR.Game.loopIter>=5', timeout=15000)
    t4, sp4 = page.locator('#toast').inner_text(), page.evaluate('__PR.S.speed')
    check('ramp: below the pass line -> same speed again', sp4 == 150 and 'もう一度 150%' in t4, (sp4, t4))
    page.screenshot(path=SHOTS + '15_ramp_play.png')
    page.evaluate("()=>{__PR.Game.quit();__PR.Game.setLoop({on:false});__PR.setSetting('ramp',false);__PR.setSetting('speed',100);__PR.setSetting('countIn',true);}")

    # ---------- demo songs + 88-key / labels rendering ----------
    for i in range(3):
        info = page.evaluate(f'''()=>{{const s=__PR.DEMOS[{i}].make();return {{title:s.title,n:s.notes.length,dur:+s.dur.toFixed(2),parts:s.parts.map(p=>p.name+':'+p.count+':'+p.hand)}}}}''')
        check(f'demo {i} builds', info['n'] > 20, info)
    page.evaluate('''()=>{__PR.setSetting('wait',false);__PR.setSetting('labels','ja');__PR.openSetup(__PR.DEMOS[1].make());}''')
    page.click('[data-preset="right"]')
    modes = page.evaluate('()=>__PR.Game.song.parts.map(p=>p.mode)')
    check('preset 右手だけ => RH play, LH auto', modes == ['play', 'auto'], modes)
    page.click('#btnStart')
    page.wait_for_timeout(5200)
    page.screenshot(path=SHOTS + '08_twinkle_play.png')
    page.evaluate('''()=>{__PR.setSetting('range','88');}''')
    page.wait_for_timeout(300)
    page.screenshot(path=SHOTS + '09_88keys.png')
    page.evaluate('''()=>{__PR.setSetting('range','auto');__PR.setSetting('labels','none');__PR.Game.quit();}''')

    # ---------- touch keyboard ----------
    page.evaluate('''()=>{__PR.Input.held.clear();__PR.openSetup(__PR.DEMOS[0].make())}''')
    page.click('#btnStart')
    page.wait_for_timeout(300)
    hy = page.evaluate('()=>__PR.R.hitY')
    kx = page.evaluate('()=>{const R=__PR.R;return R.kx[60]+R.kw[60]/2}')
    page.mouse.move(kx, hy + 0.9 * (800 - hy))
    page.mouse.down()
    held = page.evaluate('()=>[...__PR.Input.held.keys()]')
    page.mouse.up()
    check('touch on white key C4 => note 60', held == [60], held)
    bx = page.evaluate('()=>{const R=__PR.R;return R.kx[61]+R.kw[61]/2}')
    page.mouse.move(bx, hy + 10); page.mouse.down()
    held2 = page.evaluate('()=>[...__PR.Input.held.keys()]')
    page.mouse.up()
    check('touch on black key C#4 => note 61', held2 == [61], held2)
    page.evaluate('()=>__PR.Game.quit()')

    # ---------- split hands for single-part file ----------
    # build a single-part song spanning C4 explicitly
    page.evaluate('''()=>{const s=__PR.DEMOS[1].make();s.notes.forEach(n=>n.part=0);const p0=s.parts[0];p0.count=s.notes.length;p0.min=43;p0.max=69;p0.name='ピアノ';s.parts=[p0];
       __PR.openSetup(s);}''')
    has_split = page.locator('[data-preset="split"]').count()
    check('single-part song offers split', has_split == 1, has_split)
    page.click('[data-preset="split"]')
    sp = page.evaluate('()=>__PR.Game.song.parts.map(p=>[p.name,p.count,p.hand])')
    check('split => 右手/左手 parts', len(sp) == 2 and sum(x[1] for x in sp) == 66, sp)
    page.screenshot(path=SHOTS + '10_split.png')

    # portrait / small screen sanity
    page.set_viewport_size({'width': 800, 'height': 1280})
    page.evaluate('()=>{__PR.Game.quit();document.querySelector("[data-go=home]").click();}')
    page.wait_for_timeout(300)
    page.screenshot(path=SHOTS + '11_home_portrait.png')

    # ---------- MIDI blocked detection inside a cross-origin iframe (like the claude.ai artifact viewer) ----------
    app = serve(ROOT)
    app_url = f'http://127.0.0.1:{app.server_address[1]}/index.html'
    parent = serve(None, html=f'<!DOCTYPE html><iframe src="{app_url.replace("127.0.0.1", "localhost")}" style="width:1200px;height:800px;border:0"></iframe>')
    pg2 = ctx.new_page()
    pg2.goto(app_url); pg2.wait_for_timeout(500)
    top_state = pg2.evaluate('__PR.MidiIO.state')
    check('top-level http page: MIDI not blocked', top_state in ('idle', 'ok', 'denied'), top_state)
    pg2.goto(f'http://127.0.0.1:{parent.server_address[1]}/'); pg2.wait_for_timeout(1200)
    fr = [f for f in pg2.frames if f != pg2.main_frame][0]
    ifr_state = fr.evaluate('__PR.MidiIO.state')
    check('cross-origin iframe: MIDI reported as blocked', ifr_state == 'blocked', ifr_state)
    pg2.close(); app.shutdown(); parent.shutdown()

    check('no page errors', not errors, errors)
    br.close()

fails = [r for r in results if not r[1]]
print(f'\n{len(results) - len(fails)}/{len(results)} passed')
sys.exit(1 if fails else 0)
