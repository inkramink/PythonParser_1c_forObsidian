#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bsl_analyzer.py — единый класс для работы с кодом 1С (BSL):

  • разбиение на процедуры/функции (parser)
  • дерево вниз  — что вызывает функция (tree_down)
  • дерево вверх — кто вызывает функцию (tree_up)
  • очистка     — удаление функции и «висячих» зависимостей (cleaner)

Использование:
    python bsl_analyzer.py              # интерактивный режим
    from bsl_analyzer import BSLAnalyzer
    a = BSLAnalyzer()
    a.parse()
    a.tree_down("ИмяФункции")
    a.tree_up("ИмяФункции")
    a.clean("ИмяФункции")
"""

from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, List, Tuple, Optional


class BSLAnalyzer:
    """
    Анализатор кода 1С.

    Общий функционал:
      - загрузка banlist
      - поиск процедур/функций
      - извлечение прямых и косвенных вызовов
      - построение графа вызовов (calls_from / called_by)
      - генерация markdown-файлов с ссылками [[Имя]]
      - удаление функции и «висячих» зависимостей из output/
    """

    # ====================== КОНФИГУРАЦИЯ ======================
    MAIN_DIR = Path("C:/Users/it-pfm/Desktop/python/PythonParser_1c_forObsidian-main")
    INPUT_DIR = MAIN_DIR / Path("input")
    OUTPUT_DIR = MAIN_DIR / Path("output")
    TREES_DIR = MAIN_DIR / Path("trees")
    BANLIST_FILE = MAIN_DIR / Path("banlist.txt")
    REPORT_FILE = MAIN_DIR / Path("report.txt")

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

    # Ссылки [[Имя]] в уже сгенерированных .md (для cleaner)
    MARKED_CALL_RE = re.compile(r'\[\[([А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*)\]\]')

    # ====================== ИНИЦИАЛИЗАЦИЯ ======================

    def __init__(
        self,
        input_dir: Path | str | None = None,
        output_dir: Path | str | None = None,
        trees_dir: Path | str | None = None,
        banlist_file: Path | str | None = None,
    ):
        self.input_dir = Path(input_dir) if input_dir else self.INPUT_DIR
        self.output_dir = Path(output_dir) if output_dir else self.OUTPUT_DIR
        self.trees_dir = Path(trees_dir) if trees_dir else self.TREES_DIR
        self.banlist_file = Path(banlist_file) if banlist_file else self.BANLIST_FILE

        self.banlist: Set[str] = set()
        self.code_of: Dict[str, str] = {}                    # name_lower → полный код
        self.calls_from: Dict[str, Set[str]] = defaultdict(set)  # кто → кого вызывает
        self.called_by: Dict[str, Set[str]] = defaultdict(set)   # кого → кто вызывает
        self.original_names: Dict[str, str] = {}             # name_lower → оригинальный регистр
        self.origin_of: Dict[str, Path] = {}                 # name_lower → Path исходного файла (относительно input_dir)
        self._parsed = False

    # ====================== ВСПОМОГАТЕЛЬНЫЕ ======================

    def load_banlist(self) -> Set[str]:
        ban: Set[str] = set()
        if not self.banlist_file.exists():
            print(f"[WARN] Бан-лист {self.banlist_file} не найден — работаем без него.")
            return ban
        with self.banlist_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("//"):
                    ban.add(line.lower())
        return ban

    @staticmethod
    def protect_strings_and_comments(text: str) -> Tuple[str, List[str]]:
        placeholders: List[str] = []

        def replacer(m: re.Match) -> str:
            placeholders.append(m.group(0))
            return f"__PH_{len(placeholders)-1}__"

        text = re.sub(r'/\*.*?\*/', replacer, text, flags=re.DOTALL)
        text = re.sub(r'//[^\n]*', replacer, text)
        text = re.sub(r'"(?:\\.|[^"\\])*"', replacer, text)
        text = re.sub(r"'(?:\\.|[^'\\])*'", replacer, text)
        return text, placeholders

    @staticmethod
    def restore_placeholders(text: str, placeholders: List[str]) -> str:
        for i, orig in enumerate(placeholders):
            text = text.replace(f"__PH_{i}__", orig)
        return text

    def extract_calls(self, body: str) -> Set[str]:
        """Собирает имена вызываемых функций (прямые + косвенные)."""
        found: Set[str] = set()
        protected, ph = self.protect_strings_and_comments(body)

        for m in self.CALL_RE.finditer(protected):
            name = m.group("name")
            if name.lower() not in self.banlist:
                found.add(name)

        text = self.restore_placeholders(protected, ph)
        for pattern, gi in self.INDIRECT_PATTERNS:
            for m in pattern.finditer(text):
                name = m.group(gi)
                if name.lower() not in self.banlist:
                    found.add(name)
        return found

    def _is_not_banned(self, name: str) -> bool:
        return name.lower() not in self.banlist

    def _is_user_func(self, name: str, user_funcs: Set[str]) -> bool:
        return name.lower() in {f.lower() for f in user_funcs} and self._is_not_banned(name)

    # ====================== ПАРСИНГ ======================

    def parse_all(self) -> None:
        """
        Читает все .txt / .bsl из input/,
        строит code_of, calls_from, called_by, original_names, origin_of.
        """
        self.banlist = self.load_banlist()
        self.code_of.clear()
        self.calls_from.clear()
        self.called_by.clear()
        self.original_names.clear()
        self.origin_of.clear()

        if not self.input_dir.exists():
            print(f"Папка {self.input_dir} не найдена")
            return

        files = list(self.input_dir.rglob("*.txt")) + list(self.input_dir.rglob("*.bsl"))
        if not files:
            print(f"Папка {self.input_dir} пуста")
            return

        for path in files:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(self.input_dir)

            for m in self.PROC_FUNC_RE.finditer(text):
                name = m.group("name")
                name_lower = name.lower()
                full_code = m.group("header") + m.group("body") + m.group("footer")

                self.code_of[name_lower] = full_code
                self.original_names[name_lower] = name
                self.origin_of[name_lower] = rel

                called = self.extract_calls(m.group("body"))
                called_lower = {c.lower() for c in called}
                self.calls_from[name_lower] = called_lower

                for c in called:
                    self.called_by[c.lower()].add(name_lower)

        self._parsed = True
        print(f"Загружено функций/процедур: {len(self.code_of)}")
        print(f"Бан-лист: {len(self.banlist)} записей")

    def ensure_parsed(self) -> None:
        if not self._parsed:
            self.parse_all()

    # ====================== ГРАФ ВЫЗОВОВ ======================

    def collect_downward(self, start: str) -> Set[str]:
        """Все функции, которые start вызывает прямо или косвенно."""
        result: Set[str] = set()
        stack = [start.lower()]
        while stack:
            current = stack.pop()
            for callee in self.calls_from.get(current, set()):
                if callee not in result:
                    result.add(callee)
                    stack.append(callee)
        return result

    def collect_upward(self, start: str) -> Set[str]:
        """Все функции, которые прямо или косвенно вызывают start."""
        result: Set[str] = set()
        stack = [start.lower()]
        while stack:
            current = stack.pop()
            for caller in self.called_by.get(current, set()):
                if caller not in result:
                    result.add(caller)
                    stack.append(caller)
        return result

    # ====================== ГЕНЕРАЦИЯ ФАЙЛОВ ======================

    def make_file_content(self, code: str, calls: Set[str]) -> str:
        """Формирует содержимое .md: ссылки [[Имя]] + код в ```."""
        links = []
        for c in sorted(calls):
            display = self.original_names.get(c, c)
            links.append(f"[[{display}]]")
        links_str = "\n".join(links)
        if links_str:
            links_str += "\n"
        return f"{links_str}```\n{code}\n```\n"

    def _write_md(self, path: Path, content: str, skip_existing: bool = False) -> bool:
        if skip_existing and path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True

    # ====================== РЕЖИМ: PARSER ======================

    def mark_calls_in_body(
        self,
        body: str,
        user_funcs: Set[str],
        all_marked: Set[str],
    ) -> List[str]:
        """Собирает имена вызываемых пользовательских функций (без изменения текста)."""
        found: List[str] = []
        protected, ph = self.protect_strings_and_comments(body)

        def call_replacer(m: re.Match) -> str:
            prefix = m.group("prefix") or ""
            name = m.group("name")
            if not self._is_not_banned(name):
                return m.group(0)
            if self._is_user_func(name, user_funcs) or prefix:
                all_marked.add(name)
                found.append(name)
            return m.group(0)

        self.CALL_RE.sub(call_replacer, protected)

        text_restored = self.restore_placeholders(protected, ph)
        for pattern, group_idx in self.INDIRECT_PATTERNS:
            for m in pattern.finditer(text_restored):
                name = m.group(group_idx)
                if self._is_not_banned(name):
                    all_marked.add(name)
                    found.append(name)

        # уникальные с сохранением порядка
        seen: Set[str] = set()
        unique: List[str] = []
        for n in found:
            if n.lower() not in seen:
                seen.add(n.lower())
                unique.append(n)
        return unique

    def parse(
        self,
        report_file: Path | str | None = None,
    ) -> None:
        """
        Режим parser.py:
        разбивает исходники на отдельные .md по процедурам/функциям,
        кладёт в output/<имя_файла>/ИмяФункции.md
        """
        self.banlist = self.load_banlist()
        report: List[str] = []
        global_created: Set[str] = set()

        if not self.input_dir.exists():
            self.input_dir.mkdir(parents=True)
            print(f"Создана папка {self.input_dir}. Положите туда .txt / .bsl файлы.")
            return

        # уже существующие файлы — не перезаписываем
        if self.output_dir.exists():
            for md in self.output_dir.rglob("*.md"):
                rel = md.relative_to(self.output_dir)
                if len(rel.parts) >= 2:
                    key = str(rel.with_suffix("")).replace("\\", "/").lower()
                    global_created.add(key)

        files = list(self.input_dir.rglob("*.txt")) + list(self.input_dir.rglob("*.bsl"))
        if not files:
            print("Нет файлов в input/")
            return

        for src_path in sorted(files):
            text = src_path.read_text(encoding="utf-8")
            procs = list(self.PROC_FUNC_RE.finditer(text))
            rel = src_path.relative_to(self.input_dir)
            target_dir = self.output_dir / rel.parent / rel.stem

            if not procs:
                report.append(f"[WARN] В {src_path.name} не найдено процедур/функций")
                continue

            user_funcs = {m.group("name") for m in procs}
            all_marked: Set[str] = set()
            created_in_this_file: Set[str] = set()

            report.append(f"=== {src_path.name} → папка {target_dir}/ ===")
            report.append(f"Найдено процедур/функций: {len(procs)}")
            report.append(f"Пользовательские: {', '.join(sorted(user_funcs))}")

            for m in procs:
                name = m.group("name")
                rel_key = str(rel.parent / rel.stem).replace("\\", "/").lower()
                key = f"{rel_key}/{name}".lower()
                body = m.group("body")
                found = self.mark_calls_in_body(body, user_funcs, all_marked)

                links = "\n".join(f"[[{n}]]" for n in found)
                if links:
                    links += "\n"

                code = m.group("header") + body + m.group("footer")
                content = f"{links}```\n{code}\n```\n"

                out_path = target_dir / f"{name}.md"
                if self._write_md(out_path, content, skip_existing=True):
                    report.append(f"  → создан {target_dir}/{name}.md")
                else:
                    report.append(f"     [SKIP] уже существует: {out_path.name}")

                created_in_this_file.add(name.lower())
                global_created.add(key)

                if found:
                    report.append(f"     вызовы: {', '.join(found)}")

        report_path = Path(report_file) if report_file else self.REPORT_FILE
        report_path.write_text("\n".join(report), encoding="utf-8")
        print(f"\nГотово. Отчёт: {report_path}")
        print("\n".join(report))

    # ====================== РЕЖИМ: TREE DOWN ======================

    def tree_down(self, name: str | None = None) -> None:
        """
        Режим tree_down.py:
        создаёт папку trees/tree_down_<Имя>/ с файлами
        самой функции и всех, кого она вызывает (рекурсивно).
        Структура папок повторяет исходные файлы (как в parse).
        """
        self.ensure_parsed()

        if not self.code_of:
            print("Не найдено ни одной процедуры/функции")
            return

        if name is None:
            name = input("Имя функции: ").strip().strip("[]")
        if not name:
            print("Имя пустое")
            return

        name_lower = name.lower()
        if name_lower not in self.code_of:
            print(f"Функция «{name}» не найдена")
            return

        downward = self.collect_downward(name_lower)
        to_create = set(downward)
        to_create.add(name_lower)

        display_name = self.original_names.get(name_lower, name)
        folder = self.trees_dir / f"tree_down_{display_name}"

        print(f"\nСоздаём {len(to_create)} файлов в «{folder}»:")
        for n in sorted(to_create):
            code = self.code_of.get(n)
            if not code:
                continue

            content = self.make_file_content(code, self.calls_from.get(n, set()))

            origin = self.origin_of.get(n)
            if origin is not None:
                target_dir = folder / origin.parent / origin.stem
            else:
                target_dir = folder

            out = target_dir / f"{self.original_names.get(n, n)}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            print(f"  → {out.relative_to(folder)}")

        print("\nГотово.")

    # ====================== РЕЖИМ: TREE UP ======================

    def tree_up(self, name: str | None = None) -> None:
        """
        Режим tree_up.py:
        создаёт папку trees/tree_up_<Имя>/ с файлами
        самой функции и всех, кто её вызывает (рекурсивно).
        Структура папок повторяет исходные файлы (как в parse).
        """
        self.ensure_parsed()

        if not self.code_of and not self.called_by:
            print("Не найдено ни одной процедуры/функции")
            return

        if name is None:
            name = input("Имя функции: ").strip().strip("[]")
        if not name:
            print("Имя пустое")
            return

        name_lower = name.lower()
        if name_lower not in self.code_of and name_lower not in self.called_by:
            print(f"Функция «{name}» не найдена ни в определениях, ни в вызовах")
            return

        upward = self.collect_upward(name_lower)
        to_create = set(upward)
        if name_lower in self.code_of:
            to_create.add(name_lower)

        if not to_create:
            print("Никто не вызывает эту функцию (и её код не найден)")
            return

        display_name = self.original_names.get(name_lower, name)
        folder = self.trees_dir / f"tree_up_{display_name}"

        print(f"\nСоздаём {len(to_create)} файлов в «{folder}»:")
        for n in sorted(to_create):
            code = self.code_of.get(n)
            if not code:
                continue

            content = self.make_file_content(code, self.calls_from.get(n, set()))

            origin = self.origin_of.get(n)
            if origin is not None:
                target_dir = folder / origin.parent / origin.stem
            else:
                target_dir = folder

            out = target_dir / f"{self.original_names.get(n, n)}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            print(f"  → {out.relative_to(folder)}")

        print("\nГотово.")

    # ====================== РЕЖИМ: CLEANER ======================

    def find_all_md_files(self) -> List[Path]:
        """Все .md в output/ (результат parse)."""
        if not self.output_dir.exists():
            return []
        return list(self.output_dir.rglob("*.md"))

    @staticmethod
    def extract_marked_calls(content: str) -> Set[str]:
        """Все имена из [[Имя]] в содержимом markdown-файла."""
        return {m.lower() for m in BSLAnalyzer.MARKED_CALL_RE.findall(content)}

    def build_output_index(
        self, files: List[Path]
    ) -> Tuple[Dict[str, Path], Dict[str, Set[str]], Dict[str, int]]:
        """
        Индекс по уже сгенерированным .md:
          name_to_path  — name_lower → Path
          calls_from    — name → set of called names (из [[ ]])
          call_count    — name → сколько раз на неё ссылаются
        """
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
            called = self.extract_marked_calls(content)
            calls_from[name] = called
            for c in called:
                call_count[c] += 1

        return name_to_path, calls_from, call_count

    def collect_deletable(
        self,
        start: str,
        calls_from: Dict[str, Set[str]],
        call_count: Dict[str, int],
        name_to_path: Dict[str, Path],
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
        remaining = dict(call_count)
        stack = [start]

        while stack:
            current = stack.pop()
            if current in to_delete:
                continue
            if current not in name_to_path:
                continue

            to_delete.add(current)

            for callee in calls_from.get(current, set()):
                if callee in to_delete:
                    continue
                remaining[callee] = remaining.get(callee, 0) - 1
                if remaining[callee] <= 0:
                    stack.append(callee)

        return to_delete

    def delete_files(
        self, names: Set[str], name_to_path: Dict[str, Path]
    ) -> List[str]:
        """Удаляет файлы и пустые родительские папки. Возвращает список удалённых путей."""
        deleted: List[str] = []
        for name in sorted(names):
            path = name_to_path.get(name)
            if path and path.exists():
                rel = path.relative_to(self.output_dir)
                path.unlink()
                deleted.append(str(rel))
                parent = path.parent
                if (
                    parent != self.output_dir
                    and parent.exists()
                    and not any(parent.iterdir())
                ):
                    parent.rmdir()
        return deleted

    def clean(self, name: str | None = None, confirm: bool = True) -> None:
        """
        Режим cleaner.py:
        удаляет указанную функцию и все «висячие» зависимости
        (функции, которые она вызывала и которые больше никем не вызываются).

        Работает по уже сгенерированным .md в output/.
        """
        print("=== Удаление функции и всех «висячих» зависимостей ===\n")

        files = self.find_all_md_files()
        if not files:
            print("В папке output/ нет .md файлов")
            return

        name_to_path, calls_from, call_count = self.build_output_index(files)

        if name is None:
            while True:
                raw = input("Имя функции, которую нужно удалить: ").strip()
                if not raw:
                    print("Имя не может быть пустым")
                    continue
                name = raw.strip("[]").strip()
                break

        name_lower = name.lower()

        if name_lower not in name_to_path:
            print(f"\n[ОШИБКА] Функция «{name}» не найдена.")
            print("Доступные функции:")
            for p in sorted(name_to_path.values(), key=lambda x: x.stem.lower()):
                print(f"  • {p.relative_to(self.output_dir)}")
            return

        to_delete = self.collect_deletable(
            name, calls_from, call_count, name_to_path
        )

        print(f"\nБудут удалены ({len(to_delete)} файлов):")
        for n in sorted(to_delete):
            p = name_to_path[n]
            marker = " ← целевая" if n == name_lower else ""
            print(f"  • {p.relative_to(self.output_dir)}{marker}")

        if confirm:
            answer = input("\nПодтвердить удаление? [y/N]: ").strip().lower()
            if answer not in ("y", "yes", "д", "да"):
                print("Отменено.")
                return

        deleted = self.delete_files(to_delete, name_to_path)

        print(f"\nУдалено {len(deleted)} файлов:")
        for d in deleted:
            print(f"  ✓ {d}")

    # ====================== ИНТЕРАКТИВНЫЙ РЕЖИМ ======================

    def change_folder(self) -> None:
        print("=== Смена путей ===\n")
        print(f"1. Путь для исходных файлов:  {self.INPUT_DIR}")
        print(f"2. Путь для новых файлов:     {self.OUTPUT_DIR}")
        print(f"3. Путь для файлов деревьев:  {self.TREES_DIR}")
        print("0. Назад\n")

        choice = input("Выберите [1/2/3/0]: ").strip()

        if choice == "1":
            raw = input(f"Новый путь для исходных файлов [{self.INPUT_DIR}]: ").strip()
            if not raw:
                print("Путь не изменён.")
                return
            try:
                self.INPUT_DIR = Path(raw)
            except Exception as e:
                print(f"Ошибка выбора пути к папке: {e}")
                return
            print(f"→ INPUT_DIR = {self.INPUT_DIR}")

        elif choice == "2":
            raw = input(f"Новый путь для новых файлов [{self.OUTPUT_DIR}]: ").strip()
            if not raw:
                print("Путь не изменён.")
                return
            try:
                self.OUTPUT_DIR = Path(raw)
            except Exception as e:
                print(f"Ошибка выбора пути к папке: {e}")
                return
            print(f"→ OUTPUT_DIR = {self.OUTPUT_DIR}")

        elif choice == "3":
            raw = input(f"Новый путь для файлов деревьев [{self.TREES_DIR}]: ").strip()
            if not raw:
                print("Путь не изменён.")
                return
            try:
                self.TREES_DIR = Path(raw)
            except Exception as e:
                print(f"Ошибка выбора пути к папке: {e}")
                return
            print(f"→ TREES_DIR = {self.TREES_DIR}")

        elif choice == "0":
            print("Возврат в меню.")
        else:
            print("Неизвестный выбор.")

    @staticmethod
    def ask_yes_no(question: str, default: bool = True) -> bool:
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

    def interactive(self) -> bool:
        print("=== BSL Analyzer ===\n")
        print("1. Parse          — разбить исходники на .md по процедурам/функциям")
        print("2. Tree Down      — дерево вниз (что вызывает функция)")
        print("3. Tree Up        — дерево вверх (кто вызывает функцию)")
        print("4. Clean          — удалить функцию и «висячие» зависимости")
        print("5. Change Dir     — изменить путь к файлам (исходным, новым, деревьям)")

        print("0. Выход\n")

        choice = input("Выберите режим [1/2/3/4/5/0]: ").strip()

        if choice == "1":
            self.parse()

        elif choice == "2":
            self.tree_down()

        elif choice == "3":
            self.tree_up()

        elif choice == "4":
            self.clean()

        elif choice == "5":
            self.change_folder()

        elif choice == "0":
            print("Выход.")
            return False
        else:
            print("Неизвестный выбор.")
        return True
