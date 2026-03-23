"""Tkinter GUI for CHM to PDF conversion."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__, convert
from .extractor import PyChmExtractor
from .pdf_renderer import PlaywrightRenderer, PrinceXmlRenderer, WeasyPrintRenderer


APP_NAME = "CHM to PDF Builder"
SETTINGS_FILE = Path.home() / ".chm_to_pdf_gui_settings.json"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{__version__}")
        self.geometry("980x720")
        self.minsize(900, 650)
        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None

        # Detect available backends
        self._pychm_available = PyChmExtractor().available()
        self._playwright_available = PlaywrightRenderer().available()
        self._weasyprint_available = WeasyPrintRenderer().available()
        self._prince_available = PrinceXmlRenderer().available()

        # Variables
        self.chm_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.hh_var = tk.StringVar()
        self.prince_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.include_toc_var = tk.BooleanVar(value=True)
        self.keep_work_var = tk.BooleanVar(value=True)
        self.renderer_var = tk.StringVar(
            value="playwright" if self._playwright_available
            else "weasyprint" if self._weasyprint_available
            else "prince"
        )

        self._build_ui()
        self._load_settings()
        self._on_renderer_change()  # Set initial field visibility
        self.after(150, self._drain_queue)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=12)
        top.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(top, text="CHM file").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self.chm_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Browse...", command=self.pick_chm).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(top, text="Output folder").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self.output_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Browse...", command=self.pick_output).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(top, text="PDF title").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self.title_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(top, text="Blank = use CHM filename").grid(row=row, column=2, sticky="w", **pad)

        row += 1
        ttk.Label(top, text="PDF renderer").grid(row=row, column=0, sticky="w", **pad)
        renderer_values = []
        if self._playwright_available:
            renderer_values.append("playwright")
        if self._weasyprint_available:
            renderer_values.append("weasyprint")
        if self._prince_available:
            renderer_values.append("prince")
        self.renderer_combo = ttk.Combobox(
            top,
            textvariable=self.renderer_var,
            values=renderer_values,
            state="readonly",
            width=20,
        )
        self.renderer_combo.grid(row=row, column=1, sticky="w", **pad)
        self.renderer_combo.bind("<<ComboboxSelected>>", lambda _: self._on_renderer_change())

        # hh.exe row (hidden if pychm is available)
        row += 1
        self._hh_row = row
        self.hh_label = ttk.Label(top, text="hh.exe path")
        self.hh_entry = ttk.Entry(top, textvariable=self.hh_var)
        self.hh_btn = ttk.Button(top, text="Browse...", command=lambda: self.pick_exe(self.hh_var))

        # prince.exe row (hidden unless prince renderer selected)
        row += 1
        self._prince_row = row
        self.prince_label = ttk.Label(top, text="PrinceXML path")
        self.prince_entry = ttk.Entry(top, textvariable=self.prince_var)
        self.prince_btn = ttk.Button(top, text="Browse...", command=lambda: self.pick_exe(self.prince_var))

        # Options
        options = ttk.Frame(self)
        options.pack(fill="x", padx=12)
        ttk.Checkbutton(options, text="Add generated contents page", variable=self.include_toc_var).pack(side="left", padx=8, pady=4)
        ttk.Checkbutton(options, text="Keep extracted working folder", variable=self.keep_work_var).pack(side="left", padx=8, pady=4)

        # Actions
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=12, pady=8)
        self.convert_btn = ttk.Button(actions, text="Convert CHM to PDF", command=self.start_conversion)
        self.convert_btn.pack(side="left")
        ttk.Button(actions, text="Open output folder", command=self.open_output_folder).pack(side="left", padx=8)

        # Determinate progress bar
        self.progress = ttk.Progressbar(actions, mode="determinate", length=220, maximum=100)
        self.progress.pack(side="right", padx=8)
        self.progress_label = ttk.Label(actions, text="")
        self.progress_label.pack(side="right")

        # Log
        ttk.Label(self, text="Log").pack(anchor="w", padx=16, pady=(8, 0))
        self.log_box = ScrolledText(self, wrap="word", height=24)
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.log_box.configure(state="disabled")

    def _on_renderer_change(self):
        """Show/hide fields based on renderer and extractor availability."""
        pad = {"padx": 8, "pady": 6}
        renderer = self.renderer_var.get()

        # hh.exe: show only if pychm is NOT available (need hh.exe as fallback)
        if self._pychm_available:
            self.hh_label.grid_remove()
            self.hh_entry.grid_remove()
            self.hh_btn.grid_remove()
        else:
            self.hh_label.grid(row=self._hh_row, column=0, sticky="w", **pad)
            self.hh_entry.grid(row=self._hh_row, column=1, sticky="ew", **pad)
            self.hh_btn.grid(row=self._hh_row, column=2, **pad)

        # prince path: show only if prince renderer selected
        if renderer == "prince":
            self.prince_label.grid(row=self._prince_row, column=0, sticky="w", **pad)
            self.prince_entry.grid(row=self._prince_row, column=1, sticky="ew", **pad)
            self.prince_btn.grid(row=self._prince_row, column=2, **pad)
        else:
            self.prince_label.grid_remove()
            self.prince_entry.grid_remove()
            self.prince_btn.grid_remove()

    def log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.update_idletasks()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        self.chm_var.set(data.get("chm", self.chm_var.get()))
        self.output_var.set(data.get("output", self.output_var.get()))
        self.hh_var.set(data.get("hh", self.hh_var.get()))
        self.prince_var.set(data.get("prince", self.prince_var.get()))
        self.title_var.set(data.get("title", self.title_var.get()))
        self.include_toc_var.set(data.get("include_toc", self.include_toc_var.get()))
        self.keep_work_var.set(data.get("keep_work", self.keep_work_var.get()))
        saved_renderer = data.get("renderer", "")
        if saved_renderer in ("weasyprint", "prince"):
            self.renderer_var.set(saved_renderer)

    def _save_settings(self):
        data = {
            "chm": self.chm_var.get(),
            "output": self.output_var.get(),
            "hh": self.hh_var.get(),
            "prince": self.prince_var.get(),
            "title": self.title_var.get(),
            "include_toc": self.include_toc_var.get(),
            "keep_work": self.keep_work_var.get(),
            "renderer": self.renderer_var.get(),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------

    def pick_chm(self):
        path = filedialog.askopenfilename(
            filetypes=[("CHM files", "*.chm"), ("All files", "*.*")]
        )
        if path:
            self.chm_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent))
            if not self.title_var.get():
                self.title_var.set(Path(path).stem)

    def pick_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def pick_exe(self, variable: tk.StringVar):
        path = filedialog.askopenfilename(
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")]
        )
        if path:
            variable.set(path)

    def open_output_folder(self):
        output = self.output_var.get().strip()
        if not output:
            messagebox.showinfo(APP_NAME, "Select an output folder first.")
            return
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(path)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open folder:\n{exc}")

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def start_conversion(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "A conversion is already running.")
            return

        chm = Path(self.chm_var.get().strip())
        output_str = self.output_var.get().strip()
        output = Path(output_str) if output_str else None

        if not chm.exists():
            messagebox.showerror(APP_NAME, "Select a valid .chm file.")
            return
        if output is None:
            messagebox.showerror(APP_NAME, "Select an output folder.")
            return
        output.mkdir(parents=True, exist_ok=True)

        self._save_settings()
        self.convert_btn.configure(state="disabled")
        self.progress["value"] = 0
        self.progress_label.configure(text="")
        self.log("=" * 72)
        self.log("Starting conversion...")

        convert_kwargs = {
            "chm_path": chm,
            "output_pdf": output / f"{chm.stem}.pdf",
            "title": self.title_var.get().strip() or None,
            "include_toc": self.include_toc_var.get(),
            "renderer": self.renderer_var.get(),
            "prince_path": self.prince_var.get().strip(),
            "hh_path": self.hh_var.get().strip(),
            "keep_work": self.keep_work_var.get(),
            "log": lambda msg: self.msg_queue.put(("log", msg)),
            "progress_callback": lambda cur, tot: self.msg_queue.put(("progress", cur, tot)),
        }

        def worker_fn():
            try:
                pdf_path = convert(**convert_kwargs)
                self.msg_queue.put(("done", str(pdf_path)))
            except Exception as exc:
                self.msg_queue.put(("error", str(exc)))

        self.worker = threading.Thread(target=worker_fn, daemon=True)
        self.worker.start()

    def _set_rendering_mode(self):
        """Switch progress bar to indeterminate mode during PDF rendering."""
        self.progress.configure(mode="indeterminate")
        self.progress.start(20)
        self.progress_label.configure(text="Rendering PDF...")

    def _set_determinate_mode(self):
        """Switch progress bar back to determinate mode."""
        self.progress.stop()
        self.progress.configure(mode="determinate")

    def _drain_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    text = msg[1]
                    self.log(text)
                    # Detect rendering phase from log messages
                    if "Rendering PDF with" in text or "Rendering chunk" in text:
                        self._set_rendering_mode()
                    elif "Merging" in text:
                        self.progress_label.configure(text="Merging PDFs...")
                elif kind == "progress":
                    current, total = msg[1], msg[2]
                    pct = int(current / total * 100) if total > 0 else 0
                    self.progress["value"] = pct
                    self.progress_label.configure(text=f"Processing topics: {current}/{total}")
                elif kind == "done":
                    self._set_determinate_mode()
                    self.progress["value"] = 100
                    self.progress_label.configure(text="Complete")
                    self.convert_btn.configure(state="normal")
                    self.log("Done.")
                    messagebox.showinfo(APP_NAME, f"PDF created:\n{msg[1]}")
                elif kind == "error":
                    self._set_determinate_mode()
                    self.progress["value"] = 0
                    self.progress_label.configure(text="Error")
                    self.convert_btn.configure(state="normal")
                    self.log(f"ERROR: {msg[1]}")
                    messagebox.showerror(APP_NAME, msg[1])
        except queue.Empty:
            pass
        self.after(150, self._drain_queue)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
