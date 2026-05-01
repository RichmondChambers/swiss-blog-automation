import json
from pathlib import Path
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGAL_AUTHORITIES_DIR = REPO_ROOT / "knowledge" / "legal_authorities"
MAP_PATH = REPO_ROOT / "authority_pack_map.json"


def iter_paths_from_map(data):
    for section_name in ("pillar_defaults", "subtopic_overrides"):
        section = data.get(section_name, {})
        for key, paths in section.items():
            for path in paths:
                yield section_name, key, path

    for pillar, article_type_map in data.get("article_type_extras", {}).items():
        for article_type, paths in article_type_map.items():
            for path in paths:
                yield "article_type_extras", f"{pillar}:{article_type}", path

    for pillar, complexity_map in data.get("legal_complexity_extras", {}).items():
        for complexity, paths in complexity_map.items():
            for path in paths:
                yield "legal_complexity_extras", f"{pillar}:{complexity}", path


def main():
    with MAP_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    errors = []

    if not LEGAL_AUTHORITIES_DIR.exists():
        errors.append(f"Legal authorities folder not found: {LEGAL_AUTHORITIES_DIR}")

    for section, key, relative_path in iter_paths_from_map(data):
        full_path = LEGAL_AUTHORITIES_DIR / relative_path

        if not full_path.exists():
            errors.append(f"Missing file: [{section}] {key} -> {relative_path}")

    for section_name in ("pillar_defaults", "subtopic_overrides"):
        section = data.get(section_name, {})
        for key, paths in section.items():
            duplicates = [path for path, count in Counter(paths).items() if count > 1]
            for duplicate in duplicates:
                errors.append(f"Duplicate path in {section_name} / {key}: {duplicate}")

    for key in data.get("subtopic_overrides", {}):
        if ":" not in key:
            errors.append(f"Invalid subtopic override key, expected pillar:subtopic: {key}")

    if errors:
        print("Authority pack map validation failed:\n")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print("Authority pack map validation passed.")


if __name__ == "__main__":
    main()
