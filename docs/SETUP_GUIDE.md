# あなたの作業手順（GitHub 公開・タブレットでの確認）

Claude（Claude Code）が代わりにできない作業をまとめています。GitHub アカウントでの操作、Pages の設定、実機（Android タブレット＋電子ピアノ）での確認です。上から順に進めてください。

ターミナルのコマンドは、どれも**このリポジトリのフォルダ（`index.html` がある場所）に移動してから**実行してください。

---

## 0. 準備（最初の1回だけ）

| 必要なもの | 確認・入れ方 |
|---|---|
| GitHub アカウント | https://github.com |
| git | ターミナルで `git --version`。無ければ https://git-scm.com からインストール |
| GitHub CLI（おすすめ） | https://cli.github.com の手順でインストール → `gh auth login`（「GitHub.com」→「HTTPS」→「Login with a web browser」を選ぶ） |
| コミットに使うメールアドレス | GitHub の **Settings → Emails** で「Keep my email addresses private」をオンにし、表示される `…@users.noreply.github.com` のアドレスを控えておく（手順1で使う） |

`gh auth login` を済ませておくと、push のときにパスワード（トークン）を聞かれなくなります。

---

## 1. ローカルで Git リポジトリにする

```bash
git init -b main
# コミットに記録する名前とメール（このリポジトリだけの設定）
git config user.name "GitHub のユーザー名"
git config user.email "123456+ユーザー名@users.noreply.github.com"   # 手順0で控えたもの
git config --show-origin user.email    # 「file:.git/config」と出れば、このリポジトリだけに設定できている
git add .
git commit -m "ピアノリズム v0.1"
```

- コミットには名前とメールが記録され、公開リポジトリでは誰でも見られます。noreply のアドレスを使えば、普段のメールアドレスは出ません
- `git config` に `--global` を付けていないので、設定はこのリポジトリの `.git/config` にだけ保存されます。ほかのリポジトリで使っている名前とメール（`--global` の設定）はそのまま変わりません
- `git status` で、`tests/shots/`、`tests/midi/`、`.mid` ファイルが入っていないことを確認してください（`.gitignore` で除外しています）

---

## 2. GitHub にリポジトリを作って push する

> **公開範囲について**：GitHub Pages を無料プランで使うには、リポジトリを **Public** にする必要があります（Private リポジトリで Pages を使えるのは有料プラン）。公開されるのはゲームのコードだけです。自分の MIDI ファイルや動画はコミットしないでください。

### 方法A：GitHub CLI（おすすめ）

```bash
gh repo create piano-rhythm --public --source=. --remote=origin --push
```

### 方法B：ブラウザ

1. https://github.com/new を開く
2. Repository name に `piano-rhythm`、**Public** を選ぶ。「Add a README file」「.gitignore」「license」は**選ばない**（ローカルにあるため）
3. 「Create repository」を押し、ターミナルで次を実行する（`<ユーザー名>` は自分の GitHub ユーザー名）

```bash
git remote add origin https://github.com/<ユーザー名>/piano-rhythm.git
git push -u origin main
```

push でパスワードを聞かれたら、GitHub のパスワードではなく Personal Access Token が必要です。手間なら `gh auth login` と `gh auth setup-git` を実行してから push し直してください（`gh auth setup-git` が設定するのは github.com に接続するときの認証だけで、ほかのサービスの git の認証には影響しません）。

---

## 3. GitHub Pages を有効にする

1. GitHub のリポジトリのページで **Settings → Pages** を開く
2. 「Build and deployment」の Source を **Deploy from a branch** にする
3. Branch を **main**、フォルダを **/(root)** にして **Save**
4. 1〜2分待って Pages の画面を再読み込みすると、URL が表示される
   `https://<ユーザー名>.github.io/piano-rhythm/`

まず PC の Chrome でこの URL を開き、ゲームが表示されることを確認してください。

---

## 4. タブレットで確認する

### つなぎ方
1. ケーブル：ピアノの **USB TO HOST** 端子（多くは四角い Type-B。最近の機種は USB-C のこともある）とタブレットの USB-C をつなぐ。Type-B と USB-C のケーブル、または Type-B と USB-A のケーブル＋USB-C 変換アダプタ（OTG）を使う
2. ピアノの電源を入れてからつなぐ。「USB の用途」や「アプリで開く？」と聞かれたら、閉じるかキャンセルでよい
3. ほかのピアノアプリが起動していたら終了しておく（MIDI 機器を取り合うことがある）

### ゲームの確認
1. タブレットの **Chrome** で Pages の URL を開く（端末に最初から入っているブラウザなど、Chrome 以外では動きません）
   ︙ メニュー →「ホーム画面に追加」をしておくと、次からはアプリのように開けます
2. 「ピアノに接続する」を押し、許可のダイアログで「許可」
3. 緑のランプで「接続中：（ピアノの名前）」と出て、鍵盤を弾くと「最後に弾いた鍵盤」が変われば成功
4. 「ドレミ音階」を通常モード（練習モード OFF）で最後まで弾く。結果画面に「補正を … にする」ボタンが出たら押す（タブレットの表示の遅れを補正します）
5. 「きらきら星」で「右手だけ」を選び、左手が自動で鳴ることを確認。設定の「自動パートの音」を「ピアノ」にすると、ピアノから伴奏が鳴るかも試す

### うまくいかないとき

| 表示・症状 | 対処 |
|---|---|
| 「この表示画面ではMIDIの使用がブロックされています」 | claude.ai のアーティファクトなど、埋め込み表示で開いています。Pages の URL を Chrome で直接開いてください |
| 「ピアノが見つかりません」 | ケーブルが充電専用でないか、ピアノの電源、ほかのピアノアプリが使っていないかを確認。つなぎ直すと自動で認識します |
| 「MIDIの使用が許可されませんでした」 | アドレスバー左のアイコン →「権限」→ MIDI デバイスを「許可」にしてから、もう一度「ピアノに接続する」 |
| 判定がいつも遅い／早い | 結果画面の補正ボタンを使うか、設定の「タイミング補正」を調整 |
| 画面がカクつく | 下のチェックリストに書いて Claude Code に伝えてください |

---

## 5. 確認結果を Claude Code に伝える

次のチェックリストを埋めて、Claude Code に貼り付けてください。CLAUDE.md の「ロードマップ1」の調整に使います。

```
- 接続：できた / できない（ピアノの機種名：　　　　）
- 弾いた鍵盤が正しく表示される：はい / いいえ
- ドレミ音階の結果：平均のずれ　　ms（補正後の値　　ms）
- 描画のなめらかさ：問題なし / ときどきカクつく / 重い
- 「自動」パートの音を「ピアノ」にしたとき：ピアノから鳴った / 鳴らない / ミスタッチが増えた
- 文字やボタンの大きさ：ちょうどよい / 小さい / 大きい
- その他、気になったこと：
```

---

## 6. Claude Code で続きを作る

1. このリポジトリのフォルダで Claude Code を開く（エディタの拡張機能、またはターミナルで `claude`）
2. 話しかける。`CLAUDE.md` は自動で読み込まれます
   - 例：「実機で確認した結果です（チェックリストを貼る）。ロードマップ1の調整をして」
   - 例：「ロードマップ2の動画→MIDI変換ツールを作って」
3. テストを Claude Code に実行させるには、最初に一度だけ準備が必要です

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r tests/requirements.txt
python -m playwright install chromium
python tests/run_tests.py
```

---

## 7. 更新をタブレットに反映する

```bash
git add .
git commit -m "変更内容のメモ"
git push
```

1〜2分で Pages に反映されます。タブレットでは再読み込みしてください。変わらないときは、タブを閉じて開き直してください。

---

## 8. 動画から MIDI を作る（PC で）

「鍵盤＋ノーツが降ってくる」動画から、ゲーム用の MIDI を作れます。詳しくは [tools/video2midi/README.md](../tools/video2midi/README.md)。

```bash
source .venv/bin/activate
pip install -r tools/video2midi/requirements.txt         # 最初の1回だけ
python tools/video2midi/video2midi.py 動画.mp4 --check check.png   # 位置合わせの確認画像
python tools/video2midi/video2midi.py 動画.mp4 -o 曲.mid           # 変換
```

できた `曲.mid` をタブレットにコピーし、ゲームの「MIDIファイル（.mid）を開く」で読み込みます。動画は自分で用意したファイルを使ってください（下の「変換に使う動画」を参照）。

実際の動画で試したら、次を Claude Code に伝えてください（変換ツールの調整に使います）。

```
- 動画の解像度と fps：
- --check の画像で位置は合っていたか：はい / いいえ（--range や --region を指定した：　　）
- 方式：keys / bars
- 結果：ほぼ正しい / 音が抜ける / 余分な音が出る / 右手・左手が逆・混ざる
- 気になった箇所（動画の何秒あたり）：
```

---

## 9. あなたが決めること

- **ライセンス**：公開リポジトリに MIT などのライセンスを付けるかどうか（付けないと、他人は閲覧はできても再利用はできない扱いになります）
- **リポジトリ名**：`piano-rhythm` 以外にする場合は、手順2・3の名前と URL を読み替えてください
- **変換に使う動画**：動画→MIDI 変換ツールに使う動画は、自分で用意したファイルを使う前提です。YouTube の動画をダウンロードすることは YouTube の規約で原則認められていません。アレンジ自体も動画作者の著作物なので、個人で楽しむ範囲にとどめ、作者が MIDI を配布しているならそちらを使うのが確実です
