# SLMS — Simple Linux Music Sync

A simple Linux desktop app (PySide6) for cataloguing a local music library, editing tags/cover art, and two-way syncing selected tracks/albums/artists with a removable device (pendrive, SD card).

*(Polska wersja poniżej / Polish version below)*

## Features

- Select a music directory, scan it recursively, and build a SQLite database of all tracks (path, filename, hash).
- Incremental rescans: unchanged files (same size + modification time) are skipped, so only new/changed files are re-hashed.
- Supported audio formats: mp3, flac, ogg, wav, m4a, aac, wma.
- Table view (sortable by artist, album, disc number, title, track number, tracks-in-album, year, genre, format, size) and tree view (artist → album → track). Column widths, order, and visibility are remembered per view (table/tree, library/device) and restored on next launch.
- Checkboxes on both the table and tree view let you build a selection of tracks to sync; checking/unchecking is shared and kept in sync between the two views (checking an artist/album cascades to its tracks). A "Sync checked" button per pane syncs just that selection.
- The tree view marks presence on the other side with a check icon: full green check when every track under an artist/album is present, a muted grey check when only some are.
- Edit tags (artist, album, title, track number, tracks-in-album, disc number, year, genre) and cover art per track, or for a whole album at once (artist, album, year, genre, tracks-in-album, cover — title/track number stay untouched). Available on both the library and device side (double-click a track, or use the context menu on a track/album) — device-side edits only touch the copy on the device, never the library file.
- Edit the filename directly from the tag editor.
- Manually picking a cover in the tag/album editor stores it as-is (no automatic resizing) — the library can hold covers at any resolution.
- Next/Previous navigation while editing: track editor moves across all tracks (crossing album/artist boundaries); album editor moves between whole albums.
- Detects mounted removable storage devices (via `lsblk`) and lets you pick a sync target from a dropdown.
- Two-way sync, run manually: library → device, or device → library. Never deletes files, and asks before overwriting a file that differs from the source.
- Configurable directory/filename templates for the copies on the device, using placeholders: `{artist} {album} {title} {track} {track_total} {disc} {year} {genre}` (zero-padded numbers). Plain text can be mixed in, e.g. `music/{artist}`.
- Sync the whole library/device from the toolbar buttons, checked tracks from a pane's "Sync checked" button, or a single artist, album, or track from context menus.
- The device gets its own database; tracks already present are marked with the presence icon (matched against the original library file's hash, so this still works correctly even if the copy on the device was transcoded/had its cover resized), and can be deleted from the device only (source file untouched) via the context menu.
- Delete a track, whole album, or whole artist — from the library (permanently removes the files and their DB rows) or from a device only (source files untouched) — via the context menu on either side. Always asks for confirmation first.
- Safe "Eject" button (`udisksctl unmount` + `power-off`) — same as your desktop's native eject.
- English/Polish UI language (remembered; requires restart to switch) and Light/Dark/Auto theme (remembered; applies live, "Auto" follows the system theme).
- Optional "Convert on sync" checkbox + format dropdown at the bottom of the window (FLAC 16/44.1, FLAC 24/44.1, FLAC 24/48, FLAC 24/96, MP3 320kbps CBR). When enabled, every track copied to a device during that sync is transcoded via `ffmpeg` to the selected format — useful for players that don't need/support high-res FLAC. Only ever applies library → device (device → PC sync is always an untouched copy), never upsamples lossy sources or lossless sources already at/below the target, and its own checkbox state is not remembered between sessions. An optional libsoxr resampler can be enabled in Settings (remembered), if your ffmpeg build supports it.
- A second, independent "Resize cover art" checkbox + size (px)/DPI fields at the bottom of the window shrinks embedded cover art on tracks copied to a device during that sync, keeping the longer side at the given size (aspect ratio kept) and stamping the given DPI. Never upscales — a cover already at or below the target size is left untouched. Same rules as the conversion checkbox: only library → device, the library file's cover is never touched, and neither the checkbox nor the field values are remembered between sessions.
- "TrackNoFix" checkbox in Settings (remembered): zero-pads single-digit track number **tags** embedded in tracks copied to a device during sync (1 → 01, ..., 9 → 09) — the metadata field, not the filename. Only affects the copy on the device, never the library file. (Filenames built from the `{track}` template placeholder are always zero-padded regardless of this checkbox — see "Directory/filename templates" above.)
- Profile dropdown + "Add profile"/"Delete profile" buttons at the bottom of the window save/restore the directory and filename templates together with the conversion and cover-resize checkbox/field state under a name, so you can switch a whole sync configuration (e.g. "phone" vs. "car player") in one click. Selecting a profile applies it immediately; the last-used profile is remembered and re-applied on the next launch.

## Requirements

- Linux
- Python 3.10+
- `lsblk` and `udisksctl` (standard on most desktop distros) for device detection/eject
- `ffmpeg` (with `ffprobe`) if you want to use the "Convert on sync" feature; optionally built with `libsoxr` support for higher-quality resampling

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

- Library database and app settings (directory/filename templates, language, theme, TrackNoFix/libsoxr toggles, saved sync profiles, last used source directory, remembered column layout): `~/.local/share/SLMS/music_db/` (`library.db` and `settings.json`), respecting `$XDG_DATA_HOME` if set. Same location regardless of whether you run from source, `.venv`, or the AppImage — the executable's own location is never used, since an AppImage runs from a fresh temporary mountpoint on every launch.
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
  templating.py              {artist}/{album}/... path rendering
  converter.py                ffmpeg audio conversion (targets, ffmpeg/libsoxr detection)
  cover_utils.py              cover image resizing/re-encoding (used at sync time only)
  sync.py                     two-way sync logic, conflict handling, conversion/cover-resize wiring
  settings.py                 persisted app settings
  i18n.py                     EN/PL translations
  gui/
    main_window.py            main window, table/tree views, menus, checked-selection sync, convert/resize/profile bars, deletion
    models.py                 table model
    icons.py                  hand-drawn checkbox/presence icons
    tag_edit_dialog.py        single-track tag editor (Next/Previous)
    album_edit_dialog.py      album-wide tag editor (Next/Previous album)
    settings_dialog.py        templates, language, theme, libsoxr/TrackNoFix toggles
    theme.py                  light/dark/auto palette handling
```

---

# SLMS — Simple Linux Music Sync (PL)

Prosta aplikacja desktopowa na Linuksa (PySide6) do katalogowania lokalnej biblioteki muzycznej, edycji tagów/okładek oraz dwukierunkowej synchronizacji wybranych utworów/albumów/artystów z nośnikiem wymiennym (pendrive, karta SD).

## Funkcje

- Wybór katalogu z muzyką, rekurencyjne skanowanie i budowa bazy SQLite wszystkich utworów (ścieżka, nazwa pliku, hash).
- Inkrementalne ponowne skanowanie: niezmienione pliki (ten sam rozmiar i data modyfikacji) są pomijane — hashowane są tylko nowe/zmienione pliki.
- Obsługiwane formaty audio: mp3, flac, ogg, wav, m4a, aac, wma.
- Widok tabeli (sortowanie wg artysty, albumu, numeru płyty, tytułu, numeru utworu, liczby utworów w albumie, roku, gatunku, formatu, rozmiaru) oraz widok drzewa (artysta → album → utwór). Szerokość, kolejność i widoczność kolumn są zapamiętywane osobno dla każdego widoku (tabela/drzewo, biblioteka/nośnik) i przywracane przy następnym uruchomieniu.
- Checkboxy zarówno w tabeli, jak i w drzewie pozwalają zbudować zaznaczenie utworów do synchronizacji; zaznaczanie/odznaczanie jest współdzielone i zsynchronizowane między obydwoma widokami (zaznaczenie artysty/albumu kaskadowo zaznacza jego utwory). Przycisk „Synchronizuj zaznaczone” w każdym panelu synchronizuje tylko to zaznaczenie.
- Widok drzewa oznacza obecność po drugiej stronie ikoną „✓”: pełny zielony ✓, gdy wszystkie utwory artysty/albumu są obecne, wyszarzony ✓, gdy tylko część.
- Edycja tagów (artysta, album, tytuł, numer utworu, liczba utworów w albumie, numer płyty, rok, gatunek) i okładki dla pojedynczego utworu lub dla całego albumu naraz (artysta, album, rok, gatunek, liczba utworów, okładka — tytuł i numer utworu pozostają nietknięte). Dostępne zarówno po stronie biblioteki, jak i nośnika (dwuklik na utworze albo menu kontekstowe utworu/albumu) — edycja po stronie nośnika dotyczy tylko kopii na nośniku, nigdy pliku w bibliotece.
- Edycja nazwy pliku bezpośrednio z edytora tagów.
- Ręczny wybór okładki w edytorze tagów/albumu zapisuje ją bez zmian (bez automatycznego skalowania) — biblioteka może trzymać okładki w dowolnej rozdzielczości.
- Nawigacja Następny/Poprzedni podczas edycji: edytor utworu przechodzi przez wszystkie utwory (także między albumami/artystami); edytor albumu przechodzi między całymi albumami.
- Wykrywanie podłączonych nośników wymiennych (przez `lsblk`) i wybór celu synchronizacji z listy.
- Synchronizacja dwukierunkowa uruchamiana ręcznie: biblioteka → nośnik lub nośnik → biblioteka. Nigdy nic nie usuwa i pyta przed nadpisaniem pliku różniącego się od źródła.
- Konfigurowalne szablony katalogów/nazw plików dla kopii na nośniku, oparte na znacznikach: `{artist} {album} {title} {track} {track_total} {disc} {year} {genre}` (liczby dopełniane zerami). Można mieszać z dowolnym tekstem, np. `muzyka/{artist}`.
- Synchronizacja całej biblioteki/nośnika z przycisków na pasku narzędzi, zaznaczonych utworów przyciskiem „Synchronizuj zaznaczone” w danym panelu, albo pojedynczego artysty, albumu lub utworu z menu kontekstowego.
- Nośnik ma własną bazę danych; utwory już na nim obecne są oznaczone ikoną obecności (dopasowanie po hashu oryginalnego pliku z biblioteki, więc działa poprawnie nawet jeśli kopia na nośniku została przekonwertowana/miała zmniejszoną okładkę) i można je usunąć wyłącznie z nośnika (plik źródłowy pozostaje nietknięty) z menu kontekstowego.
- Usuwanie utworu, całego albumu lub całego artysty — z biblioteki (trwale usuwa pliki i wpisy w bazie) albo tylko z nośnika (plik źródłowy nietknięty) — z menu kontekstowego po dowolnej stronie. Zawsze z prośbą o potwierdzenie.
- Bezpieczny przycisk "Wysuń" (`udisksctl unmount` + `power-off`) — działa jak natywne wysuwanie w środowisku graficznym.
- Język interfejsu polski/angielski (zapamiętywany, zmiana wymaga restartu) oraz motyw Jasny/Ciemny/Auto (zapamiętywany, stosowany na żywo, "Auto" podąża za motywem systemowym).
- Opcjonalny checkbox „Konwertuj przy synchronizacji” + rozwijana lista formatu na dole okna (FLAC 16/44,1, FLAC 24/44,1, FLAC 24/48, FLAC 24/96, MP3 320kbps CBR). Gdy włączony, każdy utwór kopiowany na nośnik podczas tej synchronizacji jest przekodowywany przez `ffmpeg` do wybranego formatu — przydatne dla odtwarzaczy, które nie potrzebują/nie obsługują FLAC wysokiej rozdzielczości. Dotyczy wyłącznie kierunku biblioteka → nośnik (synchronizacja nośnik → PC zawsze kopiuje bez zmian), nigdy nie podbija jakości plików stratnych ani bezstratnych już na poziomie targetu lub niższym, a stan samego checkboxa nie jest zapamiętywany między sesjami. Opcjonalny resampler libsoxr można włączyć w Ustawieniach (zapamiętywany), jeśli zainstalowany ffmpeg go obsługuje.
- Drugi, niezależny checkbox „Resize okładki” + pola rozmiaru (px)/DPI na dole okna zmniejsza okładkę wbudowaną w utworach kopiowanych na urządzenie podczas tej synchronizacji, trzymając dłuższy bok na wpisanym rozmiarze (proporcje zachowane) i zapisując wpisane DPI. Nigdy nie powiększa — okładka już mniejsza lub równa docelowemu rozmiarowi zostaje bez zmian. Te same zasady co przy konwersji: dotyczy wyłącznie biblioteka → nośnik, okładka pliku w bibliotece nigdy nie jest ruszana, a ani checkbox, ani wpisane wartości nie są zapamiętywane między sesjami.
- Checkbox „TrackNoFix” w Ustawieniach (zapamiętywany): dopełnia zerem jednocyfrowe numery utworów w **tagu** (metadanych), nie w nazwie pliku (1 → 01, ..., 9 → 09), w utworach kopiowanych na urządzenie podczas sync. Dotyczy tylko kopii na urządzeniu, nigdy pliku w bibliotece. (Nazwy plików budowane ze znacznika `{track}` są zawsze dopełniane zerem niezależnie od tego checkboxa — patrz „Szablony katalogów/nazw plików” wyżej.)
- Rozwijana lista profili + przyciski „Dodaj profil”/„Usuń profil” na dole okna zapisują/przywracają szablon katalogu i nazwy pliku razem ze stanem checkboxów/pól konwersji i resize okładki pod nazwą, więc można przełączyć całą konfigurację synchronizacji (np. „telefon” vs „odtwarzacz w aucie”) jednym kliknięciem. Wybór profilu stosuje go od razu; ostatnio użyty profil jest zapamiętywany i stosowany ponownie przy następnym uruchomieniu.

## Wymagania

- Linux
- Python 3.10+
- `lsblk` i `udisksctl` (standardowo dostępne w większości dystrybucji z środowiskiem graficznym) do wykrywania nośników i wysuwania
- `ffmpeg` (z `ffprobe`), jeśli chcesz korzystać z funkcji „Konwertuj przy synchronizacji”; opcjonalnie zbudowany z obsługą `libsoxr` dla lepszej jakości resamplingu

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

- Baza biblioteki i ustawienia aplikacji (szablony katalogu/nazwy pliku, język, motyw, przełączniki TrackNoFix/libsoxr, zapisane profile synchronizacji, ostatnio używany katalog źródłowy, zapamiętany układ kolumn): `~/.local/share/SLMS/music_db/` (`library.db` i `settings.json`), z uwzględnieniem `$XDG_DATA_HOME` jeśli jest ustawione. Ta sama lokalizacja niezależnie od tego, czy uruchamiasz z kodu źródłowego, `.venv`, czy z AppImage — lokalizacja samego pliku wykonywalnego nigdy nie jest używana, bo AppImage przy każdym uruchomieniu montuje się w innym tymczasowym katalogu.
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
  templating.py              renderowanie ścieżek {artist}/{album}/...
  converter.py                konwersja audio przez ffmpeg (targety, wykrywanie ffmpeg/libsoxr)
  cover_utils.py              skalowanie/przekodowywanie okładek (używane tylko przy sync)
  sync.py                     logika synchronizacji dwukierunkowej, konflikty, konwersja/resize okładek
  settings.py                 zapisywane ustawienia aplikacji
  i18n.py                     tłumaczenia EN/PL
  gui/
    main_window.py            okno główne, widoki tabeli/drzewa, menu, sync. zaznaczenia, paski konwersji/resize/profili, usuwanie
    models.py                 model tabeli
    icons.py                  ręcznie rysowane ikony checkboxa/obecności
    tag_edit_dialog.py        edytor tagów pojedynczego utworu (Następny/Poprzedni)
    album_edit_dialog.py      edytor tagów albumu (Następny/Poprzedni album)
    settings_dialog.py        szablony, język, motyw, przełączniki libsoxr/TrackNoFix
    theme.py                  obsługa palety jasny/ciemny/auto
```
