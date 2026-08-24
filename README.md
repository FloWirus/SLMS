# SLMS — Simple Linux Music Sync

A simple Linux desktop app (PySide6) for cataloguing a local music library, editing tags/cover art, and two-way syncing selected tracks/albums/artists with a removable device (pendrive, SD card).

*(Polska wersja poniżej / Polish version below)*

## Features

- Select a music directory, scan it recursively, and build a SQLite database of all tracks (path, filename, hash).
- Incremental rescans: unchanged files (same size + modification time) are skipped, so only new/changed files are re-hashed.
- Supported audio formats: mp3, flac, ogg, wav, m4a, aac, wma.
- Table view (sortable by artist, album, disc number, title, track number, tracks-in-album, year, genre, format, size) and tree view (artist → album → track). Column widths, order, and visibility are remembered per view (table/tree, library/device) and restored on next launch.
- One search box, above both panels, filters the library and the device together as you type — and each side's table and tree at once. Typing an artist once narrows both panels, so what is missing on the device shows up next to what the library has instead of needing the same query typed twice. It matches every column the table shows (artist, album, title, genre, format, year, track/disc numbers, size); in the tree, a hit on an artist or album keeps everything beneath it visible, so searching for a band gives you its whole discography rather than only the tracks whose titles repeat the band's name. Matching branches expand themselves, and clearing the box restores both panels exactly as they were — the same row order, and the same artists/albums left collapsed.
- Checkboxes on both the table and tree view let you build a selection of tracks to sync; checking/unchecking is shared and kept in sync between the two views (checking an artist/album cascades to its tracks), with a "Check all"/"Uncheck all" toggle button per pane. When any tracks are checked, the toolbar Sync button for that pane syncs just that selection instead of everything.
- The tree view marks presence on the other side with a check icon: full green check when every track under an artist/album is present, a muted grey check when only some are.
- Edit tags (artist, album, title, track number, tracks-in-album, disc number, year, genre) and cover art per track, or for a whole album at once (artist, album, year, genre, tracks-in-album, cover — title/track number stay untouched). Available on both the library and device side (double-click a track, or use the context menu on a track/album) — device-side edits only touch the copy on the device, never the library file.
- Edit any hand-picked set of tracks at once: select several rows (Ctrl/Shift) in the table or the tree and pick "Edit tags of N selected tracks…" from the context menu. Fields the selected tracks all agree on show that one value; fields that differ show each track's own value in tree order, `;`-separated — so you can retarget a single file by editing its segment, or apply one value to every selected track by replacing the whole list with it.
- A "More tags…" toggle in both editors widens the window and adds a panel with every further tag mutagen can actually write for that file's format — composer, conductor, lyricist, comment, mood, BPM, ISRC, and more for a single track; release-level ones like album artist, label, catalog number, barcode, or compilation for a whole album (the same track-vs-album split as the basic fields). A field the format can't support is left out rather than offered and failing on save (e.g. MP3 has no plain "comment" tag; M4A/AAC supports only a handful of these to begin with) — FLAC/OGG accept the full set, since Vorbis Comment tags are freeform. Saving applies the basic fields, extended tags, cover, and filename together in one pass.
- Edit the filename directly from the tag editor.
- Manually picking a cover in the tag/album editor stores it as-is (no automatic resizing) — the library can hold covers at any resolution.
- "Download from Tidal…" in both the track and the album editor fetches album art from Tidal's public search API — no account or token of your own needed. Rather than trusting result order, candidates are re-ranked against the artist/album you actually typed, every credited artist is scored (so collaborations and duet albums, which Tidal files under a single lead artist, aren't dismissed as wrong-artist hits), and several regional catalogues are queried and pooled, since Tidal only returns albums licensed in the region asked for. What it finds is shown side by side with the cover you already have, each with its real pixel dimensions, so you can compare before replacing anything. A cover accepted from Tidal is also written out next to the album as `cover.jpg`; a cover you picked manually from disk is not, since that file already exists wherever you chose it from.
- Next/Previous navigation while editing: track editor moves across all tracks (crossing album/artist boundaries); album editor moves between whole albums.
- Detects mounted removable storage devices (via `lsblk`) and lets you pick a sync target from a dropdown. Selecting/reconnecting a device automatically rescans it against what's actually on disk, so files deleted manually (outside the app) since the last connection are correctly detected as missing instead of still showing as present. "Refresh devices" re-lists what's currently plugged in without rescanning a device that's already selected; "Scan device" forces a full rescan on demand.
- Both panels show a live count of distinct artists, albums and tracks currently listed (library or device) right under the file list. The device panel additionally shows how much free space is left on it: a compact bar plus the exact free/total figures next to it.
- Two-way sync, run manually: library → device, or device → library. Never deletes files, and asks before overwriting a file that differs from the source (unless "Force re-sync" is checked, which overwrites without asking — see below). The sync progress dialog has a Cancel button: cancelling stops before the next track is touched, so tracks already copied stay as they are and nothing already in flight is left half-written.
- Configurable directory/filename templates for the copies on the device, using placeholders: `{artist} {album} {title} {track} {track_total} {disc} {year} {genre}` (zero-padded numbers). Plain text can be mixed in, e.g. `music/{artist}`.
- Sync the whole library/device (or just the checked tracks, if any are checked) from the toolbar buttons, or a single artist, album, or track from context menus.
- The device gets its own database; tracks already present are marked with the presence icon (matched against the original library file's hash, so this still works correctly even if the copy on the device was transcoded/had its cover resized), and can be deleted from the device only (source file untouched) via the context menu.
- Delete a track, whole album, or whole artist — from the library (permanently removes the files and their DB rows) or from a device only (source files untouched) — via the context menu on either side. Always asks for confirmation first, and cleans up any directories left empty by the deletion (e.g. deleting an artist's only album also removes the now-empty artist folder).
- "MediaInfo…" context menu entry (library or device side) shows technical details read straight from the file: codec, sample rate, bit depth, channels, duration, bitrate, and file size.
- Safe "Eject" button (`udisksctl unmount` + `power-off`) — same as your desktop's native eject. Runs in the background with a progress indicator, since unmounting can take a while to flush cached writes on slow SD cards — the app stays responsive instead of appearing to freeze.
- English/Polish UI language (remembered; requires restart to switch) and Light/Dark/Auto theme (remembered; applies live, "Auto" follows the system theme).
- Optional "Convert on sync" checkbox + format dropdown at the bottom of the window (FLAC 16/44.1, FLAC 24/44.1, FLAC 24/48, FLAC 24/96, MP3 320kbps CBR). When enabled, every track copied to a device during that sync is transcoded via `ffmpeg` to the selected format — useful for players that don't need/support high-res FLAC. Only ever applies library → device (device → PC sync is always an untouched copy), never upsamples lossy sources or lossless sources already at/below the target, and its own checkbox state is not remembered between sessions. An optional libsoxr resampler can be enabled in Settings (remembered), if your ffmpeg build supports it.
- A second, independent "Resize cover art" checkbox + size (px)/DPI fields at the bottom of the window shrinks the best available cover art for each album copied to a device during that sync: the embedded art of its tracks, or a loose cover file sitting directly in the album's source folder (`cover.jpg`, `folder.png`, `front.jpg`, ...) if one exists and is higher quality — that file is only ever read, never moved or modified. The chosen artwork is scaled so its longer side matches the given size (aspect ratio kept) and stamped with the given DPI. Never upscales — a cover already at or below the target size is left untouched. Same rules as the conversion checkbox: only library → device, the library file's own tags/cover are never touched, and neither the checkbox nor the field values are remembered between sessions. Resized covers are cached next to the library (see "Data locations" below), keyed by the original artwork's content hash and the size/DPI settings — resizing the same cover art (multiple tracks in an album, repeat syncs, different devices) only does the actual image work once.
- "TrackNoFix" checkbox in Settings (remembered): zero-pads single-digit track number **tags** embedded in tracks copied to a device during sync (1 → 01, ..., 9 → 09) — the metadata field, not the filename. Only affects the copy on the device, never the library file. (Filenames built from the `{track}` template placeholder are always zero-padded regardless of this checkbox — see "Directory/filename templates" above.)
- "Force re-sync" checkbox at the bottom of the window: re-copies/re-converts/re-processes tracks even if the device already has them with a matching hash, overwriting existing files without asking. Use it after changing conversion or cover-resize settings so they retroactively apply to tracks that were already on the device. Not remembered between sessions. If the directory/filename template has changed since a track was last synced, Force also removes its old copy from the device once the new one is written, so the device converges on one copy per track instead of accumulating one per past template.
- Profile dropdown + "Add profile"/"Delete profile" buttons at the bottom of the window save/restore the directory and filename templates together with the conversion and cover-resize checkbox/field state under a name, so you can switch a whole sync configuration (e.g. "phone" vs. "car player") in one click. Selecting a profile applies it immediately; the last-used profile is remembered and re-applied on the next launch.
- Sync writes to the device are crash-safe: conversion, cover resizing, and track-number fixing are done on a temp file on local disk first (the device is only touched once, with the finished file), and the final write lands under a temp name in the same directory and is only renamed into place once complete — so a sync interrupted mid-write (crash, card pulled) never leaves a half-written file at its real path, and any such leftovers from a previous interrupted sync are cleaned up automatically on the next one.

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

`main.py` automatically re-executes itself with the `.venv` interpreter if PySide6 isn't found in the system Python, so `python3 main.py` works regardless of whether the venv is activated. Run from a terminal to see live logging of scans, syncs, and device actions as they happen.

## Prebuilt AppImage

A portable, self-contained `SLMS-x86_64.AppImage` (no `.venv`/system Python required) is published on the [Releases page](https://github.com/FloWirus/SLMS/releases) — download it, `chmod +x`, and run. Built and published manually per version, not via CI.

## Data locations

- Library database and app settings (directory/filename templates, language, theme, TrackNoFix/libsoxr toggles, saved sync profiles, last used source directory, remembered column layout): `~/.local/share/SLMS/music_db/` (`library.db` and `settings.json`), respecting `$XDG_DATA_HOME` if set. Same location regardless of whether you run from source, `.venv`, or the AppImage — the executable's own location is never used, since an AppImage runs from a fresh temporary mountpoint on every launch.
- On each synced device: `<device>/.music_db/device.db` (dot-prefixed so it stays hidden from file managers on the device; older `music_db/` folders from earlier versions are migrated automatically).
- Resized cover art cache, kept next to the music library itself (not in `~/.local/share/SLMS/`) so it travels with the library if the folder is moved: `<music library>/music_db/covers.db` indexes the cached files, which live in a `[Covers]/` folder placed directly inside whichever directory holds the source album's audio files — independent of the library's own folder layout (`artist/album`, `artist-album`, flat `album` folders, ...). Shared across every sync target — resizing a given cover once covers every device it's later synced to.

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
  cover_cache.py              persistent resized-cover cache (covers.db + [Covers] folders in the library)
  album_covers.py             loose album cover (cover.jpg/folder.png/...) discovery, read in place as a resize source
  tidal_cover.py              Tidal cover art lookup (public search API, multi-region pooling, candidate re-ranking)
  sync.py                     two-way sync logic, conflict handling, conversion/cover-resize wiring, atomic device writes
  settings.py                 persisted app settings
  i18n.py                     EN/PL translations
  gui/
    main_window.py            main window, table/tree views, live search filtering, menus, checked-selection sync, convert/resize/profile bars, deletion
    models.py                 table model, sort keys, search filter proxy
    icons.py                  hand-drawn checkbox/presence icons
    tag_edit_dialog.py        single-track tag editor (Next/Previous)
    album_edit_dialog.py      album-wide tag editor (Next/Previous album)
    extended_tags_panel.py    inline "More tags" panel of format-specific extra tags, embedded in both editors above
    tidal_cover_worker.py     background-thread wrapper around the Tidal lookup, so the dialog stays responsive
    cover_compare_dialog.py   side-by-side "current vs. found on Tidal" cover comparison
    media_info_dialog.py      read-only technical info dialog (codec, sample rate, bitrate, ...)
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
- Jedno pole wyszukiwania, nad obydwoma panelami, filtruje bibliotekę i nośnik jednocześnie w trakcie pisania — a po każdej stronie zarówno tabelę, jak i drzewo. Wpisanie artysty raz zawęża oba panele, więc od razu widać, czego brakuje na nośniku, zamiast wpisywać to samo dwa razy. Dopasowuje wszystkie kolumny widoczne w tabeli (artysta, album, tytuł, gatunek, format, rok, numer utworu/płyty, rozmiar); w drzewie trafienie w artystę lub album zostawia widoczne wszystko, co pod nim leży — więc szukanie zespołu daje całą jego dyskografię, a nie tylko utwory, w których tytule powtarza się nazwa zespołu. Pasujące gałęzie rozwijają się same, a wyczyszczenie pola przywraca oba panele dokładnie do stanu sprzed filtrowania — tę samą kolejność wierszy i tak samo pozwijanych artystów/albumy.
- Checkboxy zarówno w tabeli, jak i w drzewie pozwalają zbudować zaznaczenie utworów do synchronizacji; zaznaczanie/odznaczanie jest współdzielone i zsynchronizowane między obydwoma widokami (zaznaczenie artysty/albumu kaskadowo zaznacza jego utwory), z przyciskiem „Zaznacz/Odznacz wszystko” w każdym panelu. Gdy cokolwiek jest zaznaczone, przycisk „Synchronizuj” na pasku narzędzi dla danego panelu synchronizuje tylko to zaznaczenie zamiast wszystkiego.
- Widok drzewa oznacza obecność po drugiej stronie ikoną „✓”: pełny zielony ✓, gdy wszystkie utwory artysty/albumu są obecne, wyszarzony ✓, gdy tylko część.
- Edycja tagów (artysta, album, tytuł, numer utworu, liczba utworów w albumie, numer płyty, rok, gatunek) i okładki dla pojedynczego utworu lub dla całego albumu naraz (artysta, album, rok, gatunek, liczba utworów, okładka — tytuł i numer utworu pozostają nietknięte). Dostępne zarówno po stronie biblioteki, jak i nośnika (dwuklik na utworze albo menu kontekstowe utworu/albumu) — edycja po stronie nośnika dotyczy tylko kopii na nośniku, nigdy pliku w bibliotece.
- Edycja dowolnie wybranego zestawu utworów naraz: zaznacz kilka wierszy (Ctrl/Shift) w tabeli albo w drzewie i wybierz z menu kontekstowego „Edytuj tagi zaznaczonych utworów (N)…”. Pola, w których wszystkie zaznaczone utwory się zgadzają, pokazują jedną wartość; pola, które się różnią, pokazują wartość każdego utworu w kolejności z drzewa, rozdzieloną znakiem `;` — dzięki temu można poprawić pojedynczy plik, edytując jego fragment, albo nadać jedną wartość wszystkim zaznaczonym, zastępując nią całą listę.
- Przełącznik „Więcej tagów…” w obu edytorach poszerza okno i dodaje panel ze wszystkimi dalszymi tagami, jakie mutagen faktycznie potrafi zapisać dla danego formatu pliku — kompozytor, dyrygent, autor tekstu, komentarz, nastrój, BPM, ISRC i inne dla pojedynczego utworu; pola na poziomie wydania, jak artysta albumu, wytwórnia, numer katalogowy, kod kreskowy czy kompilacja dla całego albumu (ten sam podział utwór/album co w polach podstawowych). Pole, którego dany format nie obsługuje, jest pomijane zamiast oferowane i kończące się błędem przy zapisie (np. MP3 nie ma zwykłego tagu „comment”; M4A/AAC obsługuje tylko garstkę z tych pól) — FLAC/OGG przyjmują cały zestaw, bo tagi Vorbis Comment są dowolne. Zapis stosuje podstawowe pola, rozszerzone tagi, okładkę i nazwę pliku razem w jednym przebiegu.
- Edycja nazwy pliku bezpośrednio z edytora tagów.
- Ręczny wybór okładki w edytorze tagów/albumu zapisuje ją bez zmian (bez automatycznego skalowania) — biblioteka może trzymać okładki w dowolnej rozdzielczości.
- „Pobierz z Tidal…” w edytorze utworu i albumu pobiera okładkę z publicznego API wyszukiwania Tidala — bez własnego konta ani tokenu. Zamiast ufać kolejności wyników, kandydaci są przeliczani względem faktycznie wpisanego artysty/albumu, punktowany jest każdy artysta wymieniony przy wydawnictwie (więc współprace i albumy duetów, które Tidal podpina pod jednego artystę wiodącego, nie są odrzucane jako trafienie w złego artystę), a odpytywanych i łączonych jest kilka katalogów regionalnych, bo Tidal zwraca tylko albumy licencjonowane w regionie, o który pytano. Znaleziona okładka jest pokazywana obok tej, którą już masz, każda z realnymi wymiarami w pikselach — można je porównać przed jakąkolwiek podmianą. Okładka przyjęta z Tidala jest dodatkowo zapisywana obok albumu jako `cover.jpg`; okładka wskazana ręcznie z dysku już nie, bo ten plik i tak leży tam, skąd go wybrałeś.
- Nawigacja Następny/Poprzedni podczas edycji: edytor utworu przechodzi przez wszystkie utwory (także między albumami/artystami); edytor albumu przechodzi między całymi albumami.
- Wykrywanie podłączonych nośników wymiennych (przez `lsblk`) i wybór celu synchronizacji z listy. Wybranie/ponowne podłączenie nośnika automatycznie skanuje go pod kątem faktycznej zawartości dysku, więc pliki usunięte ręcznie (poza aplikacją) od ostatniego podłączenia są poprawnie wykrywane jako brakujące, zamiast wciąż pokazywać się jako obecne. „Odśwież nośniki” tylko odświeża listę podłączonych nośników bez ponownego skanowania już wybranego; „Skanuj nośnik” wymusza pełne ponowne skanowanie na żądanie.
- Oba panele pokazują na bieżąco liczbę artystów, albumów i utworów aktualnie widocznych na liście (w bibliotece lub na nośniku), tuż pod widokiem plików. Panel nośnika dodatkowo pokazuje ilość wolnego miejsca: skrócony pasek plus dokładne wartości wolne/całość obok niego.
- Synchronizacja dwukierunkowa uruchamiana ręcznie: biblioteka → nośnik lub nośnik → biblioteka. Nigdy nic nie usuwa i pyta przed nadpisaniem pliku różniącego się od źródła (chyba że zaznaczony jest „Wymuś resync”, który nadpisuje bez pytania — patrz niżej). Okno postępu synchronizacji ma przycisk Anuluj: przerwanie zatrzymuje kopiowanie przed kolejnym utworem, więc utwory już skopiowane zostają bez zmian, a nic będącego akurat w trakcie zapisu nie zostaje w połowie napisane.
- Konfigurowalne szablony katalogów/nazw plików dla kopii na nośniku, oparte na znacznikach: `{artist} {album} {title} {track} {track_total} {disc} {year} {genre}` (liczby dopełniane zerami). Można mieszać z dowolnym tekstem, np. `muzyka/{artist}`.
- Synchronizacja całej biblioteki/nośnika (albo tylko zaznaczonych utworów, jeśli coś jest zaznaczone) z przycisków na pasku narzędzi, albo pojedynczego artysty, albumu lub utworu z menu kontekstowego.
- Nośnik ma własną bazę danych; utwory już na nim obecne są oznaczone ikoną obecności (dopasowanie po hashu oryginalnego pliku z biblioteki, więc działa poprawnie nawet jeśli kopia na nośniku została przekonwertowana/miała zmniejszoną okładkę) i można je usunąć wyłącznie z nośnika (plik źródłowy pozostaje nietknięty) z menu kontekstowego.
- Usuwanie utworu, całego albumu lub całego artysty — z biblioteki (trwale usuwa pliki i wpisy w bazie) albo tylko z nośnika (plik źródłowy nietknięty) — z menu kontekstowego po dowolnej stronie. Zawsze z prośbą o potwierdzenie, a po usunięciu sprząta katalogi, które zostały puste (np. usunięcie jedynego albumu artysty usuwa też jego pusty już folder).
- Pozycja menu kontekstowego „MediaInfo…” (po stronie biblioteki lub nośnika) pokazuje dane techniczne odczytane wprost z pliku: kodek, częstotliwość próbkowania, głębię bitową, liczbę kanałów, czas trwania, bitrate i rozmiar pliku.
- Bezpieczny przycisk "Wysuń" (`udisksctl unmount` + `power-off`) — działa jak natywne wysuwanie w środowisku graficznym. Działa w tle z wskaźnikiem postępu, bo odmontowanie może chwilę potrwać przy zapisie bufora na wolniejsze karty SD — aplikacja zostaje responsywna zamiast sprawiać wrażenie zawieszonej.
- Język interfejsu polski/angielski (zapamiętywany, zmiana wymaga restartu) oraz motyw Jasny/Ciemny/Auto (zapamiętywany, stosowany na żywo, "Auto" podąża za motywem systemowym).
- Opcjonalny checkbox „Konwertuj przy synchronizacji” + rozwijana lista formatu na dole okna (FLAC 16/44,1, FLAC 24/44,1, FLAC 24/48, FLAC 24/96, MP3 320kbps CBR). Gdy włączony, każdy utwór kopiowany na nośnik podczas tej synchronizacji jest przekodowywany przez `ffmpeg` do wybranego formatu — przydatne dla odtwarzaczy, które nie potrzebują/nie obsługują FLAC wysokiej rozdzielczości. Dotyczy wyłącznie kierunku biblioteka → nośnik (synchronizacja nośnik → PC zawsze kopiuje bez zmian), nigdy nie podbija jakości plików stratnych ani bezstratnych już na poziomie targetu lub niższym, a stan samego checkboxa nie jest zapamiętywany między sesjami. Opcjonalny resampler libsoxr można włączyć w Ustawieniach (zapamiętywany), jeśli zainstalowany ffmpeg go obsługuje.
- Drugi, niezależny checkbox „Resize okładki” + pola rozmiaru (px)/DPI na dole okna zmniejsza najlepszą dostępną okładkę dla każdego albumu kopiowanego na urządzenie podczas tej synchronizacji: okładkę wbudowaną w tagi utworów albumu albo luźny plik okładki leżący bezpośrednio w katalogu źródłowym albumu (`cover.jpg`, `folder.png`, `front.jpg`, ...), jeśli istnieje i ma lepszą jakość — ten plik jest wyłącznie odczytywany, nigdy nie jest przenoszony ani modyfikowany. Wybrana okładka jest skalowana tak, by dłuższy bok osiągnął wpisany rozmiar (proporcje zachowane), i oznaczana wpisanym DPI. Nigdy nie powiększa — okładka już mniejsza lub równa docelowemu rozmiarowi zostaje bez zmian. Te same zasady co przy konwersji: dotyczy wyłącznie biblioteka → nośnik, własne tagi/okładka pliku w bibliotece nigdy nie są ruszane, a ani checkbox, ani wpisane wartości nie są zapamiętywane między sesjami. Przeskalowane okładki są cache'owane przy bibliotece (patrz „Lokalizacja danych” niżej), po hashu oryginalnej okładki i ustawieniach rozmiaru/DPI — przeskalowanie tej samej okładki (wiele utworów w albumie, kolejne synchronizacje, różne urządzenia) liczy się realnie tylko raz.
- Checkbox „TrackNoFix” w Ustawieniach (zapamiętywany): dopełnia zerem jednocyfrowe numery utworów w **tagu** (metadanych), nie w nazwie pliku (1 → 01, ..., 9 → 09), w utworach kopiowanych na urządzenie podczas sync. Dotyczy tylko kopii na urządzeniu, nigdy pliku w bibliotece. (Nazwy plików budowane ze znacznika `{track}` są zawsze dopełniane zerem niezależnie od tego checkboxa — patrz „Szablony katalogów/nazw plików” wyżej.)
- Checkbox „Wymuś resync” na dole okna: kopiuje/konwertuje/przetwarza utwory od nowa nawet jeśli na urządzeniu już są z pasującym hashem, nadpisując istniejące pliki bez pytania. Przydaje się po zmianie ustawień konwersji lub resize okładki, żeby zastosowały się też do utworów już obecnych na urządzeniu. Nie jest zapamiętywany między sesjami. Jeśli szablon katalogu/nazwy pliku zmienił się od ostatniej synchronizacji danego utworu, Wymuś resync usuwa też jego starą kopię z nośnika po zapisaniu nowej — dzięki temu na nośniku zostaje jedna kopia utworu, a nie po jednej na każdy dotychczasowy szablon.
- Rozwijana lista profili + przyciski „Dodaj profil”/„Usuń profil” na dole okna zapisują/przywracają szablon katalogu i nazwy pliku razem ze stanem checkboxów/pól konwersji i resize okładki pod nazwą, więc można przełączyć całą konfigurację synchronizacji (np. „telefon” vs „odtwarzacz w aucie”) jednym kliknięciem. Wybór profilu stosuje go od razu; ostatnio użyty profil jest zapamiętywany i stosowany ponownie przy następnym uruchomieniu.
- Zapis na nośnik jest odporny na przerwanie: konwersja, resize okładki i poprawa numeru utworu odbywają się najpierw na pliku tymczasowym na lokalnym dysku (nośnik jest dotykany tylko raz, gotowym plikiem), a finalny zapis ląduje pod tymczasową nazwą w tym samym katalogu i jest zmieniany na docelową dopiero po zakończeniu — więc przerwana synchronizacja (crash, wyjęcie karty) nigdy nie zostawia niekompletnego pliku pod właściwą nazwą, a ewentualne pozostałości po poprzednim przerwaniu są automatycznie sprzątane przy kolejnej synchronizacji.

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

`main.py` automatycznie przełącza się na interpreter z `.venv`, jeśli PySide6 nie jest dostępne w systemowym Pythonie — więc `python3 main.py` działa niezależnie od tego, czy venv jest aktywowany. Uruchomienie z terminala pokazuje na żywo logi skanowań, synchronizacji i działań na nośniku.

## Gotowy AppImage

Przenośny, samodzielny plik `SLMS-x86_64.AppImage` (nie wymaga `.venv` ani systemowego Pythona) jest publikowany na [stronie Releases](https://github.com/FloWirus/SLMS/releases) — pobierz, nadaj `chmod +x` i uruchom. Budowany i publikowany ręcznie przy każdej wersji, bez CI.

## Lokalizacja danych

- Baza biblioteki i ustawienia aplikacji (szablony katalogu/nazwy pliku, język, motyw, przełączniki TrackNoFix/libsoxr, zapisane profile synchronizacji, ostatnio używany katalog źródłowy, zapamiętany układ kolumn): `~/.local/share/SLMS/music_db/` (`library.db` i `settings.json`), z uwzględnieniem `$XDG_DATA_HOME` jeśli jest ustawione. Ta sama lokalizacja niezależnie od tego, czy uruchamiasz z kodu źródłowego, `.venv`, czy z AppImage — lokalizacja samego pliku wykonywalnego nigdy nie jest używana, bo AppImage przy każdym uruchomieniu montuje się w innym tymczasowym katalogu.
- Na każdym zsynchronizowanym nośniku: `<nośnik>/.music_db/device.db` (nazwa z kropką, żeby pozostawał ukryty w menedżerach plików na nośniku; starsze foldery `music_db/` z wcześniejszych wersji są migrowane automatycznie).
- Cache przeskalowanych okładek, trzymany przy samej bibliotece muzycznej (nie w `~/.local/share/SLMS/`), żeby wędrował razem z biblioteką przy przenoszeniu folderu: `<biblioteka muzyki>/music_db/covers.db` indeksuje zcache'owane pliki, które leżą w folderze `[Covers]/` umieszczonym bezpośrednio w katalogu, w którym fizycznie znajdują się pliki audio danego albumu — niezależnie od układu folderów samej biblioteki (`artysta/album`, `artysta-album`, płaskie foldery `album`, ...). Współdzielony między wszystkimi celami synchronizacji — przeskalowanie danej okładki raz obejmuje każde urządzenie, na które trafi później.

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
  cover_cache.py              trwały cache przeskalowanych okładek (covers.db + foldery [Covers] w bibliotece)
  album_covers.py             wykrywanie luźnej okładki albumu (cover.jpg/folder.png/...), odczyt w miejscu jako źródło do resize
  tidal_cover.py              wyszukiwanie okładek w Tidalu (publiczne API, łączenie regionów, przeliczanie kandydatów)
  sync.py                     logika synchronizacji dwukierunkowej, konflikty, konwersja/resize okładek, atomowy zapis na nośnik
  settings.py                 zapisywane ustawienia aplikacji
  i18n.py                     tłumaczenia EN/PL
  gui/
    main_window.py            okno główne, widoki tabeli/drzewa, filtrowanie wyszukiwarką, menu, sync. zaznaczenia, paski konwersji/resize/profili, usuwanie
    models.py                 model tabeli, klucze sortowania, proxy filtrujące wyszukiwarki
    icons.py                  ręcznie rysowane ikony checkboxa/obecności
    tag_edit_dialog.py        edytor tagów pojedynczego utworu (Następny/Poprzedni)
    album_edit_dialog.py      edytor tagów albumu (Następny/Poprzedni album)
    extended_tags_panel.py    panel „Więcej tagów” z dodatkowymi tagami zależnymi od formatu, osadzany w obu edytorach powyżej
    tidal_cover_worker.py     wątek w tle dla wyszukiwania w Tidalu, żeby okno pozostało responsywne
    cover_compare_dialog.py   porównanie okładek obok siebie: obecna vs. znaleziona w Tidalu
    media_info_dialog.py      okno tylko-do-odczytu z danymi technicznymi (kodek, próbkowanie, bitrate, ...)
    settings_dialog.py        szablony, język, motyw, przełączniki libsoxr/TrackNoFix
    theme.py                  obsługa palety jasny/ciemny/auto
```
