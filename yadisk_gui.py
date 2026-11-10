#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ГРАФИЧЕСКОЕ ОКНО для скачивания папки с Яндекс.Диска по публичной ссылке.

- Вставляете ссылку, видите имя папки (по умолчанию — как в ссылке),
  при желании меняете папку назначения кнопкой «Обзор…».
- Жмёте «Скачать» — файлы качаются со структурой подпапок.
- Ошибки Яндекса (429/500/502…) обходятся автоматически: проблемные файлы
  пропускаются и докачиваются в конце, а кнопка «Скачать» докачивает остальное.

Запуск: двойной клик по «Скачать фото.bat»  или:  python yadisk_gui.py
Требуется Python 3 (tkinter входит в стандартную установку). Библиотеки не нужны.
"""

import json
import os
import queue
import random
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    TK_OK = True
except ImportError:
    TK_OK = False

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) yadisk-folder-downloader/2.0 (gui)"
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DEFAULT_LINK = "https://disk.yandex.ru/d/eTBvgLOraPcM6g"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ошибки Яндекса, при которых помогает повтор
RETRY_CODES = (429, 500, 502, 503, 504)


def human_size(n):
    if not n:
        return "0 Б"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.1f} {unit}" if unit != "Б" else f"{int(n)} Б"
        n /= 1024


def sanitize(name):
    name = BAD_CHARS.sub("_", name).strip().rstrip(". ")
    return name or "_"


# ----------------------------------------------------------------------------
#  Работа с API Яндекс.Диска
# ----------------------------------------------------------------------------

def api_get(params, tries=6):
    """GET к API с терпеливыми повторами (429/500/502/503/504) и случайными паузами."""
    url = API + "?" + "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    delay = 5
    for attempt in range(1, tries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except HTTPError as e:
            if e.code in RETRY_CODES and attempt < tries:
                time.sleep(delay + random.uniform(0, 3))
                delay = min(delay * 2, 300)
            else:
                raise
        except (URLError, TimeoutError, OSError):
            if attempt < tries:
                time.sleep(delay + random.uniform(0, 3))
                delay = min(delay * 2, 300)
            else:
                raise
    raise RuntimeError("API не ответил")


def get_folder_name(link):
    """Имя корневой папки по ссылке (для поля «Сохранить в»)."""
    data = api_get({"public_key": link})
    return data.get("name") or "yadisk_export"


def list_dir(link, path):
    items, offset = [], 0
    while True:
        data = api_get({"public_key": link, "path": path, "limit": 100, "offset": offset})
        emb = data.get("_embedded", {})
        batch = emb.get("items", [])
        items.extend(batch)
        total = emb.get("total", len(items))
        offset += len(batch)
        if not batch or offset >= total:
            return items


def collect_files(link, path, prefix="", on_folder=None):
    """Рекурсивно собрать все файлы, сохранив структуру подпапок."""
    files = []
    for it in list_dir(link, path):
        rel = f"{prefix}/{it['name']}" if prefix else it["name"]
        if it["type"] == "dir":
            if on_folder:
                on_folder(rel)
            files.extend(collect_files(link, it["path"], rel, on_folder))
        else:
            files.append({
                "rel": rel,
                "path": it["path"],
                "size": it.get("size") or 0,
                "url": it.get("file"),
            })
    return files


def get_direct_url(link, path):
    """Свежая прямая ссылка на файл (старые истекают)."""
    meta = api_get({"public_key": link, "path": path})
    return meta.get("file")


# ----------------------------------------------------------------------------
#  Окно
# ----------------------------------------------------------------------------

class App:
    PACE = 0.3          # пауза между файлами, сек
    MAX_TRIES = 4       # попыток на файл за один проход
    PASSES = 3          # проходов всего (1 + 2 докачки проблемных)

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self._setting_dest = False
        self.dest_edited = False

        root.title("Скачивание фото с Яндекс.Диска")
        root.geometry("680x560")
        root.minsize(560, 460)

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(6, weight=1)

        ttk.Label(frm, text="Ссылка на папку Яндекс.Диска:").grid(row=0, column=0, sticky="w")
        self.link_var = tk.StringVar(value=DEFAULT_LINK)
        ent = ttk.Entry(frm, textvariable=self.link_var)
        ent.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 10), ipady=3)
        ent.bind("<FocusOut>", self._on_link_change)
        ent.bind("<Return>", self._on_link_change)

        ttk.Label(frm, text="Сохранить в папку:").grid(row=2, column=0, sticky="w")
        self.dest_var = tk.StringVar()
        self.dest_var.trace_add("write", self._on_dest_write)
        ttk.Entry(frm, textvariable=self.dest_var).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(2, 10), ipady=3)
        ttk.Button(frm, text="Обзор…", command=self.browse).grid(
            row=3, column=2, sticky="ew", padx=(8, 0), pady=(2, 10))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.start_btn = ttk.Button(btns, text="▶  Скачать", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■  Стоп", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.retry_btn = ttk.Button(btns, text="↻  Докачать нескачанное", command=self.start)
        self.retry_btn.pack(side="left", padx=(8, 0))
        self.retry_btn.state(["disabled"])

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 4))

        self.current_lbl = ttk.Label(frm, text="Готов к работе")
        self.current_lbl.grid(row=6 - 1, column=0, columnspan=3, sticky="w")

        self.log = tk.Text(frm, height=14, wrap="word", state="disabled",
                           font=("Consolas", 9), background="#f7f7f7")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(6, 4))

        self.status_lbl = ttk.Label(frm, text="", foreground="#555555")
        self.status_lbl.grid(row=7, column=0, columnspan=3, sticky="w")

        self._set_dest(os.path.join(SCRIPT_DIR, "yadisk_export"))
        self.root.after(200, self.pump)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        threading.Thread(target=self._fetch_name, daemon=True).start()

    # ---------- мелкие помощники ----------

    def _set_dest(self, path):
        self._setting_dest = True
        self.dest_var.set(path)
        self._setting_dest = False

    def _on_dest_write(self, *_):
        if not self._setting_dest:
            self.dest_edited = True

    def _on_link_change(self, _event=None):
        if self.worker and self.worker.is_alive():
            return
        threading.Thread(target=self._fetch_name, daemon=True).start()

    def _fetch_name(self):
        link = self.link_var.get().strip()
        if not link:
            return
        try:
            name = get_folder_name(link)
        except Exception:
            return
        self.q.put(("name", name))

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.dest_var.get() or SCRIPT_DIR)
        if d:
            self.dest_edited = True
            self._set_dest(d)

    def post(self, kind, payload):
        self.q.put((kind, payload))

    def pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "name":
                    if not self.dest_edited:
                        self._set_dest(os.path.join(SCRIPT_DIR, sanitize(payload)))
                elif kind == "status":
                    self.current_lbl.config(text=payload)
                elif kind == "bar":
                    cur, total = payload
                    self.progress.config(maximum=max(total, 1), value=cur)
                elif kind == "bar_wait":
                    self.progress.config(mode="indeterminate")
                    self.progress.start(12)
                elif kind == "bar_ready":
                    self.progress.stop()
                    self.progress.config(mode="determinate")
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def _append_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------- запуск / стоп ----------

    def start(self):
        link = self.link_var.get().strip()
        dest = self.dest_var.get().strip()
        if "disk.yandex.ru" not in link and "yadi.sk" not in link:
            messagebox.showwarning("Проверьте ссылку",
                                   "Похоже, это не ссылка Яндекс.Диска.\n"
                                   "Нужна вида https://disk.yandex.ru/d/...")
            return
        if not dest:
            messagebox.showwarning("Папка назначения", "Укажите, куда сохранять файлы.")
            return

        self.stop_event.clear()
        self.start_btn.state(["disabled"])
        self.retry_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self.worker = threading.Thread(target=self._run, args=(link, dest), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.status_lbl.config(text="Останавливаемся…")
        self._append_log("\n— Остановлено пользователем —")

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askokcancel("Выход", "Скачивание идёт. Выйти и прервать?"):
                return
            self.stop_event.set()
        self.root.destroy()

    # ---------- рабочий поток ----------

    def _sleep(self, sec):
        """Сон с проверкой остановки. True — если нужно прерваться."""
        end = time.time() + sec
        while time.time() < end:
            if self.stop_event.is_set():
                return True
            time.sleep(0.1)
        return False

    def _run(self, link, dest):
        ok = skipped = 0
        failed = []
        try:
            self.post("bar_wait", None)
            self.post("status", "Сканируем папку по ссылке…")
            self.post("log", f"Читаем содержимое: {link}")
            files = collect_files(link, "/", on_folder=lambda p: self.post("log", f"  папка: {p}/"))
            self.post("bar_ready", None)

            total = len(files)
            total_size = sum(f["size"] for f in files)
            self.post("log", f"\nНайдено файлов: {total}, общий размер: {human_size(total_size)}\n")
            self.post("bar", (0, total))

            todo = files
            for pass_no in range(1, self.PASSES + 1):
                if pass_no > 1:
                    if not todo:
                        break
                    self.post("log", f"\n— Проход {pass_no}: докачиваем {len(todo)} проблемных файлов —")
                    if self._sleep(15):
                        return self._finish(ok, skipped, failed, dest, stopped=True)
                still = []
                for f in todo:
                    if self.stop_event.is_set():
                        return self._finish(ok, skipped, failed + [x["rel"] for x in todo], dest, stopped=True)
                    self.post("status", f"{f['rel']}  ({human_size(f['size'])})")
                    r = self.download_one(link, f, dest)
                    if r == "ok":
                        ok += 1
                        if f["rel"] in failed:
                            failed.remove(f["rel"])   # докачали на повторе
                    elif r == "skip":
                        skipped += 1
                        if f["rel"] in failed:
                            failed.remove(f["rel"])
                    elif r == "cancel":
                        return self._finish(ok, skipped, failed + [f["rel"]] + [x["rel"] for x in todo[todo.index(f) + 1:]], dest, stopped=True)
                    else:
                        failed.append(f["rel"])
                        still.append(f)
                    self.post("bar", (ok + skipped, total))
                todo = still

            self._finish(ok, skipped, failed, dest, stopped=False)
        except Exception as e:
            self.post("log", f"\nОШИБКА: {e}")
            self.post("bar_ready", None)
            self.post("done", {"ok": ok, "skip": skipped, "failed": failed, "stopped": True})

    def download_one(self, link, f, dest):
        """Скачать один файл с повторами. Возвращает ok/skip/fail/cancel."""
        parts = [sanitize(p) for p in f["rel"].split("/")]
        dst = os.path.join(dest, *parts)

        if os.path.exists(dst) and os.path.getsize(dst) == f["size"]:
            self.post("log", f"  [уже скачан] {f['rel']}")
            return "skip"

        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        part = dst + ".part"
        url = f.get("url")

        for attempt in range(1, self.MAX_TRIES + 1):
            if self.stop_event.is_set():
                return "cancel"
            try:
                if not url:
                    url = get_direct_url(link, f["path"])
                req = Request(url, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=120) as resp, open(part, "wb") as out:
                    while True:
                        if self.stop_event.is_set():
                            return "cancel"
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                os.replace(part, dst)
                self.post("log", f"  [ok] {f['rel']}")
                if self._sleep(self.PACE):
                    return "cancel"
                return "ok"
            except HTTPError as e:
                if e.code in (403, 404):
                    url = None  # ссылка истекла — возьмём свежую
                    continue
                if e.code in RETRY_CODES and attempt < self.MAX_TRIES:
                    wait = min(5 * 2 ** (attempt - 1), 60) + random.uniform(0, 4)
                    self.post("log", f"  [!] {e.code} у «{f['rel']}», повтор через {wait:.0f} с…")
                    url = None
                    if self._sleep(wait):
                        return "cancel"
                    continue
                self.post("log", f"  [ОШИБКА {e.code}] {f['rel']}")
                return "fail"
            except (URLError, TimeoutError, OSError):
                if attempt < self.MAX_TRIES:
                    wait = min(5 * 2 ** (attempt - 1), 60) + random.uniform(0, 4)
                    self.post("log", f"  [!] нет связи с «{f['rel']}», повтор через {wait:.0f} с…")
                    url = None
                    if self._sleep(wait):
                        return "cancel"
                    continue
                self.post("log", f"  [ОШИБКА СЕТИ] {f['rel']}")
                return "fail"
        return "fail"

    def _finish(self, ok, skipped, failed, dest, stopped):
        self.post("bar_ready", None)
        if failed:
            try:
                with open(os.path.join(dest, "failed.txt"), "w", encoding="utf-8") as fh:
                    fh.write("\n".join(failed))
            except OSError:
                pass
        self.post("done", {"ok": ok, "skip": skipped, "failed": failed, "stopped": stopped})

    def _on_done(self, d):
        self.start_btn.state(["!disabled"])
        self.stop_btn.state(["disabled"])
        ok, skipped, failed = d["ok"], d["skip"], d["failed"]
        summary = (f"Скачано: {ok}   Пропущено (уже были): {skipped}   "
                   f"Ошибок: {len(failed)}")
        self.status_lbl.config(text=summary)
        self.post("log", f"\n{'-' * 40}\n{summary}")
        if failed:
            self.retry_btn.state(["!disabled"])
            self.current_lbl.config(
                text="Часть файлов не скачалась — нажмите «Докачать нескачанное»")
            self.post("log", "Список ошибок сохранён в failed.txt. "
                             "Часто помогает повтор через время (лучше вечером/ночью, "
                             "когда серверы Яндекса менее загружены).")
        else:
            self.current_lbl.config(text="Готово, все файлы на месте ✔")
            if not d["stopped"]:
                messagebox.showinfo("Готово", summary)


def main():
    if not TK_OK:
        print("Не найден tkinter. Переустановите Python 3 с python.org "
              "(стандартная установка включает tkinter).")
        return
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")      # красивая тема Windows
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
