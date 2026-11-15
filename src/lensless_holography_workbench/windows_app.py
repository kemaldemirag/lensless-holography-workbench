"""Tkinter shell for the U02 lensless-holography desktop workflow.

The module deliberately creates no Tk objects at import time.  This keeps the
parameter and formatting helpers usable in headless test and automation
environments while :func:`main` remains a regular Windows GUI entry point.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from .depth_scan import DepthMetric, PropagationParameters
from .desktop_workflow import (
    COMPARISON_RESULT,
    DesktopWorkflow,
    ScanRequest,
    WorkflowState,
)
from .session import U02_T2_STATUS

APP_TITLE = "Lensless Holography Workbench · U02"
STATUS_BANNER = f"U02-T2 · {U02_T2_STATUS} · {COMPARISON_RESULT}"
FOCUS_WARNING = "Uyarı: sayısal tepe fiziksel odak değildir."
MAX_SCAN_PLANES = 1000
MAX_CACHED_PLANE_BYTES = 512 * 1024 * 1024

PARAMETER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("pixel_pitch_um", "Piksel aralığı (µm)", "0.0851"),
    ("wavelength_um", "Dalga boyu (µm)", "0.66"),
    ("refractive_index", "Kırılma indisi", "1.33"),
    ("depth_start_um", "Derinlik başlangıcı (µm)", "0"),
    ("depth_stop_um", "Derinlik sonu (µm)", "20"),
    ("depth_step_um", "Derinlik adımı (µm)", "1"),
    ("roi_size_px", "ROI boyutu (px)", "256"),
)


def parse_parameters(values: Mapping[str, str]) -> PropagationParameters:
    """Parse text-entry values and return one validated parameter record."""

    required = {name for name, _label, _default in PARAMETER_FIELDS}
    missing = sorted(required.difference(values))
    if missing:
        raise ValueError(f"missing parameter fields: {', '.join(missing)}")

    parsed_floats: dict[str, float] = {}
    for name in required.difference({"roi_size_px"}):
        text = values[name].strip()
        if not text:
            raise ValueError(f"{name} must not be empty")
        try:
            parsed_floats[name] = float(text)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number") from exc

    roi_text = values["roi_size_px"].strip()
    if not roi_text:
        raise ValueError("roi_size_px must not be empty")
    try:
        roi_size = int(roi_text, 10)
    except ValueError as exc:
        raise ValueError("roi_size_px must be an integer") from exc

    parameters = PropagationParameters(
        pixel_pitch_um=parsed_floats["pixel_pitch_um"],
        wavelength_um=parsed_floats["wavelength_um"],
        refractive_index=parsed_floats["refractive_index"],
        depth_start_um=parsed_floats["depth_start_um"],
        depth_stop_um=parsed_floats["depth_stop_um"],
        depth_step_um=parsed_floats["depth_step_um"],
        roi_size_px=roi_size,
    )
    parameters.validate()
    plane_count = int(parameters.depths_um().size)
    if plane_count > MAX_SCAN_PLANES:
        raise ValueError(
            f"scan contains {plane_count} planes; maximum is {MAX_SCAN_PLANES}"
        )
    cache_bytes = (
        plane_count
        * parameters.roi_size_px**2
        * np.dtype(np.float32).itemsize
    )
    if cache_bytes > MAX_CACHED_PLANE_BYTES:
        raise ValueError(
            "requested plane cache exceeds the 512 MiB desktop limit"
        )
    return parameters


def format_depth(depth_um: float) -> str:
    """Format one depth without implying more precision than the scan grid."""

    if not np.isfinite(depth_um):
        raise ValueError("depth must be finite")
    return f"{depth_um:.9g} µm"


def format_metric(metric: DepthMetric | None) -> str:
    """Create the compact metric text shown beside a selected plane."""

    if metric is None:
        return "Metrik: doğrulanmış oturum önizlemesi (yeniden hesaplanmadı)"
    return (
        f"Derinlik: {format_depth(metric.depth_um)}\n"
        f"Gradyan enerjisi: {metric.gradient_energy:.8g}\n"
        f"Genlik standart sapması: {metric.amplitude_std:.8g}"
    )


def nearest_depth_index(depths: tuple[float, ...], slider_value: float) -> int:
    """Clamp and round a continuous Tk scale value to a depth-list index."""

    if not depths:
        raise ValueError("at least one depth is required")
    if not np.isfinite(slider_value):
        raise ValueError("slider value must be finite")
    return min(max(int(round(slider_value)), 0), len(depths) - 1)


def scan_configuration_signature(
    signal: str | Path,
    backgrounds: list[Path] | tuple[Path, ...],
    reference: str | Path | None,
    parameters: PropagationParameters,
) -> tuple[object, ...]:
    """Return a canonical UI configuration identity for stale-form checks."""

    return (
        str(Path(signal).resolve()).casefold(),
        tuple(str(Path(path).resolve()).casefold() for path in backgrounds),
        (
            str(Path(reference).resolve()).casefold()
            if reference is not None
            else None
        ),
        tuple(parameters.as_dict().items()),
    )


class WorkbenchApp:
    """Thin Tk view around :class:`DesktopWorkflow`."""

    def __init__(self, root: Any) -> None:
        # Tk imports are intentionally lazy so importing this module never
        # requires a display server or creates a default root.
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.workflow = DesktopWorkflow()
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._backgrounds: list[Path] = []
        self._depths: tuple[float, ...] = ()
        self._preview_photo: Any | None = None
        self._scan_signature: tuple[object, ...] | None = None

        root.title(APP_TITLE)
        root.geometry("1180x760")
        root.minsize(980, 680)
        root.protocol("WM_DELETE_WINDOW", self._close)

        self.signal_var = tk.StringVar()
        self.reference_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.parameter_vars = {
            name: tk.StringVar(value=default)
            for name, _label, default in PARAMETER_FIELDS
        }
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="Hazır")
        self.state_var = tk.StringVar(value="Durum: EMPTY")
        self.depth_var = tk.DoubleVar(value=0.0)
        self.depth_text_var = tk.StringVar(value="Derinlik: —")
        self.metric_var = tk.StringVar(value="Metrik: —")

        self._build_view()
        for variable in (
            self.signal_var,
            self.reference_var,
            *self.parameter_vars.values(),
        ):
            variable.trace_add("write", self._form_changed)
        self._set_running(False)
        self.root.after(60, self._poll_events)

    def _build_view(self) -> None:
        ttk = self.ttk
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        root_frame.columnconfigure(0, weight=2)
        root_frame.columnconfigure(1, weight=3)
        root_frame.rowconfigure(0, weight=1)

        controls = ttk.Frame(root_frame)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        controls.columnconfigure(0, weight=1)

        files = ttk.LabelFrame(controls, text="Girdiler", padding=10)
        files.grid(row=0, column=0, sticky="ew")
        files.columnconfigure(0, weight=1)
        self._path_row(
            files,
            0,
            "Sinyal görüntüsü",
            self.signal_var,
            self._choose_signal,
        )

        ttk.Label(files, text="Arka plan görüntüleri").grid(
            row=2, column=0, sticky="w", pady=(8, 2)
        )
        backgrounds_frame = ttk.Frame(files)
        backgrounds_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        backgrounds_frame.columnconfigure(0, weight=1)
        self.background_list = self.tk.Listbox(
            backgrounds_frame,
            height=4,
            exportselection=False,
        )
        self.background_list.grid(row=0, column=0, rowspan=2, sticky="ew")
        ttk.Button(
            backgrounds_frame,
            text="Ekle…",
            command=self._add_backgrounds,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(
            backgrounds_frame,
            text="Temizle",
            command=self._clear_backgrounds,
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))

        self._path_row(
            files,
            4,
            "Referans float TIFF (isteğe bağlı)",
            self.reference_var,
            self._choose_reference,
        )
        self._path_row(
            files,
            6,
            "Oturum çıktı klasörü",
            self.output_var,
            self._choose_output,
        )

        parameters = ttk.LabelFrame(controls, text="Tarama parametreleri", padding=10)
        parameters.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        parameters.columnconfigure(1, weight=1)
        for row, (name, label, _default) in enumerate(PARAMETER_FIELDS):
            ttk.Label(parameters, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=2
            )
            ttk.Entry(parameters, textvariable=self.parameter_vars[name]).grid(
                row=row, column=1, sticky="ew", pady=2
            )

        actions = ttk.Frame(controls)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure((0, 1, 2), weight=1)
        self.scan_button = ttk.Button(
            actions, text="Taramayı Başlat", command=self._start_scan
        )
        self.scan_button.grid(row=0, column=0, sticky="ew")
        self.cancel_button = ttk.Button(
            actions, text="İptal", command=self._cancel_scan
        )
        self.cancel_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.export_button = ttk.Button(
            actions, text="Oturumu Dışa Aktar", command=self._export_session
        )
        self.export_button.grid(row=0, column=2, sticky="ew")
        self.open_button = ttk.Button(
            actions,
            text="Doğrulanmış Oturum Aç…",
            command=self._open_session,
        )
        self.open_button.grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )

        self.progress = ttk.Progressbar(
            controls,
            variable=self.progress_var,
            maximum=100.0,
        )
        self.progress.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(controls, textvariable=self.progress_text_var).grid(
            row=4, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Label(controls, textvariable=self.state_var).grid(
            row=5, column=0, sticky="w", pady=(2, 0)
        )

        viewer = ttk.LabelFrame(root_frame, text="Düzlem önizlemesi", padding=10)
        viewer.grid(row=0, column=1, sticky="nsew")
        viewer.columnconfigure(0, weight=1)
        viewer.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            viewer,
            text="Tarama çalıştırın veya doğrulanmış bir oturum açın.",
            anchor="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        depth_frame = ttk.Frame(viewer)
        depth_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        depth_frame.columnconfigure(0, weight=1)
        self.depth_scale = ttk.Scale(
            depth_frame,
            from_=0,
            to=0,
            variable=self.depth_var,
            command=self._on_depth_scale,
            state="disabled",
        )
        self.depth_scale.grid(row=0, column=0, sticky="ew")
        ttk.Label(depth_frame, textvariable=self.depth_text_var).grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Label(
            depth_frame,
            textvariable=self.metric_var,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Separator(depth_frame).grid(row=3, column=0, sticky="ew", pady=8)
        ttk.Label(
            depth_frame,
            text=STATUS_BANNER,
            foreground="#8a4b00",
        ).grid(row=4, column=0, sticky="w")
        ttk.Label(
            depth_frame,
            text=FOCUS_WARNING,
            foreground="#9b1c1c",
        ).grid(row=5, column=0, sticky="w", pady=(2, 0))

    def _path_row(
        self,
        parent: Any,
        row: int,
        label: str,
        variable: Any,
        command: Any,
    ) -> None:
        self.ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", pady=(6 if row else 0, 2)
        )
        line = self.ttk.Frame(parent)
        line.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
        line.columnconfigure(0, weight=1)
        self.ttk.Entry(line, textvariable=variable).grid(
            row=0, column=0, sticky="ew"
        )
        self.ttk.Button(line, text="Seç…", command=command).grid(
            row=0, column=1, padx=(6, 0)
        )

    @staticmethod
    def _image_filetypes() -> tuple[tuple[str, str], ...]:
        return (
            ("Görüntüler", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
            ("Tüm dosyalar", "*.*"),
        )

    def _choose_signal(self) -> None:
        path = self.filedialog.askopenfilename(filetypes=self._image_filetypes())
        if path:
            self.signal_var.set(path)

    def _add_backgrounds(self) -> None:
        paths = self.filedialog.askopenfilenames(filetypes=self._image_filetypes())
        known = {str(path.resolve()).casefold() for path in self._backgrounds}
        for raw_path in paths:
            path = Path(raw_path).resolve()
            identity = str(path).casefold()
            if identity not in known:
                known.add(identity)
                self._backgrounds.append(path)
                self.background_list.insert("end", str(path))
        self._form_changed()

    def _clear_backgrounds(self) -> None:
        self._backgrounds.clear()
        self.background_list.delete(0, "end")
        self._form_changed()

    def _choose_reference(self) -> None:
        path = self.filedialog.askopenfilename(
            filetypes=(("Float TIFF", "*.tif *.tiff"), ("Tüm dosyalar", "*.*"))
        )
        if path:
            self.reference_var.set(path)

    def _choose_output(self) -> None:
        path = self.filedialog.askdirectory(mustexist=False)
        if path:
            self.output_var.set(path)

    def _values(self) -> dict[str, str]:
        return {name: variable.get() for name, variable in self.parameter_vars.items()}

    def _current_form_signature(self) -> tuple[object, ...]:
        signal = self.signal_var.get().strip()
        if not signal:
            raise ValueError("Sinyal görüntüsü seçilmelidir.")
        if not self._backgrounds:
            raise ValueError("En az bir arka plan görüntüsü seçilmelidir.")
        reference_text = self.reference_var.get().strip()
        parameters = parse_parameters(self._values())
        return scan_configuration_signature(
            signal,
            self._backgrounds,
            reference_text or None,
            parameters,
        )

    def _configure_workflow(self) -> tuple[object, ...]:
        signal = self.signal_var.get().strip()
        reference_text = self.reference_var.get().strip()
        parameters = parse_parameters(self._values())
        signature = self._current_form_signature()
        # Input selection validates before it mutates the workflow.  Applying
        # it first therefore keeps an existing completed scan intact when a
        # newly typed path is invalid.
        self.workflow.configure_inputs(
            signal,
            self._backgrounds,
            reference_text or None,
        )
        self.workflow.configure_parameters(parameters)
        return signature

    def _start_scan(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        try:
            signature = self._configure_workflow()
            request = self.workflow.begin_scan()
        except Exception as exc:
            self.messagebox.showerror("Tarama başlatılamadı", str(exc))
            return

        self._scan_signature = signature
        self.progress_var.set(0.0)
        self.progress_text_var.set("Tarama hazırlanıyor…")
        self.state_var.set("Durum: RUNNING")
        self._depths = ()
        self._preview_photo = None
        self.preview_label.configure(image="", text="Tarama çalışıyor…")
        self.depth_text_var.set("Derinlik: —")
        self.metric_var.set("Metrik: —")
        self._set_running(True)
        self._worker = threading.Thread(
            target=self._run_scan,
            args=(request,),
            daemon=True,
            name="u02-depth-scan",
        )
        self._worker.start()

    def _run_scan(self, request: ScanRequest) -> None:
        def progress(completed: int, total: int) -> None:
            self._events.put(("progress", (completed, total)))

        try:
            published = self.workflow.execute_scan(request, progress=progress)
        except Exception as exc:
            self._events.put(("error", exc))
        else:
            self._events.put(("done", published))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self._events.get_nowait()
                if event == "progress":
                    completed, total = payload  # type: ignore[misc]
                    percentage = 0.0 if not total else 100.0 * completed / total
                    self.progress_var.set(percentage)
                    self.progress_text_var.set(f"Düzlem: {completed}/{total}")
                elif event == "done":
                    self._scan_finished(bool(payload))
                elif event == "error":
                    self._scan_failed(payload)
        except queue.Empty:
            pass
        try:
            self.root.after(60, self._poll_events)
        except self.tk.TclError:
            pass

    def _scan_finished(self, published: bool) -> None:
        self._set_running(False)
        self.state_var.set(f"Durum: {self.workflow.state.value}")
        if not published:
            if self.workflow.state is WorkflowState.CANCELLED:
                self.progress_text_var.set("Tarama iptal edildi.")
            else:
                self.progress_text_var.set("Eski tarama sonucu yayımlanmadı.")
            return
        self.progress_var.set(100.0)
        self.progress_text_var.set("Tarama tamamlandı.")
        self._load_depths()
        if not self._form_matches_scan():
            self.progress_text_var.set(
                "Tarama tamamlandı; form değiştiği için yeniden tarayın."
            )
            self.state_var.set("Durum: FORM_CHANGED — yeniden tarayın")

    def _scan_failed(self, payload: object) -> None:
        self._set_running(False)
        self.state_var.set(f"Durum: {self.workflow.state.value}")
        self.progress_text_var.set("Tarama hatayla durdu.")
        self.messagebox.showerror("Tarama hatası", str(payload))

    def _cancel_scan(self) -> None:
        if self.workflow.cancel_scan():
            self.progress_text_var.set("İptal isteği gönderildi…")
            self.state_var.set("Durum: CANCELLED")

    def _set_running(self, running: bool) -> None:
        self.scan_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        self.open_button.configure(state="disabled" if running else "normal")
        if running:
            self.depth_scale.configure(state="disabled")
        can_export = (
            not running
            and self.workflow.state in {WorkflowState.SCANNED, WorkflowState.EXPORTED}
            and self._form_matches_scan()
        )
        self.export_button.configure(state="normal" if can_export else "disabled")

    def _form_matches_scan(self) -> bool:
        if self._scan_signature is None:
            return False
        try:
            return self._current_form_signature() == self._scan_signature
        except (OSError, ValueError):
            return False

    def _form_changed(self, *_args: object) -> None:
        if self.workflow.state is WorkflowState.RUNNING:
            return
        self._set_running(False)
        if (
            self.workflow.state in {WorkflowState.SCANNED, WorkflowState.EXPORTED}
            and not self._form_matches_scan()
        ):
            self.state_var.set("Durum: FORM_CHANGED — yeniden tarayın")
        elif self.workflow.state in {WorkflowState.SCANNED, WorkflowState.EXPORTED}:
            self.state_var.set(f"Durum: {self.workflow.state.value}")

    def _load_depths(self) -> None:
        self._depths = self.workflow.available_depths
        if not self._depths:
            return
        self.depth_scale.configure(state="normal")
        selected = self.workflow.selected_depth_um
        selected_index = 0
        if selected is not None:
            selected_index = int(np.argmin(np.abs(np.asarray(self._depths) - selected)))
        self.depth_scale.configure(to=max(len(self._depths) - 1, 0))
        self.depth_var.set(float(selected_index))
        self._show_depth(selected_index)
        self._set_running(False)

    def _on_depth_scale(self, value: str) -> None:
        if not self._depths:
            return
        try:
            index = nearest_depth_index(self._depths, float(value))
            self._show_depth(index)
        except Exception as exc:
            self.state_var.set(f"Önizleme hatası: {exc}")

    def _show_depth(self, index: int) -> None:
        from PIL import Image, ImageTk

        depth = self._depths[index]
        self.workflow.select_depth(depth)
        preview = self.workflow.plane_preview(depth)
        image = Image.fromarray(preview.image_u8, mode="L")
        image.thumbnail((640, 500), Image.Resampling.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(image=image, master=self.root)
        self.preview_label.configure(image=self._preview_photo, text="")
        self.depth_text_var.set(f"Derinlik: {format_depth(preview.depth_um)}")
        metric_text = format_metric(preview.metric)
        metric_text += (
            f"\nGörüntü aralığı: {preview.display_low:.8g} – "
            f"{preview.display_high:.8g}"
        )
        self.metric_var.set(metric_text)
        if (
            self.workflow.state in {WorkflowState.SCANNED, WorkflowState.EXPORTED}
            and not self._form_matches_scan()
        ):
            self.state_var.set("Durum: FORM_CHANGED — yeniden tarayın")
        else:
            self.state_var.set(f"Durum: {self.workflow.state.value}")

    def _export_session(self) -> None:
        output_text = self.output_var.get().strip()
        if not output_text:
            self.messagebox.showerror(
                "Dışa aktarma",
                "Oturum çıktı klasörü seçilmelidir.",
            )
            return
        if not self._form_matches_scan():
            self.messagebox.showerror(
                "Dışa aktarma",
                "Girdi veya tarama parametreleri değişti; önce yeniden tarayın.",
            )
            return
        try:
            session = self.workflow.export(output_text)
        except Exception as exc:
            self.state_var.set(f"Durum: {self.workflow.state.value}")
            self.messagebox.showerror("Dışa aktarma başarısız", str(exc))
            return
        self.state_var.set(f"Durum: {self.workflow.state.value}")
        self._set_running(False)
        self.messagebox.showinfo(
            "Oturum oluşturuldu",
            f"{session['test_id']} · {session['status']}\n{Path(output_text).resolve()}",
        )

    def _open_session(self) -> None:
        path = self.filedialog.askdirectory(mustexist=True)
        if not path:
            return
        try:
            session = self.workflow.reopen(path)
        except Exception as exc:
            self.state_var.set(f"Durum: {self.workflow.state.value}")
            self.messagebox.showerror("Oturum doğrulanamadı", str(exc))
            return
        self._scan_signature = None
        self.output_var.set(path)
        self.progress_var.set(100.0)
        self.progress_text_var.set(
            f"Doğrulanmış oturum açıldı: {session['test_id']} · {session['status']}"
        )
        self._load_depths()

    def _close(self) -> None:
        self.workflow.cancel_scan()
        self.root.destroy()


def main() -> int:
    """Launch the Windows workbench without creating GUI state on import."""

    import tkinter as tk

    root = tk.Tk()
    WorkbenchApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
