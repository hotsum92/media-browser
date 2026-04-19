# 仕様: フォルダ画像/動画ブラウザ

## 概要
指定フォルダで起動し、ブラウザからフォルダ階層を辿りながら画像・動画をサムネイル一覧で閲覧できるローカル Web サーバー。

## 起動
```
python media_browser.py [path] [--port 8000] [--host 127.0.0.1]
```
- `path` 省略時はカレントディレクトリ
- 起動時に `http://host:port/` を表示

## 表示仕様
各階層で以下を 1 つのグリッドに混在表示。
並び順: フォルダ先、画像/動画後、各グループ内はアルファベット昇順（大文字小文字無視）。

| 種類 | サムネイル | クリック動作 |
|---|---|---|
| フォルダ | 配下をアルファベット順に再帰探索した最初の画像または動画のサムネ。無ければフォルダアイコン | そのフォルダの一覧へ遷移 |
| 画像 | 画像自体の縮小版 | モーダルで原寸表示 |
| 動画 | 1 秒時点のフレーム静止画 | モーダルで再生 |

各タイルにファイル名を表示。ヘッダーにパンくず（`root / sub / ...`）。

## 対応形式
- 画像: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`
- 動画: `.mp4`, `.mov`, `.webm`, `.mkv`, `.avi`

## サムネイル
- サイズ: 最大 256x256（アスペクト比維持）
- キャッシュ: `<ROOT>/.thumb_cache/` に保存、`path + mtime + size` の SHA1 で識別
- 動画: ffmpeg で 1 秒時点のフレームを抽出（短尺は先頭フレームにフォールバック）
- 画像: Pillow でリサイズ
- 生成は初回アクセス時の遅延生成、同一キーの並列生成はロックで抑制

## ルーティング
- `GET /` — HTML シェル（SPA）
- `GET /api/list?path=<rel>` — 指定フォルダ一覧 (JSON)
- `GET /thumb?path=<rel>` — サムネ画像 (JPEG)
- `GET /file?path=<rel>` — 元ファイル（Range 対応、動画ストリーム可）

クライアントの階層状態は URL のハッシュ (`#<rel>`) で管理。

### `/api/list` レスポンス
```json
{
  "path": "sub/dir",
  "entries": [
    {"type": "folder", "name": "a", "path": "sub/dir/a", "thumb": "sub/dir/a/00.jpg"},
    {"type": "image",  "name": "b.png", "path": "sub/dir/b.png", "thumb": "sub/dir/b.png"},
    {"type": "video",  "name": "c.mp4", "path": "sub/dir/c.mp4", "thumb": "sub/dir/c.mp4"}
  ]
}
```

## セキュリティ
- 起動フォルダ外へのパストラバーサルを禁止（resolve 後に ROOT 配下か検証）
- デフォルト `127.0.0.1` のみ Listen
- 隠しファイル/ディレクトリ（`.` で始まる名前）と `.thumb_cache` は一覧から除外

## 非機能要件
- 依存: Python 3.10+ / Pillow / ffmpeg（外部コマンド、動画のみ）
- 単一ファイル実装（HTML/CSS/JS は Python 内埋め込み）
- 1000 ファイル規模でも 2 回目以降はキャッシュで即時
- マルチスレッド HTTP サーバ
