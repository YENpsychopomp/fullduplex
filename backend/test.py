from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


def guess_mime(path: Path) -> str:
	ext = path.suffix.lower()
	if ext == ".webm":
		return "audio/webm"
	if ext == ".ogg":
		return "audio/ogg"
	if ext == ".wav":
		return "audio/wav"
	if ext == ".mp3":
		return "audio/mpeg"
	if ext == ".m4a":
		return "audio/mp4"
	return "application/octet-stream"


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Test Groq STT with a local audio file")
	parser.add_argument("--file",default="streams/test.mp3", help="Path to local audio file")
	parser.add_argument("--model", default=os.getenv("GROQ_STT_MODEL", "whisper-large-v3"))
	parser.add_argument("--language", default="", help="Optional ISO-639-1 language, e.g. zh")
	parser.add_argument("--prompt", default="", help="Optional style/context prompt")
	parser.add_argument(
		"--response-format",
		default="json",
		choices=["json", "verbose_json", "text"],
	)
	parser.add_argument("--temperature", type=float, default=0.0)
	return parser.parse_args()


def main() -> int:
	load_dotenv()

	groq_api_url = os.getenv(
		"GROQ_API_URL",
		"https://api.groq.com/openai/v1/audio/transcriptions",
	)
	groq_api_key = os.getenv("GROQ_API_KEY")
	if not groq_api_key:
		print("[ERROR] GROQ_API_KEY not found in environment variables")
		return 1

	args = parse_args()
	audio_path = Path(args.file).expanduser().resolve()
	if not audio_path.exists() or not audio_path.is_file():
		print(f"[ERROR] audio file not found: {audio_path}")
		return 1

	headers = {"Authorization": f"Bearer {groq_api_key}"}
	data = {
		"model": args.model,
		"response_format": args.response_format,
		"temperature": args.temperature,
	}
	if args.prompt.strip():
		data["prompt"] = args.prompt.strip()[:500]
	if args.language.strip():
		data["language"] = args.language.strip()

	mime = guess_mime(audio_path)
	print(f"[INFO] Uploading: {audio_path.name}")
	print(f"[INFO] Size: {audio_path.stat().st_size} bytes")
	print(f"[INFO] MIME: {mime}")
	print(f"[INFO] Model: {args.model}")

	try:
		with audio_path.open("rb") as fh:
			files = {
				"file": (audio_path.name, fh, mime),
			}
			resp = requests.post(
				groq_api_url,
				headers=headers,
				data=data,
				files=files,
				timeout=120,
			)
	except Exception as e:
		print(f"[ERROR] request failed: {e}")
		return 1

	if not resp.ok:
		print(f"[ERROR] HTTP {resp.status_code}")
		print(resp.text)
		return 1

	if args.response_format == "text":
		print("\n=== Transcript ===")
		print(resp.text)
		return 0

	try:
		body = resp.json()
	except Exception as e:
		print(f"[ERROR] invalid JSON response: {e}")
		print(resp.text)
		return 1

	text = (body.get("text") or "").strip() if isinstance(body, dict) else ""
	print("\n=== Transcript ===")
	print(text if text else "<empty>")
	print("\n=== Raw Response Keys ===")
	if isinstance(body, dict):
		print(", ".join(body.keys()))
	else:
		print(type(body).__name__)

	return 0


if __name__ == "__main__":
	main()
	# raise SystemExit(main())

