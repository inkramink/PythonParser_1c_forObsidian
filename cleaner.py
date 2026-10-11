#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleaner.py

Удаляет указанную функцию и ВСЕ функции, которые она вызывает
(через [[Имя]]), при условии, что эти вызываемые функции больше
никем другим не вызываются.

Алгоритм:
1. Находим файл целевой функции.
2. Собираем все [[Имя]] из её кода.
3. Для каждой такой функции считаем оставшиеся вызовы.
4. Если вызовов не осталось — удаляем и её (рекурсивно).
5. Саму целевую функцию удаляем всегда.
"""

import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, List, Tuple

OUTPUT_DIR = Path("output")

# Ищем все вхождения [[ИмяФункции]]
MARKED_CALL_RE = re.compile(r'\[\[([А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*)\]\]')

def find_all_md_files() -> List[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return list(OUTPUT_DIR.rglob("*.md"))

def extract_calls(content: str) -> Set[str]:
    """Все имена, обёрнутые в [[ ]]."""
    return {m.lower() for m in MARKED_CALL_RE.findall(content)}

def build_index(files: List[Path]) -> Tuple[
    Dict[str, Path],          # name_lower → Path
    Dict[str, Set[str]],      # who_calls_whom: name → set of called names
    Dict[str, int]            # how_many_times_called: name → count
]:
    name_to_path: Dict[str, Path] = {}
    calls_from: Dict[str, Set[str]] = defaultdict(set)
    call_count: Dict[str, int] = defaultdict(int)

    for path in files:
        name = path.stem.lower()
        name_to_path[name] = path

        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue

        called = extract_calls(content)
        calls_from[name] = called

        for c in called:
            call_count[c] += 1

    return name_to_path, calls_from, call_count

def collect_deletable(
    start: str,
    calls_from: Dict[str, Set[str]],
    call_count: Dict[str, int],
    name_to_path: Dict[str, Path]
) -> Set[str]:
    """
    Собирает множество имён, которые можно удалить.

    - start всегда удаляется.
    - Любая функция, которую вызывает уже удаляемая,
      удаляется ТОЛЬКО если после этого у неё не остаётся
      ни одного вызова.
    """
    start = start.lower()
    to_delete: Set[str] = set()
    remaining = dict(call_count)          # живой счётчик вызовов
    stack = [start]

    while stack:
        current = stack.pop()
        if current in to_delete:
            continue
        if current not in name_to_path:
            # файла нет — просто пропускаем
            continue

        to_delete.add(current)

        # Все функции, которые current вызывает
        for callee in calls_from.get(current, set()):
            if callee in to_delete:
                continue

            # Этот вызов сейчас «пропадёт»
            remaining[callee] = remaining.get(callee, 0) - 1

            # Если больше никто не вызывает — можно удалять.
            if remaining[callee] <= 0:
                stack.append(callee)

    return to_delete

def delete_files(names: Set[str], name_to_path: Dict[str, Path]) -> List[str]:
    deleted = []
    for name in sorted(names):
        path = name_to_path.get(name)
        if path and path.exists():
            rel = path.relative_to(OUTPUT_DIR)
            path.unlink()
            deleted.append(str(rel))

            # Если папка стала пустой — удаляем и её
            parent = path.parent
            if parent != OUTPUT_DIR and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
    return deleted

def main():
    print("=== Удаление функции и всех «висячих» зависимостей ===\n")

    files = find_all_md_files()
    if not files:
        print("В папке output/ нет .md файлов")
        return

    name_to_path, calls_from, call_count = build_index(files)

    # --- спрашиваем имя ---
    while True:
        raw = input("Имя функции, которую нужно удалить: ").strip()
        if not raw:
            print("Имя не может быть пустым")
            continue
        # убираем возможные [[ ]]
        name = raw.strip("[]").strip()
        break

    name_lower = name.lower()

    if name_lower not in name_to_path:
        print(f"\n[ОШИБКА] Функция «{name}» не найдена.")
        print("Доступные функции:")
        for p in sorted(name_to_path.values(), key=lambda x: x.stem.lower()):
            print(f"  • {p.relative_to(OUTPUT_DIR)}")
        return

    # --- собираем, что можно удалить ---
    to_delete = collect_deletable(name, calls_from, call_count, name_to_path)

    print(f"\nБудут удалены ({len(to_delete)} файлов):")
    for n in sorted(to_delete):
        p = name_to_path[n]
        marker = " ← целевая" if n == name_lower else ""
        print(f"  • {p.relative_to(OUTPUT_DIR)}{marker}")

    confirm = input("\nПодтвердить удаление? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes", "д", "да"):
        print("Отменено.")
        return

    deleted = delete_files(to_delete, name_to_path)

    print(f"\nУдалено {len(deleted)} файлов:")
    for d in deleted:
        print(f"  ✓ {d}")

if __name__ == "__main__":
    main()