#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скачивание ПОЛНОЙ папки с Яндекс.Диска по публичной ссылке — бесплатно, без подписки.

Обходит ограничение веб-версии («нельзя скачать папку/несколько файлов»),
работая через официальный API: каждая папка просматривается, каждый файл
скачивается по отдельности — ровно так, как сейчас разрешает Яндекс.

Требуется только Python 3 (уже есть в Windows 10/11, macOS и Linux).
Никакие библиотеки ставить НЕ нужно.

ПРИМЕРЫ ЗАПУСКА (в командной строке, в папке со скриптом):

  python yadisk_download.py "https://disk.yandex.ru/d/eTBvgLOraPcM6g"
      — скачать всю папку «Виго» в подпапку ./yadisk_export

  python yadisk_download.py "https://disk.yandex.ru/d/eTBvgLOraPcM6g" --dry-run
      — только показать, что лежит в папке (без скачивания)

  python yadisk_download.py "https://disk.yandex.ru/d/eTBvgLOraPcM6g" -o D:\\фото
      — скачать в указанную папку

  python yadisk_download.py "https://disk.yandex.ru/d/eTBvgLOraPcM6g" --path "/Анатомический"
      — скачать только одну подпапку

Скрипт можно запускать повторно: уже скачанные файлы пропускаются,
при обрыве связи/ошибке Яндекса он ждёт и повторяет попытку.
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) yadisk-folder-downloader/1.0"
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def human_size(n):
    if n is None:
        return "?"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.1f} {unit}" if unit != "Б" else f"{n} Б"
        n /= 1024


def sanitize(name):
    """Подгоняем имя файла под требования Windows/macOS/Linux."""
    name = BAD_CHARS.sub("_", name).strip().rstrip(". ")
    return name or "_"


def api_get(params, tries=6):
    """GET-запрос к API с терпеливыми повторами при перегрузке (429/5xx)."""
    url = API + "?" + "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    delay = 5
    for attempt in range(1, tries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries:
                print(f"    API вернул {e.code}, ждём {delay} с (попытка {attempt}/{tries})...")
                time.sleep(delay)
                delay = min(delay * 2, 300)
            else:
                raise
        except (URLError, TimeoutError, OSError):
            if attempt < tries:
                print(f"    Нет ответа от сервера, ждём {delay} с...")
                time.sleep(delay)
                delay = min(delay * 2, 300)
            else:
                raise
    raise RuntimeError("не удалось выполнить запрос")


def list_dir(public_key, path, offset0=0):
    """Список объектов в папке (с учётом постранички)."""
    items, offset = [], offset0
    while True:
        data = api_get({"public_key": public_key, "path": path, "limit": 100, "offset": offset})
        emb = data.get("_embedded", {})
        batch = emb.get("items", [])
        items.extend(batch)
        total = emb.get("total", len(items))
        offset += len(batch)
        if not batch or offset >= total:
            return items


def collect_files(public_key, path, prefix=""):
    """Рекурсивно собрать все файлы папки, сохранив структуру."""
    files = []
    for it in list_dir(public_key, path):
        rel = f"{prefix}/{it['name']}" if prefix else it["name"]
        if it["type"] == "dir":
            print(f"  папка: {rel}/")
            files.extend(collect_files(public_key, it["path"], rel))
        else:
            files.append({
                "rel": rel,
                "path": it["path"],
                "size": it.get("size") or 0,
                "url": it.get("file"),
            })
    return files


def get_direct_url(public_key, path):
    """Получить свежую прямую ссылку на файл (старые ссылки истекают)."""
    meta = api_get({"public_key": public_key, "path": path})
    return meta.get("file")


def download_file(public_key, f, dest_root, pace, size_known=True):
    """Скачать один файл. Возвращает 'ok', 'skip' или 'fail'."""
    parts = [sanitize(p) for p in f["rel"].split("/")]
    dest = os.path.join(dest_root, *parts)

    if size_known and os.path.exists(dest) and os.path.getsize(dest) == f["size"]:
        print(f"  [уже скачан] {f['rel']}")
        return "skip"

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"
    url = f["url"]

    for attempt in range(1, 7):
        try:
            if not url:
                url = get_direct_url(public_key, f["path"])
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=120) as resp, open(part, "wb") as out:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(part, dest)
            if pace:
                time.sleep(pace)
            return "ok"
        except HTTPError as e:
            if e.code in (403, 404) and attempt < 6:
                url = None  # ссылка истекла — получим свежую
                continue
            if e.code in (429, 500, 502, 503, 504) and attempt < 6:
                wait = min(5 * 2 ** (attempt - 1), 300)
                print(f"    ошибка {e.code}, повтор через {wait} с...")
                time.sleep(wait)
                continue
            print(f"  [ОШИБКА {e.code}] {f['rel']}")
            return "fail"
        except (URLError, TimeoutError, OSError):
            if attempt < 6:
                wait = min(5 * 2 ** (attempt - 1), 300)
                print(f"    обрыв связи, повтор через {wait} с...")
                time.sleep(wait)
                continue
            print(f"  [ОШИБКА СЕТИ] {f['rel']}")
            return "fail"
    print(f"  [ОШИБКА] {f['rel']}")
    return "fail"


def main():
    ap = argparse.ArgumentParser(description="Скачать папку Яндекс.Диска по публичной ссылке")
    ap.add_argument("link", help="публичная ссылка, напр. https://disk.yandex.ru/d/XXXX")
    ap.add_argument("-o", "--output", default="yadisk_export", help="куда сохранить (по умолчанию ./yadisk_export)")
    ap.add_argument("--path", default="", help='скачать только подпапку, напр. "/Анатомический"')
    ap.add_argument("--dry-run", action="store_true", help="только показать содержимое, ничего не качать")
    ap.add_argument("--pace", type=float, default=0.3, help="пауза между файлами в секундах (по умолчанию 0.3)")
    args = ap.parse_args()

    public_key = args.link.strip()
    start_path = "/" + args.path.strip("/") if args.path.strip("/") else "/"

    print(f"Читаем содержимое: {public_key}")
    try:
        files = collect_files(public_key, start_path)
    except HTTPError as e:
        sys.exit(f"Яндекс API вернул ошибку {e.code}. Проверьте ссылку и попробуйте позже.")
    except Exception as e:
        sys.exit(f"Не удалось получить список файлов: {e}")

    total_size = sum(f["size"] for f in files)
    print(f"\nНайдено файлов: {len(files)}, общий размер: {human_size(total_size)}")

    if args.dry_run:
        print("\n--- Содержимое ---")
        for f in files:
            print(f"  {f['rel']}  ({human_size(f['size'])})")
        print("\n--dry-run: ничего не скачивалось. Запустите без --dry-run для скачивания.")
        return

    print(f"\nСкачиваем в: {os.path.abspath(args.output)}\n")
    ok = skip = 0
    failed = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f['rel']} ({human_size(f['size'])})")
        r = download_file(public_key, f, args.output, args.pace)
        if r == "ok":
            ok += 1
        elif r == "skip":
            skip += 1
        else:
            failed.append(f["rel"])

    print(f"\nГотово. Скачано: {ok}, пропущено (уже были): {skip}, ошибок: {len(failed)}")
    if failed:
        with open("failed.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(failed))
        print("Список нескачанных файлов сохранён в failed.txt — просто запустите скрипт ещё раз.")
    else:
        print("Все файлы на месте ✔")


if __name__ == "__main__":
    main()
