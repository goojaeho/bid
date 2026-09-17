"""Local Korean transcription. Audio is read from stdin, never written to disk."""
import io
import json
import sys
from faster_whisper import WhisperModel

def main():
    raw = sys.stdin.buffer.read(12 * 1024 * 1024 + 1)
    if not raw or len(raw) > 12 * 1024 * 1024:
        raise ValueError("Invalid recording size")
    model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True)
    segments, info = model.transcribe(io.BytesIO(raw), language="ko", beam_size=3,
        vad_filter=True, condition_on_previous_text=False)
    if info.duration > 65:
        raise ValueError("Recording too long")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    print(json.dumps({"text": text}, ensure_ascii=True))

if __name__ == "__main__":
    main()
