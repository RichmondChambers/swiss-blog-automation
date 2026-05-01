import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGAL_AUTHORITIES_DIR = REPO_ROOT / "knowledge" / "legal_authorities"
MAP_PATH = REPO_ROOT / "authority_pack_map.json"


def collect_mapped_paths(data):
    mapped = set()

    for paths in data.get("pillar_defaults", {}).values():
        mapped.update(paths)

    for paths in data.get("subtopic_overrides", {}).values():
        mapped.update(paths)

    for article_type_map in data.get("article_type_extras", {}).values():
        for paths in article_type_map.values():
            mapped.update(paths)

    for complexity_map in data.get("legal_complexity_extras", {}).values():
        for paths in complexity_map.values():
            mapped.update(paths)

    return mapped


def main():
    with MAP_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    mapped = collect_mapped_paths(data)

    all_pack_paths = []
    for path in LEGAL_AUTHORITIES_DIR.rglob("*.md"):
        if path.name == "README.md":
            continue

        relative_path = path.relative_to(LEGAL_AUTHORITIES_DIR).as_posix()
        all_pack_paths.append(relative_path)

    unmapped = sorted(path for path in all_pack_paths if path not in mapped)

    if not unmapped:
        print("All authority packs are referenced somewhere in authority_pack_map.json.")
        return

    print("Authority packs not currently referenced in authority_pack_map.json:\n")
    for path in unmapped:
        print(f"- {path}")


if __name__ == "__main__":
    main()
