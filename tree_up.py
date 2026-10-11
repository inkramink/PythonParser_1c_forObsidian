#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tree_up.py — строит дерево ВВЕРХ (кто вызывает указанную функцию).

Спрашивает имя функции, находит всех, кто её вызывает (рекурсивно),
и создаёт файлы в папке:
    дерево вверх функции <ИмяФункции>/
"""

import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, List, Tuple

INPUT_DIR = Path("input")
BANLIST_FILE = Path("banlist.txt")

IDENT = r'[А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*'

PROC_FUNC_RE = re.compile(
    r'(?P<header>'
    r'(?:^\s*&[^\n]+\n)*'
    r'^\s*(?P<type>Процедура|Функция)\s+'
    r'(?P<name>[А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*)\s*'
    r'\((?P<params>[^)]*)\)\s*'
    r'(?P<export>Экспорт)?\s*$'
    r')'
    r'(?P<body>.*?)'
    r'(?P<footer>^\s*(?:КонецПроцедуры|КонецФункции)\s*$)',
    re.IGNORECASE | re.MULTILINE | re.DOTALL
)

CALL_RE = re.compile(
    rf'(?P<prefix>(?:{IDENT})\s*\.\s*)?'
    rf'(?P<name>{IDENT})\s*\(',
    re.IGNORECASE
)

INDIRECT_PATTERNS = [
    (re.compile(rf'(ЗапуститьФоновоеЗадание|ВыполнитьВФоне|ФоновоеЗадание)\s*\(\s*["\']({IDENT})["\']', re.I), 2),
    (re.compile(rf'(Вычислить|ВычислитьВыражение)\s*\(\s*["\']({IDENT})\s*\(', re.I), 2),
    (re.compile(rf'(ПодключитьОбработчикОжидания|ОтключитьОбработчикОжидания)\s*\(\s*["\']({IDENT})["\']', re.I), 2),
    (re.compile(rf'Новый\s+ОписаниеОповещения\s*\(\s*["\']({IDENT})["\']', re.I), 1),
    (re.compile(rf'(ПодпискаНаСобытие|ДобавитьОбработчик)\s*\(\s*[^,]+,\s*["\']({IDENT})["\']', re.I), 2),
]


def load_banlist() -> Set[str]:
    ban = set()
    if BANLIST_FILE.exists():
        with BANLIST_FILE.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("//"):
                    ban.add(line.lower())
    return ban


def protect_strings_and_comments(text: str) -> Tuple[str, List[str]]:
    placeholders = []
    def replacer(m):
        placeholders.append(m.group(0))
        return f"__PH_{len(placeholders)-1}__"
    text = re.sub(r'/\*.*?\*/', replacer, text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', replacer, text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', replacer, text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", replacer, text)
    return text, placeholders


def restore_placeholders(text: str, placeholders: List[str]) -> str:
    for i, orig in enumerate(placeholders):
        text = text.replace(f"__PH_{i}__", orig)
    return text


def extract_calls(body: str, banlist: Set[str]) -> Set[str]:
    """Собирает имена вызываемых функций (без изменения текста)."""
    found = set()
    protected, ph = protect_strings_and_comments(body)

    for m in CALL_RE.finditer(protected):
        name = m.group("name")
        if name.lower() not in banlist:
            found.add(name)

    text = restore_placeholders(protected, ph)
    for pattern, gi in INDIRECT_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(gi)
            if name.lower() not in banlist:
                found.add(name)
    return found


def parse_all_input(banlist: Set[str]) -> Tuple[
    Dict[str, str],          # name_lower → полный код функции
    Dict[str, Set[str]],     # name_lower → кого вызывает
    Dict[str, Set[str]]      # name_lower → кто вызывает эту функцию
]:
    code_of: Dict[str, str] = {}
    calls_from: Dict[str, Set[str]] = defaultdict(set)
    called_by: Dict[str, Set[str]] = defaultdict(set)

    files = list(INPUT_DIR.glob("*.txt")) + list(INPUT_DIR.glob("*.bsl"))
    if not files:
        print("Папка input/ пуста или не существует")
        return code_of, calls_from, called_by

    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in PROC_FUNC_RE.finditer(text):
            name = m.group("name")
            name_lower = name.lower()
            full_code = m.group("header") + m.group("body") + m.group("footer")
            code_of[name_lower] = full_code

            called = extract_calls(m.group("body"), banlist)
            calls_from[name_lower] = {c.lower() for c in called}

            for c in called:
                called_by[c.lower()].add(name_lower)

    return code_of, calls_from, called_by


def collect_upward(start: str, called_by: Dict[str, Set[str]]) -> Set[str]:
    """Все функции, которые прямо или косвенно вызывают start."""
    result = set()
    stack = [start.lower()]
    while stack:
        current = stack.pop()
        for caller in called_by.get(current, set()):
            if caller not in result:
                result.add(caller)
                stack.append(caller)
    return result


def make_file_content(code: str, calls: Set[str], original_names: Dict[str, str]) -> str:
    """Формирует содержимое файла: ссылки + код в ```."""
    # Восстанавливаем оригинальный регистр имён
    links = []
    for c in sorted(calls):
        display = original_names.get(c, c)
        links.append(f"[[{display}]]")
    links_str = "\n".join(links)
    if links_str:
        links_str += "\n"
    return f"{links_str}```\n{code}\n```\n"


def main():
    print("=== Дерево ВВЕРХ (кто вызывает функцию) ===\n")

    if not INPUT_DIR.exists():
        print("Папка input/ не найдена")
        return

    banlist = load_banlist()
    code_of, calls_from, called_by = parse_all_input(banlist)

    if not code_of:
        print("Не найдено ни одной процедуры/функции")
        return

    # Словарь для восстановления регистра
    original_names = {}
    for path in list(INPUT_DIR.glob("*.txt")) + list(INPUT_DIR.glob("*.bsl")):
        text = path.read_text(encoding="utf-8")
        for m in PROC_FUNC_RE.finditer(text):
            original_names[m.group("name").lower()] = m.group("name")

    name = input("Имя функции: ").strip().strip("[]")
    if not name:
        print("Имя пустое")
        return

    name_lower = name.lower()
    if name_lower not in code_of and name_lower not in called_by:
        print(f"Функция «{name}» не найдена ни в определениях, ни в вызовах")
        return

    # Собираем всех, кто вызывает (рекурсивно)
    upward = collect_upward(name_lower, called_by)

    # Добавляем саму функцию, если её код есть
    to_create = set(upward)
    if name_lower in code_of:
        to_create.add(name_lower)

    if not to_create:
        print("Никто не вызывает эту функцию (и её код не найден)")
        return

    folder = Path(f"/trees/tree_up_{original_names.get(name_lower, name)}")
    folder.mkdir(parents=True, exist_ok=True)

    print(f"\nСоздаём {len(to_create)} файлов в «{folder}»:")
    for n in sorted(to_create):
        code = code_of.get(n)
        if not code:
            # Только вызывали, но кода нет
            content = f"#НеизвестнаяФункция\n\n// Код функции не найден\n"
        else:
            content = make_file_content(code, calls_from.get(n, set()), original_names)

        out = folder / f"{original_names.get(n, n)}.md"
        out.write_text(content, encoding="utf-8")
        print(f"  → {out.name}")

    print("\nГотово.")


if __name__ == "__main__":
    main()