# -*- coding: utf-8 -*-
"""Entrada independiente de AveDescargas con el editor visual sencillo."""

import ctypes
import tkinter as tk
from tkinterdnd2 import TkinterDnD

import media_grabber
from simple_editor_panel import SimpleEditorPanel


media_grabber.EditorPanel = SimpleEditorPanel


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    root = TkinterDnD.Tk()
    media_grabber.MediaGrabberApp(root)
    root.mainloop()

