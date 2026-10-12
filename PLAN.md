# Plan: Put an arbitrary video onto an SD card so the Sony FDR-AX43 plays it

## Context
Goal: take any video file plus an SD card, and write files to the card so the AX43 lists the video and plays it in its own playback mode (Event View). The project directory is empty, so this starts from scratch.

## What the research found
- **The camera keeps an index of the card (its "image database").** Sony's AX43 Help Guide (cam/1720) says that files edited on a computer or renamed may not play, and their names flash. The fix Sony gives is **MENU → Setup → Media Settings → Repair Img. DB F.**, which rescans the card and rebuilds the index. Older Handycam guides say to use it when movies "edited on a computer and written back" don't appear. **This is the hook we use:** we don't write Sony's binary database ourselves. We drop correctly formatted files in the right folders and let the camera register them.
- **Risk:** the repair *deletes files the camera thinks are corrupt*. A ZV-E10 user reports their CLIP folder was emptied. So use a scratch card or a backed-up card, and always use the AC adapter.
- **The AX43 records three formats**, and each has its own folder tree. The layout below is inferred from sibling models (AX53, A7C) plus my own knowledge. It must be confirmed on your card.
  - XAVC S 4K/HD: `PRIVATE/M4ROOT/CLIP/C0001.MP4` plus a `C0001M01.XML` sidecar, `PRIVATE/M4ROOT/MEDIAPRO.XML` (index), `THMBNL/C0001T01.JPG` (thumbnails), `STATUS.BIN`. Video is H.264 4:2:0, 60/100 Mbps for 4K and 50 Mbps for HD. Audio is LPCM 2ch 48 kHz.
  - AVCHD: `PRIVATE/AVCHD/BDMV/{INDEX.BDM,MOVIEOBJ.BDM,PLAYLIST/*.MPL,CLIPINF/*.CPI,STREAM/*.MTS}`. Video is H.264 at up to 28 Mbps. Audio is Dolby Digital.
  - MP4 from "Dual Video REC" (on by default): 1280x720 H.264 with AAC audio, probably `MP_ROOT/100ANV01/MAH00001.MP4`.
  - Index database: probably `AVF_INFO/` (`AVIN0001.BNP/.INP/.INT`, `PRV00001.BIN`). Nobody has published its format, and we avoid writing it.
- **What I couldn't find online:** any worked example of an arbitrary file playing on an AX-series camera. So the final recipe has to come from tests on your camera.

## Platform: a cross-platform Python CLI, developed on WSL
- Needs Python 3 and ffmpeg/ffprobe, both already in WSL. Optionally tsMuxeR for the AVCHD route. All of these run on Windows natively too.
- It works only with files and folders, with no raw disk access. In WSL the card is at `/mnt/<letter>`. If it doesn't appear there, run `sudo mount -t drvfs E: /mnt/e`. That means no Windows-only code is needed, and Windows users can run the same script.
- Format the card **in the camera** and never on the PC. The help guide warns that cards formatted on a computer may misbehave.

## Steps

### Phase 0: get real reference files (needs you, about 10 minutes)
1. Format a scratch card in the AX43.
2. Record a 5–10 second clip in each mode: XAVC S HD, XAVC S 4K and AVCHD, with Dual Video REC on.
3. Copy the entire card into `./reference/` in the project directory.
4. I'll run `ffprobe -show_streams -show_format` on every clip and read every XML/BIN/BDM/MPL file. That gives the exact folder tree, file names, codec profile/level, GOP, bitrate, audio format, MP4 boxes (brand, Sony `uuid`/`rtmd` metadata track) and the XML fields.

### Phase 1: one experiment per route, easiest first. Each round you insert the card and report what happens.
For each route: start from the reference card, add one converted clip, run **Repair Img. DB F.**, then check whether it lists and plays.
- **A. MP4 route (simplest):** re-encode to exactly match the reference MAH MP4 (resolution, fps, profile/level, AAC, brand), then copy it in as the next `MAH0000N.MP4`. The camera handles MP4s it makes itself (Highlight Movie), so this has the best odds. The catch is that it's limited to 720p.
- **B. XAVC S HD/4K route (best quality):** re-encode to exactly match the reference (`-c:v libx264 -profile:v high -pix_fmt yuv420p`, matching level, GOP, fps and bitrate, `-c:a pcm_s16be` or whatever ffprobe shows, `-brand XAVC`). Name it `C000N.MP4`. Generate `C000NM01.XML` by filling in a template copied from the reference (duration, frame count, timecode, dates, UMID) and add an entry to `MEDIAPRO.XML`. If the camera rejects it, the next thing to try is copying the reference's `rtmd` metadata track.
- **C. AVCHD route (fallback):** build the BDMV tree with tsMuxeR's AVCHD output, or patch the reference CPI/MPL files.
- Before each run, test what happens with *no* repair step versus *with* repair, so we learn which files really matter.

### Phase 2: build the tool from whichever route works
`ax43put.py <video> <card_root> [--format mp4|xavcs-hd|xavcs-4k]`:
1. Check that `card_root` looks like an AX43 card (`PRIVATE/`, `AVF_INFO/`), and back up the index files to `./backups/<timestamp>/`.
2. Run ffprobe on the input, then ffmpeg with the matching preset into a temp file. Use letterbox/pad to reach the target resolution and convert the frame rate to the card's system (NTSC 30/60p or PAL 25/50p, taken from the reference).
3. Pick the next free number, write the media file, the sidecar XML, a thumbnail JPG (made with ffmpeg) and the index XML entries.
4. Print: "Insert card → MENU → Setup → Media Settings → Repair Img. DB F. → Memory Card."
- Files: `ax43put.py` (single file, standard library only), and `templates/` holding sidecar/index XML templates taken from the reference card. Personal data gets removed from the templates.

## Verification
- Automated: run ffprobe on the output and diff its stream parameters against the reference clip. Diff the XML fields against the reference schema.
- On the device (you): insert the card, run the repair, and confirm that (1) no clips are deleted, (2) the new clip appears in Event View with a thumbnail, (3) it plays all the way through with audio, and (4) the camera's own clips still play.
- Run the full flow from Windows Python as well, to confirm it works there too.
