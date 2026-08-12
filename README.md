# SLMS — Simple Linux Music Sync

A simple Linux desktop app (PySide6) for cataloguing a local music library, editing tags/cover art, and one-way syncing selected tracks/albums/artists to a removable device (pendrive, SD card).

*(Polska wersja poniżej / Polish version below)*

## Features

- Select a music directory, scan it recursively, and build a SQLite database of all tracks (path, filename, hash).
- Incremental rescans: unchanged files (same size + modification time) are skipped, so only new/changed files are re-hashed.
- Supported audio formats: mp3, flac, ogg, wav, m4a, aac, wma.
- Table view (sortable by artist, album, title, track number, year, genre, format, size) and tree view (artist → album → track).
- Edit tags (artist, album, title, track number, tracks-in-album, disc number, year, genre) and cover art per track, or for a whole album at once (artist, album, year, genre, tracks-in-album, cover — title/track number stay untouched).
- Edit the filename directly from the tag editor.
- Next/Previous navigation while editing: track editor moves across all tracks (crossing album/artist boundaries); album editor moves between whole albums.
- Detects mounted removable storage devices (via `lsblk`) and lets you pick a sync target from a dropdown.
- One-way sync (library → device) run manually — never deletes files, and asks before overwriting a file that differs from the source.
- Configurable directory/filename templates for the copies on the device, using placeholders: `{artist} {album} {title} {track} {track_total} {disc} {year} {genre}` (zero-padded numbers). Plain text can be mixed in, e.g. `music/{artist}`.
- Sync the whole library, a single artist, a single album, or a single track from context menus.
- The device gets its own database; tracks already present (by content hash) are marked with an icon, and can be deleted from the device only (source file untouched) via the context menu.
- Safe "Eject" button (`udisksctl unmount` + `power-off`) — same as your desktop's native eject.
- English/Polish UI language (remembered; requires restart to switch) and Light/Dark/Auto theme (remembered; applies live, "Auto" follows the system theme).

## Requirements

- Linux
- Python 3.10+
- `lsblk` and `udisksctl` (standard on most desktop distros) for device detection/eject

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running

```bash
python3 main.py
```

`main.py` automatically re-executes itself with the `.venv` interpreter if PySide6 isn't found in the system Python, so `python3 main.py` works regardless of whether the venv is activated.

## Prebuilt AppImage

A portable, self-contained `SLMS-x86_64.AppImage` (no `.venv`/system Python required) is published on the [Releases page](https://github.com/FloWirus/SLMS/releases) — download it, `chmod +x`, and run. Built and published manually per version, not via CI.

## Data locations

- Library database and app settings (templates, language, theme, last used source directory): `~/.local/share/SLMS/music_db/` (`library.db` and `settings.json`), respecting `$XDG_DATA_HOME` if set. Same location regardless of whether you run from source, `.venv`, or the AppImage — the executable's own location is never used, since an AppImage runs from a fresh temporary mountpoint on every launch.
- On each synced device: `<device>/music_db/device.db`.

## Project layout

```
main.py                       entry point
music_sync/
  constants.py                supported formats, default templates
  db.py                       SQLite access layer (Track, MusicDatabase)
  scanner.py                  directory scanning, hashing
  tags.py                     read/write audio tags and cover art (mutagen)
  devices.py                  removable device detection + eject
  templating.py                {artist}/{album}/... path rendering
  sync.py                     one-way sync logic, conflict handling
  settings.py                 persisted app settings
  i18n.py                     EN/PL translations
  gui/
    main_window.py            main window, table/tree views, menus
    models.py                 table model
    tag_edit_dialog.py        single-track tag editor (Next/Previous)
    album_edit_dialog.py      album-wide tag editor (Next/Previous album)
    settings_dialog.py        templates, language, theme
    theme.py                  light/dark/auto palette handling
```

---

# SLMS — Simple Linux Music Sync (PL)

Prosta aplikacja desktopowa na Linuksa (PySide6) do katalogowania lokalnej biblioteki muzycznej, edycji tagów/okładek oraz jednokierunkowej synchronizacji wybranych utworów/albumów/artystów na nośnik wymienny (pendrive, karta SD).

## Funkcje

- Wybór katalogu z muzyką, rekurencyjne skanowanie i budowa bazy SQLite wszystkich utworów (ścieżka, nazwa pliku, hash).
- Inkrementalne ponowne skanowanie: niezmienione pliki (ten sam rozmiar i data modyfikacji) są pomijane — hashowane są tylko nowe/zmienione pliki.
- Obsługiwane formaty audio: mp3, flac, ogg, wav, m4a, aac, wma.
- Widok tabeli (sortowanie wg artysty, albumu, tytułu, numeru utworu, roku, gatunku, formatu, rozmiaru) oraz widok drzewa (artysta → album → utwór).
- Edycja tagów (artysta, album, tytuł, numer utworu, liczba utworów w albumie, numer płyty, rok, gatunek) i okładki dla pojedynczego utworu lub dla całego albumu naraz (artysta, album, rok, gatunek, liczba utworów, okładka — tytuł i numer utworu pozostają nietknięte).
- Edycja nazwy pliku bezpośrednio z edytora tagów.
- Nawigacja Następny/Poprzedni podczas edycji: edytor utworu przechodzi przez wszystkie utwory (także między albumami/artystami); edytor albumu przechodzi między całymi albumami.
- Wykrywanie podłączonych nośników wymiennych (przez `lsblk`) i wybór celu synchronizacji z listy.
- Synchronizacja jednokierunkowa (biblioteka → nośnik) uruchamiana ręcznie — nigdy nic nie usuwa i pyta przed nadpisaniem pliku różniącego się od źródła.
- Konfigurowalne szablony katalogów/nazw plików dla kopii na nośniku, oparte na znacznikach: `{artist} {album} {title} {track} {track_total} {disc} {year} {genre}` (liczby dopełniane zerami). Można mieszać z dowolnym tekstem, np. `muzyka/{artist}`.
- Synchronizacja całej biblioteki, pojedynczego artysty, albumu lub utworu z menu kontekstowego.
- Nośnik ma własną bazę danych; utwory już na nim obecne (wg hasha zawartości) są oznaczone ikoną i można je usunąć wyłącznie z nośnika (plik źródłowy pozostaje nietknięty) z menu kontekstowego.
- Bezpieczny przycisk "Wysuń" (`udisksctl unmount` + `power-off`) — działa jak natywne wysuwanie w środowisku graficznym.
- Język interfejsu polski/angielski (zapamiętywany, zmiana wymaga restartu) oraz motyw Jasny/Ciemny/Auto (zapamiętywany, stosowany na żywo, "Auto" podąża za motywem systemowym).

## Wymagania

- Linux
- Python 3.10+
- `lsblk` i `udisksctl` (standardowo dostępne w większości dystrybucji z środowiskiem graficznym) do wykrywania nośników i wysuwania

## Instalacja

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Uruchomienie

```bash
python3 main.py
```

`main.py` automatycznie przełącza się na interpreter z `.venv`, jeśli PySide6 nie jest dostępne w systemowym Pythonie — więc `python3 main.py` działa niezależnie od tego, czy venv jest aktywowany.

## Gotowy AppImage

Przenośny, samodzielny plik `SLMS-x86_64.AppImage` (nie wymaga `.venv` ani systemowego Pythona) jest publikowany na [stronie Releases](https://github.com/FloWirus/SLMS/releases) — pobierz, nadaj `chmod +x` i uruchom. Budowany i publikowany ręcznie przy każdej wersji, bez CI.

## Lokalizacja danych

- Baza biblioteki i ustawienia aplikacji (szablony, język, motyw, ostatnio używany katalog źródłowy): `~/.local/share/SLMS/music_db/` (`library.db` i `settings.json`), z uwzględnieniem `$XDG_DATA_HOME` jeśli jest ustawione. Ta sama lokalizacja niezależnie od tego, czy uruchamiasz z kodu źródłowego, `.venv`, czy z AppImage — lokalizacja samego pliku wykonywalnego nigdy nie jest używana, bo AppImage przy każdym uruchomieniu montuje się w innym tymczasowym katalogu.
- Na każdym zsynchronizowanym nośniku: `<nośnik>/music_db/device.db`.

## Struktura projektu

```
main.py                       punkt wejścia
music_sync/
  constants.py                obsługiwane formaty, domyślne szablony
  db.py                       warstwa dostępu do SQLite (Track, MusicDatabase)
  scanner.py                  skanowanie katalogu, hashowanie
  tags.py                     odczyt/zapis tagów audio i okładek (mutagen)
  devices.py                  wykrywanie nośników wymiennych + wysuwanie
  templating.py                renderowanie ścieżek {artist}/{album}/...
  sync.py                     logika synchronizacji jednokierunkowej, konflikty
  settings.py                 zapisywane ustawienia aplikacji
  i18n.py                     tłumaczenia EN/PL
  gui/
    main_window.py            okno główne, widoki tabeli/drzewa, menu
    models.py                 model tabeli
    tag_edit_dialog.py        edytor tagów pojedynczego utworu (Następny/Poprzedni)
    album_edit_dialog.py      edytor tagów albumu (Następny/Poprzedni album)
    settings_dialog.py        szablony, język, motyw
    theme.py                  obsługa palety jasny/ciemny/auto
```
