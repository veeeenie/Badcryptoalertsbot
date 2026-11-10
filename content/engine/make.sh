#!/usr/bin/env bash
# Usage: content/engine/make.sh content/episodes/<slug>.json [stills t1 t2 ...]
# Builds the episode into content/out/<slug>.mp4. First run installs the voice model, fonts and Python packages.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; export CACHE="$HERE/../.cache"; mkdir -p "$CACHE"
EP="$1"; SLUG="$(basename "$EP" .json)"; export WORK="$HERE/../out/$SLUG"; mkdir -p "$WORK"
python3 -c "import kokoro_onnx, soundfile, scipy, PIL" 2>/dev/null || pip install -q --break-system-packages kokoro-onnx soundfile scipy pillow
[ -f "$CACHE/kokoro.onnx" ] || curl -sL -o "$CACHE/kokoro.onnx" https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
[ -f "$CACHE/voices.bin" ] || curl -sL -o "$CACHE/voices.bin" https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
if [ ! -d "$CACHE/inter" ]; then curl -sL -o "$CACHE/inter.zip" https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip && unzip -q -o "$CACHE/inter.zip" -d "$CACHE/inter"; fi
[ -f /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf ] || [ -f "$CACHE/NotoColorEmoji.ttf" ] || curl -sL -o "$CACHE/NotoColorEmoji.ttf" https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts/NotoColorEmoji.ttf
python3 "$HERE/voice.py" "$EP" "$WORK"
if [ "${2:-}" = "stills" ]; then shift 2; python3 "$HERE/render.py" stills "$@"; echo "stills in $WORK"; exit 0; fi
python3 "$HERE/audio.py" "$WORK"
for i in 0 1 2 3; do python3 "$HERE/render.py" $i 4 & done; wait
for i in 0 1 2 3; do [ -f "$WORK/part$i.mp4" ] || { echo "part $i failed"; exit 1; }; echo "file 'part$i.mp4'"; done > "$WORK/list.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$WORK/list.txt" -c copy "$WORK/video.mp4"
ffmpeg -loglevel error -y -i "$WORK/video.mp4" -i "$WORK/mix.wav" -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest "$HERE/../out/$SLUG.mp4"
echo "done: content/out/$SLUG.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$HERE/../out/$SLUG.mp4") s)"
