#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fred Tombs
"""AX43 Movie Adder - small GUI around ax43put.

Drag video files onto the .exe (or onto its window) and they are converted and
added to the Sony FDR-AX43 SD card that is plugged in. Nothing on the card is
ever modified or deleted; files are only added.

Dragging more files onto the .exe while it is already open hands them to the
running window instead of starting a second copy.
"""
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import ax43put as core

APP = "AX43 Movie Adder"
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".flv", ".mpg", ".mpeg",
             ".ts", ".mts", ".m2ts", ".3gp", ".ogv", ".vob"}
REMINDER_TITLE = "⚠  New movies won't appear on the camera until you do this:"
REMINDER_STEPS = ("1. Put the card in the camera.\n"
                  "2. MENU > Setup > Media Settings > Repair Img. DB F. > Yes\n"
                  "3. Playback > switch the movie format to MP4.")
STAGE_TEXT = {"queued": "Waiting", "planned": "Waiting", "encoding": "Converting",
              "building": "Building file", "copying": "Copying to card", "done": "Added",
              "failed": "Failed", "cancelled": "Cancelled"}


def resource_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def setup_tools():
    exe = ".exe" if os.name == "nt" else ""
    ff = os.path.join(resource_dir(), "ffmpeg", "ffmpeg" + exe)
    fp = os.path.join(resource_dir(), "ffmpeg", "ffprobe" + exe)
    if os.path.exists(ff) and os.path.exists(fp):
        core.FFMPEG, core.FFPROBE = ff, fp
    core.HIDE_CONSOLE = True


# --------------------------------------------------------------- single instance
PORT_FILE = os.path.join(tempfile.gettempdir(), "ax43moviesadder.port")


def hand_off_to_running(paths):
    """If another window is open, send it our files and return True."""
    try:
        with open(PORT_FILE) as f:
            port, token = f.read().split()
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2) as s:
            s.sendall((token + "\n" + "\n".join(paths) + "\n").encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            return s.recv(16) == b"ok"
    except (OSError, ValueError):
        return False


def start_listener(on_paths):
    token = os.urandom(16).hex()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    try:
        with open(PORT_FILE, "w") as f:
            f.write(f"{srv.getsockname()[1]} {token}")
    except OSError:
        return

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    data = b""
                    while chunk := conn.recv(65536):
                        data += chunk
                    lines = data.decode("utf-8", "replace").splitlines()
                    if lines and lines[0] == token:
                        conn.sendall(b"ok")
                        on_paths([l for l in lines[1:] if l.strip()])
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()


def expand(paths):
    out = []
    for p in paths:
        p = p.strip().strip('"')
        if os.path.isdir(p):
            for d, _, files in os.walk(p):
                out += [os.path.join(d, f) for f in sorted(files)
                        if os.path.splitext(f)[1].lower() in VIDEO_EXT]
        elif os.path.isfile(p):
            out.append(p)
    return out


# --------------------------------------------------------------------------- GUI
class App:
    def __init__(self, root, initial):
        self.root = root
        self.q = queue.Queue()
        self.items = []          # dicts: path, iid, state
        self.worker = None
        self.cancel = threading.Event()
        self.cards = []
        self.auto = True

        root.title(APP)
        root.minsize(620, 440)
        try:
            root.iconbitmap(os.path.join(resource_dir(), "ax43.ico"))
        except tk.TclError:
            pass

        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="SD card:").pack(side="left")
        self.card_var = tk.StringVar()
        self.card_box = ttk.Combobox(top, textvariable=self.card_var, state="readonly", width=44)
        self.card_box.pack(side="left", padx=6)
        self.card_box.bind("<<ComboboxSelected>>", lambda e: self.update_card_info())
        self.eject_btn = ttk.Button(top, text="Eject card", command=self.eject)
        self.eject_btn.pack(side="right")

        self.card_info = ttk.Label(root, text="", foreground="#555")
        self.card_info.pack(fill="x", padx=10)

        mid = ttk.Frame(root)
        mid.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(mid, columns=("status",), show="tree headings", height=8)
        self.tree.heading("#0", text="Video")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=380)
        self.tree.column("status", width=180, anchor="w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("done", foreground="#1a7f37")
        self.tree.tag_configure("failed", foreground="#c62828")

        self.hint = ttk.Label(root, text="Drag videos onto this window (or onto the .exe), "
                                         "or use Add videos.", foreground="#555")
        self.hint.pack(fill="x", padx=10)

        self.bar = ttk.Progressbar(root, mode="determinate", maximum=1.0)
        self.bar.pack(fill="x", **pad)

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        ttk.Button(btns, text="Add videos...", command=self.pick).pack(side="left")
        self.clear_btn = ttk.Button(btns, text="Clear finished", command=self.clear_finished)
        self.clear_btn.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self.do_cancel, state="disabled")
        self.cancel_btn.pack(side="right")
        self.start_btn = ttk.Button(btns, text="Start", command=self.manual_start)
        self.start_btn.pack(side="right", padx=6)

        # finish box: a status line plus a hard-to-miss reminder about the camera repair step
        self.msg = tk.Frame(root, bg="#fff4d6", highlightbackground="#e0a800", highlightthickness=2)
        self.msg_head = tk.Label(self.msg, text="", anchor="w", justify="left", bg="#fff4d6",
                                 fg="#14532d", font=("Segoe UI", 10, "bold"), padx=10)
        self.msg_head.pack(fill="x", pady=(6, 2))
        tk.Label(self.msg, text=REMINDER_TITLE, anchor="w", justify="left", bg="#fff4d6",
                 fg="#8a4b00", font=("Segoe UI", 11, "bold"), padx=10).pack(fill="x")
        tk.Label(self.msg, text=REMINDER_STEPS, anchor="w", justify="left", bg="#fff4d6",
                 fg="#3d2a00", font=("Segoe UI", 10), padx=10).pack(fill="x", pady=(2, 8))
        self.log = tk.Text(root, height=6, state="disabled", wrap="word", font=("Consolas", 9),
                           relief="flat", bg="#f6f6f6")
        self.log.pack(fill="both", padx=10, pady=(0, 10))

        self.enable_drop()
        self.refresh_cards()
        self.add(initial)
        root.after(100, self.pump)

    # ----------------------------------------------------------------- helpers
    def enable_drop(self):
        try:
            from tkinterdnd2 import DND_FILES
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", lambda e: self.add(self.root.tk.splitlist(e.data)))
        except Exception:
            self.hint.config(text="Drag videos onto the .exe, or use Add videos.")

    def write_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def set_status(self, it, stage, frac=0.0):
        it["state"] = stage
        txt = STAGE_TEXT.get(stage, stage)
        if stage in ("encoding", "copying") and frac:
            txt += f" {int(frac * 100)}%"
        if stage == "done" and it.get("name"):
            txt = f"Added as {it['name']}"
        if stage == "failed" and it.get("error"):
            txt = "Failed: " + it["error"].splitlines()[0][:80]
        tags = ("done",) if stage == "done" else ("failed",) if stage in ("failed", "cancelled") else ()
        self.tree.item(it["iid"], values=(txt,), tags=tags)

    def selected_card(self):
        v = self.card_var.get()
        return v.split("  ")[0] if v else None

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    # ------------------------------------------------------------------- cards
    def refresh_cards(self):
        if not self.busy():
            try:
                cards = core.find_cards()
            except Exception:
                cards = []
            if cards != self.cards:
                self.cards = cards
                cur = self.selected_card()
                labels = [self.card_label(c) for c in cards]
                self.card_box["values"] = labels
                if cur in cards:
                    self.card_var.set(labels[cards.index(cur)])
                elif cards:
                    self.card_var.set(labels[0])
                else:
                    self.card_var.set("")
                self.update_card_info()
                self.maybe_start()
        self.root.after(2000, self.refresh_cards)

    def card_label(self, root):
        try:
            free = core.shutil.disk_usage(root).free / 1e9
            return f"{root}  ({free:.1f} GB free)"
        except OSError:
            return root

    def update_card_info(self):
        c = self.selected_card()
        if not c:
            self.card_info.config(text="No AX43 card found - insert the camera's SD card "
                                       "(formatted in the camera).", foreground="#c62828")
        else:
            self.card_info.config(text=f"Next file: MAH{core.next_number(c):05d}.MP4 in "
                                       f"{os.path.join(c, 'MP_ROOT', '100ANV01')}", foreground="#555")
        self.eject_btn.config(state="normal" if c and not self.busy() else "disabled")

    def eject(self):
        c = self.selected_card()
        if not c or self.busy() or os.name != "nt":
            return
        letter = c[:2]
        ps = ("$v=(New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s');"
              "if($v){$v.InvokeVerb('Eject')}; Start-Sleep 2; if(Test-Path '%s\\'){'busy'}else{'ok'}"
              % (letter, letter))
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                               text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            ok = "ok" in r.stdout
        except Exception:
            ok = False
        if ok:
            self.show_msg(f"Card {letter} ejected - safe to remove.")
            messagebox.showwarning(APP, "Card ejected - safe to remove.\n\n" + REMINDER_TITLE + "\n\n"
                                   + REMINDER_STEPS)
            self.cards = None  # force refresh
        else:
            messagebox.showwarning(APP, f"Windows could not eject {letter} right now. "
                                        "Close any windows using the card and try again.")

    # ------------------------------------------------------------------- queue
    def add(self, paths):
        files = expand(paths)
        skipped = [p for p in paths if not expand([p])]
        for p in files:
            iid = self.tree.insert("", "end", text=os.path.basename(p), values=("Waiting",))
            self.items.append({"path": p, "iid": iid, "state": "queued"})
        for p in skipped:
            self.write_log(f"Ignored (not a file): {p}")
        if files:
            self.msg.pack_forget()
            self.auto = True
            self.maybe_start()

    def pick(self):
        exts = " ".join("*" + e for e in sorted(VIDEO_EXT))
        paths = filedialog.askopenfilenames(title="Choose videos",
                                            filetypes=[("Videos", exts), ("All files", "*.*")])
        if paths:
            self.add(list(paths))

    def clear_finished(self):
        for it in [i for i in self.items if i["state"] in ("done", "failed", "cancelled")]:
            self.tree.delete(it["iid"])
            self.items.remove(it)

    def pending(self):
        return [i for i in self.items if i["state"] == "queued"]

    def manual_start(self):
        for it in self.items:   # Start also retries cancelled/failed videos
            if it["state"] in ("cancelled", "failed"):
                it["error"] = None
                self.set_status(it, "queued")
        self.auto = True
        self.start()

    def maybe_start(self):
        if self.auto and not self.busy() and self.pending() and self.selected_card():
            self.start()

    def start(self):
        card = self.selected_card()
        if self.busy():
            return
        if not card:
            messagebox.showinfo(APP, "Insert the camera's SD card first.")
            return
        batch = self.pending()
        if not batch:
            return
        self.cancel.clear()
        self.msg.pack_forget()
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.eject_btn.config(state="disabled")
        self.bar["value"] = 0
        self.write_log(f"Adding {len(batch)} video(s) to {card} ...")

        def work():
            def status(i, stage, frac):
                self.q.put(("status", batch[i], stage, frac))
            try:
                res = core.add_videos(card, [it["path"] for it in batch],
                                      log=lambda m: self.q.put(("log", m)),
                                      status=status, cancel=self.cancel, keep_going=True)
                for it, (_, name, err) in zip(batch, res):
                    self.q.put(("result", it, name, err))
                self.q.put(("finished", any(r[1] for r in res)))
            except core.Cancelled:
                for it in batch:
                    if it["state"] not in ("done", "failed"):
                        self.q.put(("status", it, "cancelled", 0))
                self.q.put(("finished", any(it["state"] == "done" for it in batch)))
            except Exception as e:  # card unplugged, out of space, ...
                for it in batch:
                    if it["state"] not in ("done",):
                        it["error"] = str(e)
                        self.q.put(("status", it, "failed", 0))
                self.q.put(("log", f"ERROR: {e}"))
                self.q.put(("finished", any(it["state"] == "done" for it in batch)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def do_cancel(self):
        self.cancel.set()
        self.auto = False
        self.write_log("Cancelling ...")

    def show_msg(self, text):
        self.msg_head.config(text=text)
        self.msg.pack(fill="x", padx=10, pady=4, before=self.log)

    # ---------------------------------------------------------- event pump
    def pump(self):
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.write_log(ev[1])
                elif kind == "status":
                    _, it, stage, frac = ev
                    self.set_status(it, stage, frac)
                    if stage == "encoding":
                        self.bar["value"] = 0.85 * frac
                    elif stage == "building":
                        self.bar["value"] = 0.85
                    elif stage == "copying":
                        self.bar["value"] = 0.85 + 0.15 * frac
                    elif stage == "done":
                        self.bar["value"] = 1.0
                elif kind == "result":
                    _, it, name, err = ev
                    it["name"], it["error"] = name, err
                    if name:
                        self.set_status(it, "done")
                    elif err:
                        self.set_status(it, "failed")
                elif kind == "add":
                    self.add(ev[1])
                elif kind == "finished":
                    self.worker = None
                    self.start_btn.config(state="normal")
                    self.cancel_btn.config(state="disabled")
                    self.update_card_info()
                    if ev[1]:
                        n = sum(1 for i in self.items if i["state"] == "done")
                        self.show_msg(f"Done - {n} video(s) on the card. Click 'Eject card' when finished.")
                    self.maybe_start()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)


def main():
    args = sys.argv[1:]
    if args and hand_off_to_running(args):
        return
    setup_tools()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    app = App(root, args)
    start_listener(lambda paths: app.q.put(("add", paths)))
    root.mainloop()


if __name__ == "__main__":
    main()
