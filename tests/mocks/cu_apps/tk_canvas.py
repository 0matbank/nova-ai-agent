"""Private custom-drawn app: buttons are only pictures on a canvas, so the accessibility
tree shows nothing — like many editors/games. Computer-use tests (plan §71)."""
import sys
import tkinter as tk

root = tk.Tk()
root.title(sys.argv[1])
root.geometry("520x320+320+220")
c = tk.Canvas(root, width=520, height=320, bg="#20232a", highlightthickness=0)
c.pack()
c.create_text(260, 40, text="Render queue", fill="white", font=("Segoe UI", 16))
for i, label in enumerate(["Render", "Pause", "Delete job"]):
    x = 60 + i * 150
    c.create_rectangle(x, 120, x + 120, 170, fill="#3b82f6" if i < 2 else "#dc2626")
    c.create_text(x + 60, 145, text=label, fill="white", font=("Segoe UI", 12))
root.mainloop()
