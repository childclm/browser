import tkinter

def on_key(e):
    print(f"char='{e.char}', keysym='{e.keysym}', keycode={e.keycode}")

root = tkinter.Tk()
root.geometry("300x200")
root.title("按键测试 - 点击后按键")
label = tkinter.Label(root, text="点击这里，然后按键", font=("Arial", 20))
label.pack(expand=True)
root.bind("<Key>", on_key)
root.focus_force()  # 强制窗口获得焦点
root.mainloop()