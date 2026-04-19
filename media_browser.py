#!/usr/bin/env python3
"""Folder image/video browser web server (single file).

Usage: python media_browser.py [path] [--port 8000] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from PIL import Image
except ImportError:
    print("Pillow is required: pip install Pillow", file=sys.stderr)
    sys.exit(1)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
THUMB_SIZE = (256, 256)
CACHE_DIR_NAME = ".thumb_cache"
FFMPEG = "ffmpeg"

ROOT: Path = Path.cwd()
CACHE_DIR: Path = ROOT / CACHE_DIR_NAME

_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS


def is_media(p: Path) -> bool:
    return p.suffix.lower() in MEDIA_EXTS


def safe_resolve(rel: str) -> Path:
    rel = (rel or "").lstrip("/")
    p = (ROOT / rel).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise PermissionError("path escapes root")
    return p


def rel_from_root(p: Path) -> str:
    try:
        r = p.relative_to(ROOT)
    except ValueError:
        return ""
    s = str(r).replace(os.sep, "/")
    return "" if s == "." else s


def first_media_recursive(folder: Path) -> Path | None:
    try:
        entries = sorted(folder.iterdir(), key=lambda e: e.name.lower())
    except (PermissionError, OSError):
        return None
    for e in entries:
        if e.is_file() and is_media(e):
            return e
    for e in entries:
        if e.is_dir() and not e.name.startswith(".") and e.name != CACHE_DIR_NAME:
            found = first_media_recursive(e)
            if found:
                return found
    return None


def cache_key(p: Path) -> str:
    try:
        st = p.stat()
    except OSError:
        return ""
    return hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()


def get_lock(key: str) -> threading.Lock:
    with _locks_mu:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def ensure_thumbnail(p: Path) -> Path | None:
    key = cache_key(p)
    if not key:
        return None
    out = CACHE_DIR / f"{key}.jpg"
    if out.exists():
        return out
    lock = get_lock(key)
    with lock:
        if out.exists():
            return out
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.jpg")
        try:
            if is_image(p):
                with Image.open(p) as im:
                    im = im.convert("RGB")
                    im.thumbnail(THUMB_SIZE)
                    im.save(tmp, "JPEG", quality=85)
            elif is_video(p):
                scale = f"scale='min({THUMB_SIZE[0]},iw)':-1"
                cmd = [FFMPEG, "-y", "-ss", "1", "-i", str(p),
                       "-frames:v", "1", "-vf", scale, str(tmp)]
                r = subprocess.run(cmd, capture_output=True, timeout=30)
                if r.returncode != 0 or not tmp.exists():
                    cmd2 = [FFMPEG, "-y", "-i", str(p),
                            "-frames:v", "1", "-vf", scale, str(tmp)]
                    subprocess.run(cmd2, capture_output=True, timeout=30)
            else:
                return None
            if tmp.exists():
                os.replace(tmp, out)
                return out
        except Exception as e:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            sys.stderr.write(f"thumb error {p}: {e}\n")
    return out if out.exists() else None


INDEX_HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Media Browser</title>
<style>
  html,body { margin:0; background:#111; color:#eee; font-family:-apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:12px 16px; background:#1a1a1a; position:sticky; top:0; z-index:10; border-bottom:1px solid #2a2a2a; }
  nav { font-size:14px; }
  nav a { color:#7ad; text-decoration:none; }
  nav a:hover { text-decoration:underline; }
  nav .sep { color:#555; margin:0 6px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:12px; padding:16px; }
  .tile { background:#1b1b1b; border-radius:8px; overflow:hidden; cursor:pointer; border:1px solid #2a2a2a; display:flex; flex-direction:column; }
  .tile:hover { border-color:#4af; }
  .thumb { aspect-ratio:1/1; background:#000; display:flex; align-items:center; justify-content:center; position:relative; }
  .thumb img { width:100%; height:100%; object-fit:contain; }
  .icon { font-size:64px; }
  .badge { position:absolute; right:6px; bottom:6px; background:rgba(0,0,0,0.7); padding:2px 6px; border-radius:4px; font-size:11px; }
  .name { padding:8px; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .empty { padding:24px; opacity:0.6; }
  .modal { position:fixed; inset:0; background:rgba(0,0,0,0.92); display:none; align-items:center; justify-content:center; z-index:100; }
  .modal.open { display:flex; }
  .modal img, .modal video { max-width:92vw; max-height:92vh; }
  .close { position:absolute; top:10px; right:16px; color:#fff; font-size:28px; cursor:pointer; user-select:none; }
  .nav-btn { position:absolute; top:50%; transform:translateY(-50%); background:rgba(0,0,0,0.5); color:#fff; border:none; width:56px; height:80px; font-size:32px; cursor:pointer; user-select:none; }
  .nav-btn:hover { background:rgba(0,0,0,0.8); }
  .nav-btn:disabled { opacity:0.25; cursor:default; }
  .nav-btn.prev { left:12px; border-radius:0 6px 6px 0; }
  .nav-btn.next { right:12px; border-radius:6px 0 0 6px; }
  .caption { position:absolute; bottom:12px; left:50%; transform:translateX(-50%); background:rgba(0,0,0,0.6); color:#fff; padding:4px 12px; border-radius:4px; font-size:13px; max-width:80vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
</style>
</head>
<body>
<header><nav id="crumbs"></nav></header>
<div class="grid" id="grid"></div>
<div class="modal" id="modal">
  <span class="close" id="close">&#10005;</span>
  <button class="nav-btn prev" id="prev" title="前 (←)">&#10094;</button>
  <button class="nav-btn next" id="next" title="次 (→)">&#10095;</button>
  <div class="caption" id="caption"></div>
  <div id="modal-body"></div>
</div>
<script>
const grid = document.getElementById('grid');
const crumbs = document.getElementById('crumbs');
const modal = document.getElementById('modal');
const modalBody = document.getElementById('modal-body');
const prevBtn = document.getElementById('prev');
const nextBtn = document.getElementById('next');
const caption = document.getElementById('caption');

let mediaItems = [];
let currentIndex = -1;

document.getElementById('close').onclick = closeModal;
prevBtn.onclick = e => { e.stopPropagation(); showAt(currentIndex - 1); };
nextBtn.onclick = e => { e.stopPropagation(); showAt(currentIndex + 1); };
modal.onclick = e => { if (e.target === modal) closeModal(); };
document.addEventListener('keydown', e => {
  if (!modal.classList.contains('open')) return;
  if (e.key === 'Escape') closeModal();
  else if (e.key === 'ArrowLeft') showAt(currentIndex - 1);
  else if (e.key === 'ArrowRight') showAt(currentIndex + 1);
});

function closeModal() {
  modal.classList.remove('open');
  modalBody.innerHTML = '';
  currentIndex = -1;
}

function showAt(index) {
  if (index < 0 || index >= mediaItems.length) return;
  currentIndex = index;
  const item = mediaItems[index];
  modalBody.innerHTML = '';
  const src = '/file?path=' + encodeURIComponent(item.path);
  if (item.type === 'image') {
    const img = document.createElement('img');
    img.src = src;
    img.style.cursor = 'pointer';
    img.title = 'クリックで次へ';
    img.onclick = e => { e.stopPropagation(); showAt(currentIndex + 1); };
    modalBody.appendChild(img);
  } else if (item.type === 'video') {
    const v = document.createElement('video'); v.src = src; v.controls = true; v.autoplay = true; modalBody.appendChild(v);
  }
  caption.textContent = (index + 1) + ' / ' + mediaItems.length + '  —  ' + item.name;
  prevBtn.disabled = index === 0;
  nextBtn.disabled = index === mediaItems.length - 1;
  modal.classList.add('open');
}
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function getPath() { return decodeURIComponent(location.hash.slice(1)); }
function setPath(p) { location.hash = encodeURIComponent(p); }

async function load() {
  const path = getPath();
  const res = await fetch('/api/list?path=' + encodeURIComponent(path));
  if (!res.ok) { grid.innerHTML = '<div class="empty">エラー: ' + res.status + '</div>'; return; }
  const data = await res.json();
  renderCrumbs(path);
  renderGrid(data.entries);
}

function renderCrumbs(path) {
  const parts = path.split('/').filter(Boolean);
  const out = ['<a href="#">root</a>'];
  let acc = '';
  for (const part of parts) {
    acc = acc ? acc + '/' + part : part;
    out.push('<span class="sep">/</span><a href="#' + encodeURIComponent(acc) + '">' + escapeHtml(part) + '</a>');
  }
  crumbs.innerHTML = out.join('');
}

function renderGrid(entries) {
  grid.innerHTML = '';
  mediaItems = entries.filter(e => e.type === 'image' || e.type === 'video');
  if (!entries.length) { grid.innerHTML = '<div class="empty">空のフォルダです</div>'; return; }
  for (const e of entries) {
    const tile = document.createElement('div');
    tile.className = 'tile';
    const thumb = document.createElement('div');
    thumb.className = 'thumb';
    if (e.thumb) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.src = '/thumb?path=' + encodeURIComponent(e.thumb);
      img.onerror = () => { thumb.innerHTML = '<div class="icon">' + (e.type === 'folder' ? '&#128193;' : '&#128196;') + '</div>'; };
      thumb.appendChild(img);
    } else {
      thumb.innerHTML = '<div class="icon">' + (e.type === 'folder' ? '&#128193;' : '&#128196;') + '</div>';
    }
    if (e.type === 'folder') {
      const b = document.createElement('div'); b.className = 'badge'; b.textContent = 'DIR'; thumb.appendChild(b);
    } else if (e.type === 'video') {
      const b = document.createElement('div'); b.className = 'badge'; b.textContent = '\u25B6'; thumb.appendChild(b);
    }
    tile.appendChild(thumb);
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = e.name;
    name.title = e.name;
    tile.appendChild(name);
    tile.onclick = () => {
      if (e.type === 'folder') setPath(e.path);
      else {
        const idx = mediaItems.indexOf(e);
        if (idx >= 0) showAt(idx);
      }
    };
    grid.appendChild(tile);
  }
}

window.addEventListener('hashchange', load);
load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, p: Path, content_type: str):
        size = p.stat().st_size
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                length = max(0, end - start + 1)
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                try:
                    with open(p, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(64 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        try:
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif u.path == "/api/list":
                self._handle_list(q.get("path", [""])[0])
            elif u.path == "/thumb":
                self._handle_thumb(q.get("path", [""])[0])
            elif u.path == "/file":
                self._handle_file(q.get("path", [""])[0])
            else:
                self._send(404, b"not found", "text/plain")
        except PermissionError:
            self._send(403, b"forbidden", "text/plain")
        except FileNotFoundError:
            self._send(404, b"not found", "text/plain")
        except Exception as e:
            sys.stderr.write(f"handler error: {e}\n")
            self._send(500, str(e).encode(), "text/plain")

    def _handle_list(self, rel: str):
        folder = safe_resolve(rel)
        if not folder.is_dir():
            self._send(404, b"not a directory", "text/plain")
            return
        try:
            items = sorted(folder.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            items = []
        entries = []
        for item in items:
            if item.name.startswith(".") or item.name == CACHE_DIR_NAME:
                continue
            if item.is_dir():
                first = first_media_recursive(item)
                entries.append({
                    "type": "folder",
                    "name": item.name,
                    "path": rel_from_root(item),
                    "thumb": rel_from_root(first) if first else None,
                })
            elif item.is_file():
                if is_image(item):
                    t = "image"
                elif is_video(item):
                    t = "video"
                else:
                    continue
                entries.append({
                    "type": t,
                    "name": item.name,
                    "path": rel_from_root(item),
                    "thumb": rel_from_root(item),
                })
        body = json.dumps({"path": rel, "entries": entries}).encode("utf-8")
        self._send(200, body, "application/json")

    def _handle_thumb(self, rel: str):
        p = safe_resolve(rel)
        if not p.is_file() or not is_media(p):
            self._send(404, b"not a media file", "text/plain")
            return
        thumb = ensure_thumbnail(p)
        if not thumb or not thumb.exists():
            self._send(500, b"thumbnail failed", "text/plain")
            return
        self._send_file(thumb, "image/jpeg")

    def _handle_file(self, rel: str):
        p = safe_resolve(rel)
        if not p.is_file():
            self._send(404, b"not a file", "text/plain")
            return
        ct, _ = mimetypes.guess_type(str(p))
        self._send_file(p, ct or "application/octet-stream")


def have_ffmpeg() -> bool:
    try:
        r = subprocess.run([FFMPEG, "-version"], capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def main():
    global ROOT, CACHE_DIR
    ap = argparse.ArgumentParser(description="Folder image/video browser")
    ap.add_argument("path", nargs="?", default=".", help="root folder (default: cwd)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    ROOT = Path(args.path).resolve()
    if not ROOT.is_dir():
        print(f"not a directory: {ROOT}", file=sys.stderr)
        sys.exit(1)
    CACHE_DIR = ROOT / CACHE_DIR_NAME
    if not have_ffmpeg():
        sys.stderr.write("warning: ffmpeg not found — video thumbnails will fail\n")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving {ROOT} at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
