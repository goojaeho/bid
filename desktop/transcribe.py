"""Persistent offline Korean speech worker. Only stdin/stdout; no audio files."""
import base64
import io
import json
import sys
import time
from faster_whisper import WhisperModel

model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True)
wake_model = WhisperModel(sys.argv[2], device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True) if len(sys.argv)>2 else model
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    request = {}
    try:
        request = json.loads(line)
        raw = base64.b64decode(request["audio"], validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            raise ValueError("Invalid audio")
        start = time.perf_counter()
        is_wake = request.get("mode") == "wake"
        selected = wake_model if is_wake else model
        segments, info = selected.transcribe(io.BytesIO(raw), language="ko", beam_size=1,
            vad_filter=True, condition_on_previous_text=False, initial_prompt="자비스" if is_wake else None)
        if info.duration > 65:
            raise ValueError("Recording too long")
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.7).strip()
        print(json.dumps({"id":request["id"], "text":text, "seconds":round(time.perf_counter()-start,3)}, ensure_ascii=True), flush=True)
    except Exception:
        print(json.dumps({"id":request.get("id") if isinstance(request,dict) else None,"error":"음성을 변환하지 못했어요."}),flush=True)
