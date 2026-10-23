"""Private Tk test app (computer-use tests, plan §71). Never the owner's apps."""
import sys
import tkinter as tk

root = tk.Tk()
root.title(sys.argv[1])
root.geometry("480x320+300+200")
tk.Label(root, text="Export options").pack(pady=6)
tk.Entry(root).pack()
tk.Checkbutton(root, text="Include audio").pack()
tk.Button(root, text="Export").pack(pady=4)
tk.Button(root, text="Close").pack()
root.mainloop()
