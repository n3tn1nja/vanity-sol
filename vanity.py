#!/usr/bin/env python3
# Vanity SOL - GUI
# original author:  n3tn1nja  (https://github.com/n3tn1nja)
# gui & features:   aq1onyt   (https://github.com/aq1onyt)
import tkinter as tk
from tkinter import filedialog
from multiprocessing import Process, Queue as MPQueue
import multiprocessing
import threading
import re
import base58
import json
import time
import os
from datetime import datetime
from collections import deque
from solders.keypair import Keypair


# palette
BG       = "#0d1117"
BG2      = "#161b22"
BG3      = "#21262d"
BORDER   = "#30363d"
ACCENT   = "#58a6ff"
GREEN    = "#3fb950"
RED      = "#f85149"
YELLOW   = "#e3b341"
TEXT     = "#c9d1d9"
MUTED    = "#6e7681"
WHITE    = "#ffffff"
SCAN_DIM = "#1e3a2f"
SCAN_PFX = "#3fb950"
SCAN_SFX = "#e3b341"

BASE58_CHARS   = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
BASE58_DISPLAY = "1-9, A-H, J-N, P-Z, a-k, m-z"


# write / read json file safely
def append_json(filepath, entry):
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
    except Exception:
        data = []
    data.append(entry)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


# worker process — generates keypairs and checks against compiled patterns
def _worker(pattern_specs, max_matches, result_q, scan_q, scan_every):
    # pattern_specs: list of (label, regex_str, flags)
    # sends ("match", label, pubkey, secret) and ("done", searched)
    compiled = [(lbl, re.compile(pat, flg)) for lbl, pat, flg in pattern_specs]
    matches  = {lbl: 0 for lbl, _, _ in pattern_specs}
    searched = 0
    try:
        while any(matches[lbl] < max_matches for lbl, _ in compiled):
            kp     = Keypair()
            pubkey = str(kp.pubkey())
            searched += 1
            if searched % scan_every == 0:
                try:
                    scan_q.put_nowait(pubkey)
                except Exception:
                    pass
            for lbl, pat in compiled:
                if matches[lbl] >= max_matches:
                    continue
                if pat.search(pubkey):
                    matches[lbl] += 1
                    secret = base58.b58encode(bytes(kp)).decode()
                    result_q.put(("match", lbl, pubkey, secret))
                    break
    finally:
        result_q.put(("done", searched))


class VanityApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vanity SOL")
        self.geometry("1300x840")
        self.minsize(1060, 700)
        self.configure(bg=BG)

        # internal pattern store: list of dicts with prefix/suffix/position/label
        self._patterns_data = []

        self._processes     = []
        self._result_q      = None
        self._scan_q        = None
        self._running       = False
        self._scan_buf      = deque(maxlen=400)
        self._scan_received = 0
        self._last_spd_recv = 0
        self._last_spd_time = time.time()
        self._pattern_match_counts = {}
        self._total_found   = 0
        self._start_time    = 0
        self._n_active      = 0
        self._error_after   = None

        self._build_ui()
        self._tick()

    # -------------------------------------------------------------------------
    # ui construction
    # -------------------------------------------------------------------------
    def _build_ui(self):
        # header
        hdr = tk.Frame(self, bg=BG, pady=10)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="VANITY SOL", font=("Consolas", 20, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")
        self._stat_var = tk.StringVar(value="idle -- add patterns and press start")
        tk.Label(hdr, textvariable=self._stat_var, font=("Consolas", 11),
                 bg=BG, fg=MUTED).pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # config row
        cfg = tk.Frame(self, bg=BG)
        cfg.pack(fill="x", padx=20, pady=12)

        # patterns panel
        pf = self._lframe(cfg, " PATTERNS ")
        pf.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self._listbox = tk.Listbox(
            pf, bg=BG3, fg=TEXT, selectbackground=ACCENT,
            selectforeground=WHITE, font=("Consolas", 9),
            bd=0, highlightthickness=0, height=4, activestyle="none"
        )
        self._listbox.pack(fill="both", expand=True)

        # error label — hidden until needed
        self._error_var = tk.StringVar(value="")
        tk.Label(pf, textvariable=self._error_var,
                 font=("Consolas", 9), bg=BG2, fg=RED, anchor="w"
                 ).pack(fill="x", pady=(3, 0))

        # entry fields row 1: starts with / ends with
        r1 = tk.Frame(pf, bg=BG2)
        r1.pack(fill="x", pady=(6, 3))
        for col, (lbl, attr) in enumerate([
            ("Starts with:", "_prefix_entry"),
            ("Ends with:",   "_suffix_entry"),
        ]):
            tk.Label(r1, text=lbl, bg=BG2, fg=MUTED,
                     font=("Consolas", 9), anchor="w"
                     ).grid(row=0, column=col * 2, sticky="w",
                            padx=(0 if col == 0 else 12, 3))
            e = tk.Entry(r1, bg=BG3, fg=TEXT, insertbackground=TEXT,
                         font=("Consolas", 9), bd=0, width=13,
                         highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT)
            e.grid(row=0, column=col * 2 + 1, sticky="ew")
            e.bind("<Return>", lambda _: self._add_pattern())
            setattr(self, attr, e)
        r1.columnconfigure(1, weight=1)
        r1.columnconfigure(3, weight=1)

        # entry fields row 2: position spinner
        r2 = tk.Frame(pf, bg=BG2)
        r2.pack(fill="x", pady=(0, 2))

        tk.Label(r2, text="At char position (0 = off):", bg=BG2, fg=MUTED,
                 font=("Consolas", 9)).pack(side="left", padx=(0, 4))
        self._pos_var = tk.IntVar(value=0)
        tk.Spinbox(r2, from_=0, to=40, textvariable=self._pos_var, width=4,
                   bg=BG3, fg=TEXT, insertbackground=TEXT,
                   buttonbackground=BG3, font=("Consolas", 9), bd=0,
                   highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACCENT
                   ).pack(side="left")

        # row 3: checkboxes on their own line so they don't get clipped
        r3 = tk.Frame(pf, bg=BG2)
        r3.pack(fill="x", pady=(0, 5))

        # anywhere checkbox — when checked, ignores prefix/suffix anchors
        self._anywhere = tk.BooleanVar(value=False)
        tk.Checkbutton(r3, text="Anywhere in address",
                       variable=self._anywhere,
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=("Consolas", 9), cursor="hand2").pack(side="left", padx=(0, 20))

        # per-pattern ignore case — each pattern can be independently case-sensitive
        self._pat_ignore_case = tk.BooleanVar(value=True)
        tk.Checkbutton(r3, text="Ignore case for this pattern",
                       variable=self._pat_ignore_case,
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=("Consolas", 9), cursor="hand2").pack(side="left")

        # add / remove buttons
        btns = tk.Frame(pf, bg=BG2)
        btns.pack(fill="x")
        self._mkbtn(btns, "Add Pattern",    self._add_pattern,    ACCENT, WHITE).pack(side="left", padx=(0, 6))
        self._mkbtn(btns, "Remove Selected", self._remove_pattern, BG3,   RED  ).pack(side="left")

        # search panel
        sf = self._lframe(cfg, " SEARCH ")
        sf.pack(side="left", fill="y", padx=(0, 10))

        tk.Label(sf, text="Case sensitivity is set\nper pattern above.",
                 bg=BG2, fg=MUTED, font=("Consolas", 9), justify="left").pack(anchor="w")

        tk.Frame(sf, bg=BORDER, height=1).pack(fill="x", pady=6)

        cpu = os.cpu_count() or 4
        for label, attr, default, lo, hi in [
            ("Processes  (-n)",  "_proc_var", cpu, 1, 64),
            ("Max matches (-m)", "_max_var",  1,   1, 9999),
        ]:
            row = tk.Frame(sf, bg=BG2)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=BG2, fg=MUTED,
                     font=("Consolas", 9), width=18, anchor="w").pack(side="left")
            var = tk.IntVar(value=default)
            setattr(self, attr, var)
            tk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=6,
                       bg=BG3, fg=TEXT, insertbackground=TEXT,
                       buttonbackground=BG3, font=("Consolas", 10), bd=0,
                       highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=ACCENT).pack(side="left")

        # output panel
        of = self._lframe(cfg, " OUTPUT ")
        of.pack(side="left", fill="y", padx=(0, 10))

        tk.Label(of, text="Save file (auto datetime on start):",
                 bg=BG2, fg=MUTED, font=("Consolas", 9)).pack(anchor="w")
        frow = tk.Frame(of, bg=BG2)
        frow.pack(fill="x", pady=(2, 8))
        self._filename_var = tk.StringVar(value="vanity_results_<datetime>.json")
        tk.Entry(frow, textvariable=self._filename_var, bg=BG3, fg=TEXT,
                 insertbackground=TEXT, font=("Consolas", 10), width=26,
                 bd=0, highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT).pack(side="left", padx=(0, 5))
        self._mkbtn(frow, "...", self._browse_file, BG3, MUTED, size=9, padx=6).pack(side="left")

        self._save_all = tk.BooleanVar(value=True)
        tk.Checkbutton(of, text="Save all found wallets", variable=self._save_all,
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=("Consolas", 9), cursor="hand2").pack(anchor="w")

        # display panel
        df = self._lframe(cfg, " DISPLAY ")
        df.pack(side="left", fill="y")

        self._live_scan    = tk.BooleanVar(value=True)
        self._show_full_pk = tk.BooleanVar(value=False)
        self._auto_stop    = tk.BooleanVar(value=True)

        for text, var in [
            ("Live scan feed",        self._live_scan),
            ("Show full private key", self._show_full_pk),
            ("Auto-stop when done",   self._auto_stop),
        ]:
            tk.Checkbutton(df, text=text, variable=var,
                           bg=BG2, fg=TEXT, selectcolor=BG3,
                           activebackground=BG2, activeforeground=TEXT,
                           font=("Consolas", 9), cursor="hand2").pack(anchor="w", pady=2)

        # control bar
        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(pady=(0, 8))

        self._start_btn = self._mkbtn(ctrl, "START", self._start, GREEN, WHITE, size=12, padx=32, pady=8)
        self._start_btn.pack(side="left", padx=10)

        self._stop_btn = self._mkbtn(ctrl, "STOP", self._stop, BG3, MUTED, size=12, padx=32, pady=8)
        self._stop_btn.configure(state="disabled")
        self._stop_btn.pack(side="left", padx=10)

        self._mkbtn(ctrl, "Clear Found", self._clear_found, BG3, MUTED, size=10, padx=14, pady=8).pack(side="left", padx=10)

        self._progress_var = tk.StringVar(value="")
        tk.Label(ctrl, textvariable=self._progress_var, font=("Consolas", 10),
                 bg=BG, fg=MUTED).pack(side="left", padx=16)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # bottom panels
        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="both", expand=True, padx=20, pady=12)
        bot.columnconfigure(0, weight=1)
        bot.columnconfigure(1, weight=1)
        bot.rowconfigure(0, weight=1)

        # live scan box
        self._scan_frame = self._lframe(bot, " LIVE SCAN ", label_fg=MUTED)
        self._scan_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._scan_text = tk.Text(
            self._scan_frame, bg=BG2, fg=SCAN_DIM, font=("Consolas", 9),
            bd=0, highlightthickness=0, state="disabled", wrap="none", cursor="arrow"
        )
        self._scan_text.pack(fill="both", expand=True)
        self._scan_text.tag_config("pfx",   foreground=SCAN_PFX)
        self._scan_text.tag_config("sfx",   foreground=SCAN_SFX)
        self._scan_text.tag_config("arrow", foreground=MUTED)

        # found wallets box
        ff = self._lframe(bot, " FOUND WALLETS ", label_fg=GREEN)
        ff.grid(row=0, column=1, sticky="nsew")
        self._found_text = tk.Text(
            ff, bg=BG2, fg=TEXT, font=("Consolas", 10),
            bd=0, highlightthickness=0, state="disabled", wrap="word", cursor="arrow"
        )
        self._found_text.pack(fill="both", expand=True)
        self._found_text.tag_config("hdr",  foreground=GREEN,  font=("Consolas", 10, "bold"))
        self._found_text.tag_config("key",  foreground=ACCENT)
        self._found_text.tag_config("priv", foreground=MUTED)
        self._found_text.tag_config("time", foreground=YELLOW)
        self._found_text.tag_config("muted",foreground=MUTED)
        self._found_text.tag_config("sep",  foreground=BG3)

        # status bar
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self._status_var = tk.StringVar(value="Ready.")
        tk.Label(self, textvariable=self._status_var, font=("Consolas", 9),
                 bg=BG, fg=MUTED, anchor="w", pady=4).pack(fill="x", padx=12)

    # -------------------------------------------------------------------------
    # helpers
    # -------------------------------------------------------------------------
    def _lframe(self, parent, title, label_fg=ACCENT):
        return tk.LabelFrame(parent, text=title, font=("Consolas", 9, "bold"),
                             bg=BG2, fg=label_fg, bd=0, relief="flat",
                             labelanchor="nw", padx=8, pady=8)

    def _mkbtn(self, parent, text, cmd, bg, fg, size=10, padx=12, pady=5):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, font=("Consolas", size, "bold"),
                         bd=0, padx=padx, pady=pady, cursor="hand2",
                         activebackground=bg, activeforeground=fg, relief="flat")

    def _set_status(self, msg):
        self._status_var.set(f"  {msg}")

    def _show_error(self, msg):
        # show inline error, auto-clear after 4s
        self._error_var.set(f"  {msg}")
        if self._error_after:
            self.after_cancel(self._error_after)
        self._error_after = self.after(4000, lambda: self._error_var.set(""))

    # -------------------------------------------------------------------------
    # base58 validation
    # -------------------------------------------------------------------------
    def _validate_text(self, text):
        # returns true if valid base58, otherwise shows error and returns false
        if not text:
            return True
        bad = sorted(set(c for c in text if c not in BASE58_CHARS))
        if bad:
            chars = ", ".join(f"'{c}'" for c in bad)
            self._show_error(
                f"Invalid character(s): {chars}. "
                f"Solana addresses only contain: {BASE58_DISPLAY}"
            )
            return False
        return True

    # -------------------------------------------------------------------------
    # pattern management
    # -------------------------------------------------------------------------
    def _add_pattern(self):
        prefix      = self._prefix_entry.get().strip()
        suffix      = self._suffix_entry.get().strip()
        position    = self._pos_var.get()
        anywhere    = self._anywhere.get()
        ignore_case = self._pat_ignore_case.get()

        if not prefix and not suffix:
            self._show_error("Enter at least one value in 'Starts with' or 'Ends with'.")
            return

        if not self._validate_text(prefix):
            return
        if not self._validate_text(suffix):
            return

        # case tag appended to label so each entry is visually distinct
        ci_tag = " [i]" if ignore_case else " [cs]"

        # build display label based on mode
        if anywhere:
            text  = prefix or suffix
            label = f"anywhere: {text}{ci_tag}"
        elif position > 0 and prefix and suffix:
            label = f"pos({position}){prefix} ... {suffix}${ci_tag}"
        elif position > 0 and prefix:
            label = f"pos({position}){prefix}{ci_tag}"
        elif position > 0 and suffix:
            label = f"{suffix} at pos({position}){ci_tag}"
        elif prefix and suffix:
            label = f"^{prefix} ... {suffix}${ci_tag}"
        elif prefix:
            label = f"^{prefix}{ci_tag}"
        else:
            label = f"{suffix}${ci_tag}"

        if label in list(self._listbox.get(0, "end")):
            self._show_error(f"Pattern '{label}' is already in the list.")
            return

        self._patterns_data.append({
            "prefix":      prefix,
            "suffix":      suffix,
            "position":    position,
            "anywhere":    anywhere,
            "ignore_case": ignore_case,
            "label":       label,
        })
        self._listbox.insert("end", label)
        self._prefix_entry.delete(0, "end")
        self._suffix_entry.delete(0, "end")
        self._error_var.set("")

    def _remove_pattern(self):
        sel = self._listbox.curselection()
        if sel:
            idx = sel[0]
            self._listbox.delete(idx)
            self._patterns_data.pop(idx)

    def _browse_file(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialfile=self._filename_var.get()
        )
        if path:
            self._filename_var.set(path)

    # -------------------------------------------------------------------------
    # build regex specs from stored pattern data
    # -------------------------------------------------------------------------
    def _build_specs(self):
        specs = []
        for p in self._patterns_data:
            prefix      = p["prefix"]
            suffix      = p["suffix"]
            position    = p["position"]
            anywhere    = p["anywhere"]
            label       = p["label"]
            # per-pattern case flag
            flags       = re.IGNORECASE if p.get("ignore_case", True) else 0

            if anywhere:
                # no anchors — match text anywhere in address
                text = prefix or suffix
                pat  = re.escape(text)

            elif position > 0:
                # pattern starts at exact character position (0-indexed)
                ep = re.escape(prefix) if prefix else ""
                es = re.escape(suffix) if suffix else ""
                if ep and es:
                    pat = f"^.{{{position}}}{ep}.*{es}$"
                elif ep:
                    pat = f"^.{{{position}}}{ep}"
                else:
                    # suffix at position = suffix ends at or after position N
                    pat = f"^.{{{position}}}{es}"

            elif prefix and suffix:
                pat = f"^{re.escape(prefix)}.*{re.escape(suffix)}$"
            elif prefix:
                pat = f"^{re.escape(prefix)}"
            else:
                pat = f"{re.escape(suffix)}$"

            specs.append((label, pat, flags))
        return specs

    # -------------------------------------------------------------------------
    # start / stop
    # -------------------------------------------------------------------------
    def _start(self):
        if not self._patterns_data:
            self._show_error("Add at least one pattern before starting.")
            return

        # auto datetime filename
        if "<datetime>" in self._filename_var.get():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._filename_var.set(f"vanity_results_{ts}.json")

        self._running       = True
        self._scan_buf.clear()
        self._scan_received = 0
        self._last_spd_recv = 0
        self._last_spd_time = time.time()
        self._total_found   = 0
        self._start_time    = time.time()
        self._pattern_match_counts = {p["label"]: 0 for p in self._patterns_data}

        self._start_btn.configure(state="disabled", bg=BG3, fg=MUTED)
        self._stop_btn.configure(state="normal", bg=RED, fg=WHITE, activebackground="#c93b30")

        specs  = self._build_specs()
        n_proc = self._proc_var.get()
        max_m  = self._max_var.get()

        self._result_q  = MPQueue()
        self._scan_q    = MPQueue(maxsize=2000)
        self._processes = []
        self._n_active  = n_proc

        for _ in range(n_proc):
            p = Process(target=_worker,
                        args=(specs, max_m, self._result_q, self._scan_q, 20),
                        daemon=True)
            p.start()
            self._processes.append(p)

        threading.Thread(target=self._read_results, daemon=True).start()
        self._set_status(
            f"Running -- {n_proc} processes -- saving to: {self._filename_var.get()}"
        )
        self._refresh_progress()

    def _stop(self):
        self._running = False
        for p in self._processes:
            p.terminate()
        self._processes.clear()
        self._filename_var.set("vanity_results_<datetime>.json")
        self._start_btn.configure(state="normal", bg=GREEN, fg=WHITE, activebackground="#4ac761")
        self._stop_btn.configure(state="disabled", bg=BG3, fg=MUTED)
        self._set_status("Stopped.")

    # -------------------------------------------------------------------------
    # result reader thread
    # -------------------------------------------------------------------------
    def _read_results(self):
        remaining = self._n_active
        while remaining > 0:
            try:
                msg = self._result_q.get(timeout=0.5)
            except Exception:
                if not self._running:
                    break
                continue
            if msg[0] == "done":
                remaining -= 1
            elif msg[0] == "match":
                _, lbl, pubkey, secret = msg
                elapsed = time.time() - self._start_time
                self.after(0, self._on_match, lbl, pubkey, secret, elapsed)

        if self._running and self._auto_stop.get():
            self.after(0, self._stop)

    # -------------------------------------------------------------------------
    # match display + save
    # -------------------------------------------------------------------------
    def _on_match(self, lbl, pubkey, secret, elapsed):
        self._total_found += 1
        self._pattern_match_counts[lbl] = self._pattern_match_counts.get(lbl, 0) + 1
        count = self._pattern_match_counts[lbl]
        max_m = self._max_var.get()
        ts    = datetime.now().strftime("%H:%M:%S")

        if self._save_all.get():
            try:
                append_json(self._filename_var.get(), {
                    "public_key":      pubkey,
                    "secret_key":      secret,
                    "matched_pattern": lbl,
                    "found_at":        datetime.now().isoformat(),
                    "time_to_find_s":  round(elapsed, 2),
                })
            except Exception as e:
                self._set_status(f"Save error: {e}")

        sk = secret if self._show_full_pk.get() else f"{secret[:20]}..."
        self._append_found(f"[{ts}]  {lbl}   match {count}/{max_m}\n", "hdr")
        self._append_found(f"  Pub  : {pubkey}\n", "key")
        self._append_found(f"  Priv : {sk}\n", "priv")
        self._append_found(f"  Time : {elapsed:.2f}s to find\n", "time")
        self._append_found("-" * 56 + "\n", "sep")
        self._refresh_progress()
        self._set_status(
            f"Match found for '{lbl}' in {elapsed:.2f}s -- saved to {self._filename_var.get()}"
        )

    def _append_found(self, text, tag=""):
        self._found_text.configure(state="normal")
        self._found_text.insert("end", text, tag)
        self._found_text.see("end")
        self._found_text.configure(state="disabled")

    def _clear_found(self):
        self._found_text.configure(state="normal")
        self._found_text.delete("1.0", "end")
        self._found_text.configure(state="disabled")
        self._total_found = 0

    def _refresh_progress(self):
        if not self._pattern_match_counts:
            self._progress_var.set("")
            return
        max_m = self._max_var.get()
        parts = [f"{lbl}: {c}/{max_m}" for lbl, c in self._pattern_match_counts.items()]
        self._progress_var.set("  |  ".join(parts))

    # -------------------------------------------------------------------------
    # 80ms ui refresh tick
    # -------------------------------------------------------------------------
    def _tick(self):
        if self._running:
            # drain scan queue into buffer
            drained = 0
            try:
                while drained < 100:
                    self._scan_buf.append(self._scan_q.get_nowait())
                    self._scan_received += 1
                    drained += 1
            except Exception:
                pass

            if self._live_scan.get() and self._scan_buf:
                # highlight prefix chars green, suffix chars yellow
                pfx_len = max((len(p["prefix"])   for p in self._patterns_data), default=0)
                sfx_len = max((len(p["suffix"])   for p in self._patterns_data), default=0)
                pos     = max((p["position"]      for p in self._patterns_data), default=0)
                # if position is set, highlight starts after those chars
                hl_start = pos if pos > 0 else 0

                self._scan_text.configure(state="normal")
                self._scan_text.delete("1.0", "end")
                for addr in list(self._scan_buf)[-90:]:
                    self._scan_text.insert("end", "- ", "arrow")
                    if hl_start:
                        self._scan_text.insert("end", addr[:hl_start])
                    if pfx_len:
                        self._scan_text.insert("end", addr[hl_start:hl_start + pfx_len], "pfx")
                    mid_end = len(addr) - sfx_len if sfx_len else len(addr)
                    self._scan_text.insert("end", addr[hl_start + pfx_len:mid_end])
                    if sfx_len:
                        self._scan_text.insert("end", addr[mid_end:], "sfx")
                    self._scan_text.insert("end", "\n")
                self._scan_text.see("end")
                self._scan_text.configure(state="disabled")

            elif not self._live_scan.get():
                self._scan_text.configure(state="normal")
                self._scan_text.delete("1.0", "end")
                self._scan_text.insert("end", "\n  Live scan feed is disabled.")
                self._scan_text.configure(state="disabled")

            # update header stats every second
            now = time.time()
            dt  = now - self._last_spd_time
            if dt >= 1.0:
                total_est = self._scan_received * 20
                speed     = (self._scan_received - self._last_spd_recv) * 20 / dt
                self._last_spd_recv = self._scan_received
                self._last_spd_time = now
                self._stat_var.set(
                    f"~{total_est:,} searched  |  {speed:,.0f} addr/s  |  "
                    f"{self._proc_var.get()} processes"
                )

        self.after(80, self._tick)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = VanityApp()
    app.mainloop()
#!/usr/bin/env python3

from multiprocessing import Process, Queue, current_process
import argparse
import json
import os
from solders.keypair import Keypair
import re
import sys
import base58
import signal

# Solana addresses use base58: no 0, O, I, l to avoid confusion
BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def validate_vanity_text(vanity_text: str, ignore_case: bool) -> None:
    """Raise SystemExit if vanity contains characters that can't appear in a Solana address."""
    if not vanity_text:
        sys.exit("Error: Vanity text cannot be empty.")
    invalid = [c for c in vanity_text if c not in BASE58_ALPHABET]
    if invalid:
        bad = ", ".join(sorted(set(invalid)))
        sys.exit(
            f"Error: Vanity text contains characters not used in Solana addresses: {bad}. \n"
            f"Solana uses base58 (no 0, O, I, l). Use only: 1-9, A-H, J-N, P-Z, a-k, m-z."
        )


def main(vanity_text, max_matches, ignore_case, match_end, num_processes):
    validate_vanity_text(vanity_text, ignore_case)

    pluralized = "es" if max_matches > 1 else ""
    filename = f"{vanity_text}-vanity-address{pluralized}.json"

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Prepend '^' if matching at the start, append '$' if matching at the end
    if match_end:
        pattern = f"{vanity_text}$"
    else:
        pattern = f"^{vanity_text}"

    flags = re.IGNORECASE if ignore_case else 0
    print(
        f"Searching for vanity: {vanity_text}, ignoring case: {'yes' if ignore_case else 'no'}, match end: {'yes' if match_end else 'no'}, processes: {num_processes}"
    )
    start_processes(pattern, flags, filename, max_matches, num_processes)


def generate_vanity_addresses(pattern_str, pattern_flags, filename, max_matches, report_interval, queue=None):
    pattern_compiled = re.compile(pattern_str, pattern_flags)
    found = 0
    searched = 0
    process_id = current_process().name

    try:
        while found < max_matches:
            keypair = Keypair()
            pubkey = str(keypair.pubkey())
            searched += 1

            if searched % report_interval == 0:
                queue.put(("progress", process_id, searched))

            if pattern_compiled.search(pubkey):
                secret_b58 = base58.b58encode(bytes(keypair)).decode("utf-8")
                found += 1
                queue.put(("match", process_id, searched, pubkey, secret_b58))
                if found >= max_matches:
                    break
    finally:
        queue.put(("done", process_id, searched))


def signal_handler(sig, frame):
    print("Exiting gracefully")
    sys.exit(0)


def start_processes(pattern_str, pattern_flags, filename, max_matches, num_processes):
    processes = []
    queue = Queue()

    # Report progress less often with many processes to avoid queue bottleneck
    report_interval = max(10, (num_processes * 5) // 2)  # e.g. 10 for 4 procs, 50 for 20, 250 for 100

    base, remainder = divmod(max_matches, num_processes)
    matches_per_process = [base + (1 if i < remainder else 0) for i in range(num_processes)]

    for i in range(num_processes):
        p = Process(
            target=generate_vanity_addresses,
            args=(pattern_str, pattern_flags, filename, matches_per_process[i], report_interval, queue),
        )
        processes.append(p)
        p.start()

    active_processes = num_processes
    per_process_searched = {}
    try:
        while active_processes > 0:
            message = queue.get()
            kind = message[0]
            process_id, searched = message[1], message[2]
            per_process_searched[process_id] = searched

            if kind == "done":
                active_processes -= 1
            elif kind == "match":
                _, _, _, pubkey, secret_b58 = message
                vanity_address = {"public_key": pubkey, "secret_key": secret_b58}
                with open(filename, "a+") as f:
                    f.seek(0)
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        data = []
                    data.append(vanity_address)
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=4)
                print(f"{process_id} found: {pubkey} after {searched} searches")
            else:
                total = sum(per_process_searched.values())
                print(f"Searched {total} addresses", end="\r")
    finally:
        for p in processes:
            p.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Vanity-Sol - Generate Vanity Solana Wallet addresses."
    )
    parser.add_argument(
        "--vanity-text",
        "--vanity",
        "-v",
        type=str,
        required=True,
        help="The text to search for in the wallet address.",
    )
    parser.add_argument(
        "--max-matches",
        "--max",
        "-m",
        type=int,
        default=1,
        help="The number of matches to find before exiting",
    )
    parser.add_argument(
        "--match-end",
        "--end",
        "-e",
        action="store_true",
        help="Match the vanity text at the end of the address instead of the beginning",
    )
    parser.add_argument(
        "--ignore-case",
        "--ignore",
        "-i",
        action="store_true",
        help="Ignore case in text matching",
    )
    parser.add_argument(
        "--num-processes",
        "-n",
        type=int,
        default=None,
        help="Number of processes (default: CPU count). Using more than CPU count usually doesn't speed things up.",
    )

    args = parser.parse_args()
    num_processes = args.num_processes if args.num_processes is not None else (os.cpu_count() or 4)

    main(
        args.vanity_text,
        args.max_matches,
        args.ignore_case,
        args.match_end,
        num_processes,
    )
