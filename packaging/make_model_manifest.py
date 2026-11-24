"""Pin supported Whisper models to a revision and build a SHA-256 manifest.

This is a release-maintenance tool, not part of the runtime build. Run it only
when intentionally updating model revisions, then review the manifest diff.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "model_manifests.json"

MODELS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}

RUNTIME_FILES = {
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
    "vocabulary.txt",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    api = HfApi()
    manifest = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {},
    }
    with tempfile.TemporaryDirectory(prefix="sensemaker-manifest-") as temp:
        cache = Path(temp)
        for key, repo in MODELS.items():
            info = api.model_info(repo, files_metadata=True, revision="main")
            revision = info.sha
            if not revision:
                raise RuntimeError(f"Could not resolve a revision for {repo}")
            files: dict[str, dict] = {}
            for sibling in info.siblings:
                name = sibling.rfilename
                if name not in RUNTIME_FILES:
                    continue
                if sibling.lfs and sibling.lfs.get("sha256"):
                    digest = sibling.lfs["sha256"]
                else:
                    downloaded = Path(
                        hf_hub_download(
                            repo_id=repo,
                            filename=name,
                            revision=revision,
                            cache_dir=str(cache),
                        )
                    )
                    digest = sha256_file(downloaded)
                files[name] = {
                    "sha256": digest,
                    "size": int(sibling.size or 0),
                }
            required = {"config.json", "model.bin", "tokenizer.json"}
            missing = required - set(files)
            if missing:
                raise RuntimeError(f"{repo} is missing required files: {sorted(missing)}")
            card = info.card_data or {}
            manifest["models"][key] = {
                "repo": repo,
                "revision": revision,
                "license": card.get("license"),
                "files": files,
            }
            print(f"{key:15} {revision}  {len(files)} files")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
