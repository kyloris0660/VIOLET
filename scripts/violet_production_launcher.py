#!/usr/bin/env python3
"""Minimal Tkinter UI for the V.I.O.L.E.T. production launcher."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import violet_production_control as control


class ProductionLauncherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("V.I.O.L.E.T. Production Launcher")
        self.geometry("820x560")
        self.minsize(760, 520)
        self.result_queue: queue.Queue[tuple[str, control.ControlResult]] = queue.Queue()
        self.status_vars = {
            "Status": tk.StringVar(value="Stopped"),
            "Environment": tk.StringVar(value="production"),
            "Port": tk.StringVar(value=""),
            "URL": tk.StringVar(value=""),
            "DB name": tk.StringVar(value=""),
            "Storage root status": tk.StringVar(value=""),
            "Last health check": tk.StringVar(value=""),
            "Last error": tk.StringVar(value=""),
        }
        self.last_result: control.ControlResult | None = None
        self._build()
        self.after(150, self.refresh_status)
        self.after(250, self._drain_queue)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, padding=(14, 12, 14, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="V.I.O.L.E.T. Production", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_vars["Status"], font=("Segoe UI", 12)).grid(row=0, column=1, sticky="e")

        fields = ttk.Frame(self, padding=(14, 4, 14, 8))
        fields.grid(row=1, column=0, sticky="ew")
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)
        labels = list(self.status_vars)
        for index, label in enumerate(labels):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(fields, text=label + ":").grid(row=row, column=col, sticky="w", padx=(0, 8), pady=3)
            ttk.Label(fields, textvariable=self.status_vars[label]).grid(row=row, column=col + 1, sticky="w", pady=3)

        log_frame = ttk.Frame(self, padding=(14, 6, 14, 8))
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(self, padding=(14, 4, 14, 14))
        buttons.grid(row=3, column=0, sticky="ew")
        for col in range(6):
            buttons.columnconfigure(col, weight=1)
        button_specs: list[tuple[str, Callable[[], None]]] = [
            ("Preflight", lambda: self.run_action("Preflight", control.preflight)),
            ("Start Production", lambda: self.run_action("Start", control.start_production)),
            ("Open Browser", lambda: self.run_action("Open Browser", control.open_browser_target)),
            ("Stop", lambda: self.run_action("Stop", control.stop_production)),
            ("Restart", lambda: self.run_action("Restart", control.restart_production)),
            ("Copy Diagnostic Summary", self.copy_diagnostics),
        ]
        for col, (text, command) in enumerate(button_specs):
            ttk.Button(buttons, text=text, command=command).grid(row=0, column=col, sticky="ew", padx=4)

    def set_busy(self, label: str) -> None:
        if label == "Start":
            self.status_vars["Status"].set("Starting")
        elif label == "Stop":
            self.status_vars["Status"].set("Stopping")
        elif label == "Restart":
            self.status_vars["Status"].set("Stopping")

    def run_action(self, label: str, func: Callable[[], control.ControlResult]) -> None:
        self.set_busy(label)

        def worker() -> None:
            result = func()
            self.result_queue.put((label, result))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self) -> None:
        try:
            while True:
                _label, result = self.result_queue.get_nowait()
                self.apply_result(result)
        except queue.Empty:
            pass
        self.after(250, self._drain_queue)

    def refresh_status(self) -> None:
        def worker() -> None:
            self.result_queue.put(("Status", control.status()))

        threading.Thread(target=worker, daemon=True).start()
        self.after(5000, self.refresh_status)

    def apply_result(self, result: control.ControlResult) -> None:
        self.last_result = result
        data = result.data
        status_text = "Running" if data.get("running") else "Stopped"
        if result.status == "blocked" or not result.ok:
            status_text = "Error"
        self.status_vars["Status"].set(status_text)
        self.status_vars["Environment"].set(str(data.get("env") or "production"))
        self.status_vars["Port"].set(str(data.get("port") or ""))
        self.status_vars["URL"].set(str(data.get("url") or ""))
        self.status_vars["DB name"].set(str(data.get("db_name") or ""))
        self.status_vars["Storage root status"].set(str(data.get("storage_root_status") or ""))
        self.status_vars["Last health check"].set(str(data.get("last_health_check") or ""))
        self.status_vars["Last error"].set(str(data.get("last_error") or ", ".join(result.errors) or ""))
        lines = data.get("recent_log_tail") or []
        if not lines and result.gates:
            lines = [f"{gate.name}: {'PASS' if gate.passed else 'FAIL'} - {gate.message}" for gate in result.gates]
        if result.message:
            lines = [result.message] + list(lines)
        self._set_log("\n".join(str(line) for line in lines))

    def _set_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)
        self.log_text.configure(state="disabled")

    def copy_diagnostics(self) -> None:
        try:
            payload = control.diagnostic_summary()
        except Exception as exc:
            messagebox.showerror("Diagnostic summary", str(exc))
            return
        text = json.dumps(payload, indent=2, sort_keys=True)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_log(text)


def main() -> int:
    app = ProductionLauncherApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
