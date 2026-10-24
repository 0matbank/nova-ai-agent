"""Private custom editor (computer-use tests, plan §72 CU-9): a text surface that offers
no accessibility value — like Scintilla editors — so it can only be typed into with the
keyboard at its cursor. Never one of the owner's apps."""
import sys
import tempfile
import tkinter as tk
from pathlib import Path

root = tk.Tk()
root.title(sys.argv[1])
root.geometry("520x300+340+240")
text = tk.Text(root, width=60, height=12)
text.insert("1.0", "first line\nsecond line")
text.pack(fill="both", expand=True)
text.focus_set()
# the test reads what is really in the editor from here (the surface itself tells nobody)
mirror = Path(tempfile.gettempdir()) / f"{sys.argv[1]}.txt"


def save(_event: object = None) -> None:
    mirror.write_text(text.get("1.0", "end-1c"), encoding="utf-8")


text.bind("<KeyRelease>", save)
save()
root.mainloop()
