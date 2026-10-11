#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер кода 1С (BSL):
- разбиение на файлы по процедурам/функциям
- разметка пользовательских вызовов [[Имя]]
- вызовы через общие модули: Модуль.[[Имя]]()
- неизвестные функции → пустой файл + #НеизвестнаяФункция (по желанию)
- структура output/<имя_исходного_файла>/ИмяФункции.md
- повторно созданные файлы не перезаписываются
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

# ====================== КОНФИГУРАЦИЯ ======================
INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
BANLIST_FILE = Path("banlist.txt")
REPORT_FILE = Path("report.txt")

# Директивы компиляции
DIRECTIVE_RE = re.compile(
    r'^\s*(&НаСервере|&НаКлиенте|&НаСервереБезКонтекста|&НаКлиентеНаСервереБезКонтекста'
    r'|&НаКлиентеНаСервере|&После|&Перед|&Вместо|&ИзменениеИКонтроль)\b',
    re.IGNORECASE | re.MULTILINE
)

# Блок процедуры/функции
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

IDENT = r'[А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*'

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

# ====================== ВСПОМОГАТЕЛЬНЫЕ ======================

def load_banlist(path: Path) -> Set[str]:
    ban = set()
    if not path.exists():
        print(f"[WARN] Бан-лист {path} не найден — создаём пустой.")
        return ban
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            ban.add(line.lower())
    return ban

def find_all_procedures(text: str) -> List[Dict]:
    results = []
    for m in PROC_FUNC_RE.finditer(text):
        results.append({
            "name": m.group("name"),
            "type": m.group("type"),
            "full": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "header": m.group("header"),
            "body": m.group("body"),
            "footer": m.group("footer"),
        })
    return results

def is_user_func(name: str, user_funcs: Set[str], banlist: Set[str]) -> bool:
    return name.lower() in {f.lower() for f in user_funcs} and name.lower() not in banlist

def is_not_banned(name: str, banlist: Set[str]) -> bool:
    return name.lower() not in banlist

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

def mark_calls(
    text: str,
    user_funcs: Set[str],
    banlist: Set[str],
    all_marked: Set[str]
) -> List[str]:
    """
    Только собирает имена вызываемых пользовательских функций.
    Текст НЕ изменяет.
    """
    found = []
    protected, ph = protect_strings_and_comments(text)

    def call_replacer(m: re.Match) -> str:
        prefix = m.group("prefix") or ""
        name = m.group("name")

        if not is_not_banned(name, banlist):
            return m.group(0)

        if is_user_func(name, user_funcs, banlist) or prefix:
            all_marked.add(name)
            found.append(name)          # только имя, без префикса
        return m.group(0)               # ничего не меняем

    CALL_RE.sub(call_replacer, protected)

    # Окольные вызовы
    text_restored = restore_placeholders(protected, ph)
    for pattern, group_idx in INDIRECT_PATTERNS:
        for m in pattern.finditer(text_restored):
            name = m.group(group_idx)
            if is_not_banned(name, banlist):
                all_marked.add(name)
                found.append(name)

    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique = []
    for n in found:
        if n.lower() not in seen:
            seen.add(n.lower())
            unique.append(n)

    return unique

def safe_write(path: Path, content: str, report: List[str]) -> bool:
    if path.exists():
        report.append(f"     [SKIP] уже существует: {path.name}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True

def ask_yes_no(question: str, default: bool = True) -> bool:
    """Задаёт вопрос в консоли. Возвращает True/False."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input(question + suffix).strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "д", "да"):
            return True
        if answer in ("n", "no", "н", "нет"):
            return False
        print("Пожалуйста, введите Y/Д или N/Н")

def process_file(
    src_path: Path,
    banlist: Set[str],
    report: List[str],
    global_created: Set[str],
    create_unknown: bool
) -> None:
    text = src_path.read_text(encoding="utf-8")
    procs = find_all_procedures(text)

    source_stem = src_path.stem
    target_dir = OUTPUT_DIR / source_stem

    if not procs:
        report.append(f"[WARN] В {src_path.name} не найдено процедур/функций")
        return

    user_funcs = {p["name"] for p in procs}
    all_marked: Set[str] = set()
    created_in_this_file: Set[str] = set()

    report.append(f"=== {src_path.name} → папка {source_stem}/ ===")
    report.append(f"Найдено процедур/функций: {len(procs)}")
    report.append(f"Пользовательские: {', '.join(sorted(user_funcs))}")

    # 1. Известные процедуры/функции
    for p in procs:
        name = p["name"]
        key = f"{source_stem}/{name}".lower()



        found = mark_calls(p["body"], user_funcs, banlist, all_marked)

        # Ссылки на вызываемые функции
        links = "\n".join(f"[[{name}]]" for name in found)
        if links:
            links += "\n"

        # Код без изменений + обёртка в ```
        code = p["header"] + p["body"] + p["footer"]
        marked = f"{links}```\n{code}\n```\n"

        out_path = target_dir / f"{name}.md"

        if safe_write(out_path, marked, report):
            report.append(f"  → создан {source_stem}/{name}.md")
            created_in_this_file.add(name.lower())
            global_created.add(key)
        else:
            created_in_this_file.add(name.lower())
            global_created.add(key)

        if found:
            report.append(f"     вызовы: {', '.join(found)}")

    # 2. Неизвестные функции (только если пользователь согласился)
    if create_unknown:
        unknown = []
        for name in sorted(all_marked):
            name_lower = name.lower()
            key = f"{source_stem}/{name}".lower()

            if name_lower in created_in_this_file or key in global_created:
                continue

            out_path = target_dir / f"{name}.md"
            content = f"#НеизвестнаяФункция\n\n// Эта функция была вызвана, но исходный код не найден.\n"

            if safe_write(out_path, content, report):
                report.append(f"  → [НЕИЗВЕСТНАЯ] создан пустой {source_stem}/{name}.md")
                unknown.append(name)
                global_created.add(key)

        if unknown:
            report.append(f"  Неизвестные функции: {', '.join(unknown)}")
    else:
        report.append("  Создание файлов неизвестных функций пропущено (по выбору пользователя)")

def main():
    banlist = load_banlist(BANLIST_FILE)
    print(f"Загружен бан-лист: {len(banlist)} записей")

    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir(parents=True)
        print(f"Создана папка {INPUT_DIR}. Положите туда .txt / .bsl файлы.")
        return

    # ===== Вопрос пользователю =====
    create_unknown = ask_yes_no(
        "Создавать пустые файлы для неизвестных функций с тегом #НеизвестнаяФункция?",
        default=True
    )
    print(f"→ {'Будем' if create_unknown else 'Не будем'} создавать файлы неизвестных функций\n")

    report: List[str] = []
    global_created: Set[str] = set()

    if OUTPUT_DIR.exists():
        for md in OUTPUT_DIR.rglob("*.md"):
            rel = md.relative_to(OUTPUT_DIR)
            if len(rel.parts) >= 2:
                key = f"{rel.parts[0]}/{rel.stem}".lower()
                global_created.add(key)

    txt_files = list(INPUT_DIR.glob("*.txt")) + list(INPUT_DIR.glob("*.bsl"))
    if not txt_files:
        print("Нет файлов в /input")
        return

    for f in sorted(txt_files):
        process_file(f, banlist, report, global_created, create_unknown)

    REPORT_FILE.write_text("\n".join(report), encoding="utf-8")
    print(f"\nГотово. Отчёт: {REPORT_FILE}")
    print("\n".join(report))

if __name__ == "__main__":
    main()