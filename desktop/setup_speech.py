"""Run with the Python environment containing faster-whisper before packaging."""
import json
import sys
from pathlib import Path
from faster_whisper import download_model

root = Path(__file__).resolve().parent
model = download_model("small", output_dir=str(root / "models" / "whisper-small"))
wake_model = download_model("base", output_dir=str(root / "models" / "whisper-base"))
(root / "speech-runtime.json").write_text(json.dumps({"python": sys.executable, "model": model, "wakeModel": wake_model}), encoding="utf-8")
print("Local speech runtime configured.")
