# MusicGrabber-Fork: Qualitätsprofile pro Playlist

Dieser Fork ergänzt Watched Playlists um eine native FLAC-Qualitätsregel. Die
Regel wird beim Erzeugen eines Imports und des Acquisition Targets gespeichert,
damit laufende Jobs ihre ursprüngliche Auswahl auch nach einem Neustart
beibehalten.

## Profile

| Profil | Anforderung | Provider-Reihenfolge |
|---|---|---|
| CD (16/44,1) | exakt 16 Bit und 44.100 Hz, nativ lossless | Monochrome `LOSSLESS`, Qobuz Format 7 |
| Hi-Res (24 Bit) | mindestens 24 Bit; 44,1/48/88,2/96/176,4/192 kHz werden in der tatsächlich gelieferten Source-Rate akzeptiert | Monochrome `HI_RES_LOSSLESS`, Qobuz Format 27 |
| Beste verfügbare Qualität | höchste verfügbare native Lossless-Qualität; Hi-Res zuerst, CD automatisch danach | Qobuz Format 27, danach Format 7 |

Hi-Res besitzt zusätzlich die Option **CD fallback**. Ist sie aktiv, wird CD
erst akzeptiert, nachdem Browser- und direkter Qobuz-Hi-Res-Versuch kein
valides 24-Bit-Audio geliefert haben. Ist sie deaktiviert, bleibt der Titel bei
fehlendem Hi-Res als fehlgeschlagen/fehlend sichtbar.

Es wird nicht hoch- oder heruntergesampelt, um ein Profil künstlich zu
erfüllen. Nach jedem CDN-Download prüft MusicGrabber Codec, Bittiefe und
Sample-Rate der tatsächlich empfangenen Datei. Ein tatsächlich lossy Codec
erfüllt keines der Profile; die Analyse behauptet nicht, die gesamte
Kodierhistorie eines äußerlich validen FLAC beweisen zu können.

## Geltungsbereich

Die Qualitätsprüfung greift für automatische Watched-Playlist-Jobs, deren
Download über **Monochrome/Qobuz** läuft. Manuell ausgewählte Downloads behalten
das bisherige MusicGrabber-Verhalten. Bereits laufende Queue-Jobs behalten den
beim Erzeugen gespeicherten Snapshot; eine Änderung auf der Playlist-Karte gilt
für danach erzeugte Importe und neue Missing-Track-Retries.

Wenn das Profil garantiert über Qobuz/Tidal-CDN erfüllt werden soll, in der
Playlist unter **Sources** ausschließlich `Monochrome` auswählen. Sind weitere
Quellen erlaubt, kann MusicGrabbers normale Cross-Source-Suche weiterhin einen
anderen Kandidaten wählen; für diese Quellen gilt die Monochrome-CDN-Prüfung
nicht.

## Bestehende Installation

Beim ersten Start ergänzt die SQLite-Migration diese Felder:

- `watched_playlists.quality_profile` (Default `best`)
- `watched_playlists.quality_fallback` (Default `1`)
- dieselben beiden Felder in `bulk_imports` als Import-Snapshot

Bestehende Playlists laufen dadurch zunächst mit **Beste verfügbare Qualität**
und erlaubtem CD-Fallback weiter. Vor dem Update trotzdem die persistente
`music_grabber.db` sichern.

## TrueNAS/Portainer und Beets-Incoming

Für den dokumentierten SmartImport-Eingang den Host-Pfad
`/downloads/musik/incoming` als MusicGrabbers schreibbares Musikverzeichnis
mounten. Beispiel (den echten TrueNAS-Poolpfad einsetzen):

```yaml
services:
  music-grabber:
    image: claptraw/musicgrabber-quality-profiles:latest
    container_name: music-grabber
    restart: unless-stopped
    shm_size: "2gb"
    ports:
      - "38274:8080"
    volumes:
      - /mnt/POOL/downloads/musik/incoming:/music
      - /mnt/POOL/appdata/musicgrabber:/data
    environment:
      - MUSIC_DIR=/music
      - DB_PATH=/data/music_grabber.db
      - PUID=1000
      - PGID=1000
```

MusicGrabber darf unterhalb von `/music` eigene Unterordner anlegen; der
bestehende rekursive Beets-SmartImport verarbeitet sie nach seiner
Stabilitätswartezeit. Beets, SmartImport und die finale Musikbibliothek müssen
nicht geändert werden.

## Image bauen und testen

Im Quellordner:

```bash
docker build -t claptraw/musicgrabber-quality-profiles:latest .
docker compose up -d music-grabber
```

Relevante lokale Tests:

```bash
bash tests/run_tests.sh
tests/.venv/bin/pytest -q \
  tests/test_playlist_quality_profiles.py \
  tests/test_monochrome.py
```

Ein gelieferter Titel lässt sich zusätzlich außerhalb von MusicGrabber prüfen:

```bash
ffprobe -v error -select_streams a:0 \
  -show_entries stream=codec_name,sample_rate,bits_per_raw_sample \
  -of default=noprint_wrappers=1 "/pfad/zur/datei.flac"
```

## Technische Leitplanken

- Qobuz Format 7 wird nur als CD-FLAC angefragt.
- Qobuz Format 27 wird als höchste verfügbare Hi-Res-Stufe angefragt; die
  empfangenen Bytes entscheiden, nicht das Provider-Label.
- Tidal-hifi-api kann im vorhandenen Upstream-Pfad höchstens CD-Lossless
  liefern. Für striktes Hi-Res ist dieser letzte Leg daher gesperrt.
- Der Fork ergänzt keinen neuen DRM-Umgehungspfad, kein Upsampling und kein
  Lossy-zu-FLAC-Schönrechnen. Tidal-Hi-Res bleibt im vorhandenen letzten
  hifi-api-Leg ausdrücklich gesperrt.
