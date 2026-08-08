# desktop_app.py
import atexit
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from urllib.parse import urlparse

import customtkinter as ctk
import requests

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Configuration ---
# Backend server address, resolved in this order:
#   1. TODO_API_URL environment variable
#   2. "api_url" in config.json next to this script
#   3. http://127.0.0.1:5000 (same machine, zero setup)
def load_api_base_url():
    url = os.environ.get("TODO_API_URL")
    if not url:
        try:
            with open(os.path.join(APP_DIR, "config.json"), "r", encoding="utf-8") as f:
                url = json.load(f).get("api_url")
        except (OSError, ValueError):
            url = None
    return (url or "http://127.0.0.1:5000").rstrip("/")

API_BASE_URL = load_api_base_url()

# Seconds to wait for the backend before giving up on a request
REQUEST_TIMEOUT = 5

# Milliseconds between automatic background refreshes of the todo list
SYNC_INTERVAL_MS = 60_000

# --- Local backend auto-start ---
# When the API points at this machine, the app launches server.py itself,
# so double-clicking start.bat (or running this file) is all that's needed.
_server_process = None

def _stop_local_server():
    if _server_process is not None and _server_process.poll() is None:
        _server_process.terminate()

def _backend_reachable():
    try:
        requests.get(f"{API_BASE_URL}/todos", timeout=1)
        return True
    except requests.exceptions.RequestException:
        return False

def ensure_local_server():
    """Start server.py in the background if the local backend isn't up yet."""
    global _server_process
    if _backend_reachable():
        return True

    parsed = urlparse(API_BASE_URL)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        return False  # Remote backend: nothing we can start from here

    server_path = os.path.join(APP_DIR, "server.py")
    if not os.path.exists(server_path):
        return False

    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    env = {**os.environ, "TODO_PORT": str(parsed.port or 5000)}
    _server_process = subprocess.Popen(
        [sys.executable, server_path], creationflags=creationflags, env=env
    )
    atexit.register(_stop_local_server)

    # Give the server a few seconds to boot before the UI starts polling
    for _ in range(20):
        time.sleep(0.25)
        if _backend_reachable():
            return True
    return False


ctk.set_appearance_mode("light")


class TodoApp:
    COLLAPSED_SIZE = (320, 56)
    EXPANDED_SIZE = (320, 400)

    def __init__(self, master):
        self.master = master
        master.title("Minimal Todo")
        master.overrideredirect(True)  # Borderless floating window
        master.attributes("-topmost", True)

        # Windows-only transparent key color, so the rounded card corners cut
        # through to the desktop. A light key color keeps the antialiased
        # corner fringe invisible; on Mac/Linux fall back to a white window.
        self.transparent_color = "#F1F2F4"
        try:
            master.attributes("-transparentcolor", self.transparent_color)
            master.configure(fg_color=self.transparent_color)
        except tk.TclError:
            self.transparent_color = None
            master.configure(fg_color="#FFFFFF")
        try:
            master.attributes("-alpha", 0.98)
        except tk.TclError:
            pass

        w, h = self.COLLAPSED_SIZE
        master.geometry(f"{w}x{h}+100+100")
        self.is_expanded = False

        # --- Palette: silver + white minimal ---
        self.col_card = "#FFFFFF"          # Cards and the collapsed bar
        self.col_panel = "#FAFBFC"         # Expanded panel background
        self.col_border = "#E6E8EC"        # Hairline borders
        self.col_text = "#33353B"          # Primary text (soft charcoal)
        self.col_text_dim = "#ABAEB6"      # Secondary / completed text
        self.col_silver = "#E9EAEE"        # Silver buttons
        self.col_silver_press = "#DCDEE4"  # Silver pressed / hover
        self.col_dot = "#9AA0AB"           # Active-task dot
        self.col_highlight = "#F0F1F4"     # Drop-target row tint
        self.col_drag_border = "#9AA0AB"   # Border of drag source / target
        self.col_del_hover = "#F5E4E4"     # Delete button hover wash

        # --- Fonts: prefer elegant CJK-capable families ---
        families = set(tkfont.families())
        base_family = next(
            (f for f in ("等线", "DengXian", "PingFang SC", "Microsoft YaHei UI",
                         "Microsoft YaHei", "Yu Gothic UI", "Segoe UI", "Helvetica Neue")
             if f in families),
            "TkDefaultFont",
        )
        self.font_main = ctk.CTkFont(family=base_family, size=16, weight="bold")
        self.font_todo = ctk.CTkFont(family=base_family, size=14)
        self.font_todo_done = ctk.CTkFont(family=base_family, size=14, overstrike=True)
        self.font_small = ctk.CTkFont(family=base_family, size=12)

        # --- Collapsed bar: a white rounded pill ---
        self.bar = ctk.CTkFrame(master, corner_radius=26, fg_color=self.col_card,
                                border_width=1, border_color=self.col_border)
        self.bar.pack(fill="both", expand=True)
        self.bar_inner = ctk.CTkFrame(self.bar, fg_color="transparent")
        self.bar_inner.place(relx=0.5, rely=0.5, anchor="center")
        self.dot_label = ctk.CTkLabel(self.bar_inner, text="●", text_color=self.col_dot,
                                      font=self.font_small)
        self.dot_label.pack(side="left", padx=(0, 7))
        self.task_label = ctk.CTkLabel(self.bar_inner, text="Loading…",
                                       text_color=self.col_text, font=self.font_main)
        self.task_label.pack(side="left")

        # --- Expanded panel ---
        self.panel = ctk.CTkFrame(master, corner_radius=18, fg_color=self.col_panel,
                                  border_width=1, border_color=self.col_border)
        # (packed on expand)

        self.input_row = ctk.CTkFrame(self.panel, fg_color="transparent")
        self.input_row.pack(fill="x", padx=12, pady=(12, 6))
        self.todo_input = ctk.CTkEntry(self.input_row, placeholder_text="Add a task…",
                                       height=36, corner_radius=18, fg_color=self.col_card,
                                       border_color=self.col_border, border_width=1,
                                       text_color=self.col_text, font=self.font_todo)
        self.todo_input.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.todo_input.bind("<Return>", self.add_todo_event)
        self.add_button = ctk.CTkButton(self.input_row, text="＋", width=36, height=36,
                                        corner_radius=18, fg_color=self.col_silver,
                                        hover_color=self.col_silver_press,
                                        text_color=self.col_text, font=self.font_main,
                                        command=self.add_todo)
        self.add_button.pack(side="right")

        self.list_frame = ctk.CTkScrollableFrame(self.panel, fg_color="transparent",
                                                 scrollbar_button_color=self.col_silver,
                                                 scrollbar_button_hover_color=self.col_silver_press)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        # --- Window drag / click-to-toggle ---
        self.master.bind("<ButtonPress-1>", self.on_window_button_press)
        self.master.bind("<B1-Motion>", self.on_window_mouse_drag)
        self.master.bind("<ButtonRelease-1>", self.on_window_button_release)

        # Right-click menu — the borderless window has no close button
        self.app_menu = tk.Menu(master, tearoff=0)
        self.app_menu.add_command(label="Refresh now", command=self.load_todos)
        self.app_menu.add_separator()
        self.app_menu.add_command(label="Quit Minimal Todo", command=self.quit_app)
        self.master.bind_all("<Button-3>", self.show_app_menu)
        self.master.bind_all("<Button-2>", self.show_app_menu)

        # --- State ---
        self.todo_frames = []
        self.current_todos_data = []
        self.dragged_item_data = None
        self.dragged_item_frame = None
        self.drag_toplevel = None
        self._drag_start_x = None
        self._drag_start_y = None
        self._offset_x = None
        self._offset_y = None
        self._is_dragging_window = False
        self._is_dragging_todo_item = False
        self._click_threshold = 5

        self.load_todos()
        self.schedule_sync()

    # --- Widget tree helpers ---
    def _todo_frame_of(self, widget):
        """Walk up the widget tree to the row frame carrying todo_data."""
        w = widget
        while w is not None and w is not self.master:
            if hasattr(w, "todo_data"):
                return w
            w = getattr(w, "master", None)
        return None

    def _in_interactive_widget(self, widget):
        """True if the widget or an ancestor is a control that handles clicks."""
        w = widget
        while w is not None and w is not self.master:
            if isinstance(w, (ctk.CTkEntry, ctk.CTkButton, ctk.CTkCheckBox,
                              ctk.CTkScrollbar, tk.Entry, tk.Scrollbar)):
                return True
            w = getattr(w, "master", None)
        return False

    # --- Window Drag and Click-to-Toggle ---
    def on_window_button_press(self, event):
        self._offset_x = event.x_root - self.master.winfo_x()
        self._offset_y = event.y_root - self.master.winfo_y()
        self._is_dragging_window = False

    def on_window_mouse_drag(self, event):
        if self._offset_x is None or self._offset_y is None:
            return
        new_x = event.x_root - self._offset_x
        new_y = event.y_root - self._offset_y
        moved = ((new_x - self.master.winfo_x()) ** 2 +
                 (new_y - self.master.winfo_y()) ** 2) ** 0.5
        if self._is_dragging_window or moved > self._click_threshold:
            self._is_dragging_window = True
            self.master.geometry(f"+{new_x}+{new_y}")

    def on_window_button_release(self, event):
        if not self._is_dragging_window and not self._is_dragging_todo_item:
            if not self._in_interactive_widget(event.widget) and \
               self._todo_frame_of(event.widget) is None:
                self.toggle_expand()
        self._offset_x = None
        self._offset_y = None
        self._is_dragging_window = False

    # --- Expand/Collapse ---
    def toggle_expand(self, event=None):
        x, y = self.master.winfo_x(), self.master.winfo_y()
        if self.is_expanded:
            w, h = self.COLLAPSED_SIZE
            self.panel.pack_forget()
            self.bar.pack(fill="both", expand=True)
        else:
            w, h = self.EXPANDED_SIZE
            self.bar.pack_forget()
            self.panel.pack(fill="both", expand=True)
            self.load_todos()
        self.master.geometry(f"{w}x{h}+{x}+{y}")
        self.is_expanded = not self.is_expanded

    # --- Load Todos ---
    def load_todos(self):
        try:
            response = requests.get(f"{API_BASE_URL}/todos", timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            todos = response.json()
            self.current_todos_data = todos
            self.update_todo_display(todos)
        except requests.exceptions.ConnectionError:
            self.task_label.configure(text="Cannot connect to server", text_color="#D66")
            self.dot_label.pack_forget()
        except Exception:
            self.task_label.configure(text="Load failed", text_color="#D66")
            self.dot_label.pack_forget()

    def _ellipsize(self, text, font_obj, max_width):
        """Trim text to max_width pixels, appending an ellipsis if trimmed."""
        if font_obj.measure(text) <= max_width:
            return text
        while text and font_obj.measure(text + "…") > max_width:
            text = text[:-1]
        return text + "…"

    # --- Render ---
    def update_todo_display(self, todos):
        for frame in self.todo_frames:
            frame.destroy()
        self.todo_frames.clear()

        incomplete = [t for t in todos if not t["is_completed"]]
        if incomplete:
            text = self._ellipsize(incomplete[0]["content"], self.font_main, 235)
            self.task_label.configure(text=text, text_color=self.col_text)
            self.dot_label.pack(side="left", padx=(0, 7), before=self.task_label)
        else:
            self.task_label.configure(text="No todos yet", text_color=self.col_text_dim)
            self.dot_label.pack_forget()

        for todo in todos:
            row = ctk.CTkFrame(self.list_frame, corner_radius=14, fg_color=self.col_card,
                               border_width=1, border_color=self.col_border)
            row.pack(fill="x", pady=4, padx=2)
            row.todo_data = todo

            checkbox = ctk.CTkCheckBox(
                row, text="", width=24, checkbox_width=20, checkbox_height=20,
                corner_radius=10, border_width=2, border_color="#C9CCD4",
                fg_color=self.col_dot, hover_color="#8A8F99", checkmark_color="white",
            )
            checkbox.configure(command=lambda t=todo, c=checkbox: self.toggle_complete(t, c))
            if todo["is_completed"]:
                checkbox.select()
            checkbox.pack(side="left", padx=(10, 4), pady=8)

            label = ctk.CTkLabel(
                row, text=todo["content"],
                text_color=self.col_text_dim if todo["is_completed"] else self.col_text,
                font=self.font_todo_done if todo["is_completed"] else self.font_todo,
                anchor="w", justify="left", wraplength=185,
            )
            label.pack(side="left", fill="x", expand=True, pady=8)

            delete_button = ctk.CTkButton(
                row, text="✕", width=26, height=26, corner_radius=13,
                fg_color="transparent", hover_color=self.col_del_hover,
                text_color=self.col_text_dim, font=self.font_small,
                command=lambda t=todo: self.delete_todo(t),
            )
            delete_button.pack(side="right", padx=(4, 8))

            # Drag bindings on the row and its label (text is most of the row)
            for widget in (row, label):
                widget.bind("<ButtonPress-1>", self.on_todo_item_press)
                widget.bind("<B1-Motion>", self.on_todo_item_drag)
                widget.bind("<ButtonRelease-1>", self.on_todo_item_release)

            self.todo_frames.append(row)

    # --- Add ---
    def add_todo_event(self, event=None):
        self.add_todo()

    def add_todo(self):
        content = self.todo_input.get().strip()
        if not content:
            return
        try:
            response = requests.post(f"{API_BASE_URL}/todos", json={"content": content},
                                     timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.todo_input.delete(0, "end")
            self.load_todos()
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error", "Cannot connect to server.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add todo: {e}")

    # --- Toggle Complete ---
    def toggle_complete(self, todo, checkbox):
        is_completed = bool(checkbox.get())
        try:
            response = requests.put(f"{API_BASE_URL}/todos/{todo['id']}",
                                    json={"is_completed": is_completed},
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.load_todos()
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error", "Cannot connect to server.")
            self.load_todos()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update todo: {e}")
            self.load_todos()

    # --- Delete ---
    def delete_todo(self, todo):
        if messagebox.askyesno("Delete", f"Delete todo: '{todo['content']}'?"):
            try:
                response = requests.delete(f"{API_BASE_URL}/todos/{todo['id']}",
                                           timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                self.load_todos()
            except requests.exceptions.ConnectionError:
                messagebox.showerror("Error", "Cannot connect to server.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete todo: {e}")

    # --- Drag & Drop Reordering ---
    def on_todo_item_press(self, event):
        row = self._todo_frame_of(event.widget)
        if row is None:
            return
        self.dragged_item_data = row.todo_data
        self.dragged_item_frame = row
        self._is_dragging_todo_item = True
        self._drag_start_x = event.x
        self._drag_start_y = event.y

        row.configure(border_color=self.col_drag_border, border_width=2)

        # Ghost window following the cursor
        self.drag_toplevel = tk.Toplevel(self.master)
        self.drag_toplevel.overrideredirect(True)
        self.drag_toplevel.attributes("-topmost", True)
        try:
            self.drag_toplevel.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        ghost = tk.Frame(self.drag_toplevel, bg="#FFFFFF",
                         highlightbackground=self.col_drag_border, highlightthickness=1)
        ghost.pack(fill="both", expand=True)
        tk.Label(ghost, text=self.dragged_item_data["content"], bg="#FFFFFF",
                 fg="#33353B", padx=12, pady=6).pack()
        self.drag_toplevel.geometry(f"+{event.x_root - event.x}+{event.y_root - event.y}")
        return "break"

    def _reset_row_highlights(self):
        for frame in self.todo_frames:
            if frame is not self.dragged_item_frame:
                frame.configure(fg_color=self.col_card,
                                border_color=self.col_border, border_width=1)

    def on_todo_item_drag(self, event):
        if not (self.dragged_item_frame and self.drag_toplevel):
            return
        self.drag_toplevel.geometry(
            f"+{event.x_root - self._drag_start_x}+{event.y_root - self._drag_start_y}")

        self._reset_row_highlights()
        target_widget = event.widget.winfo_containing(event.x_root, event.y_root)
        target_row = self._todo_frame_of(target_widget) if target_widget else None
        if target_row is not None and target_row is not self.dragged_item_frame:
            target_row.configure(fg_color=self.col_highlight,
                                 border_color=self.col_drag_border, border_width=2)
        return "break"

    def on_todo_item_release(self, event):
        if self.drag_toplevel:
            self.drag_toplevel.destroy()
            self.drag_toplevel = None

        if self.dragged_item_frame:
            self.dragged_item_frame.configure(border_color=self.col_border, border_width=1)
        self._reset_row_highlights()

        if self.dragged_item_data:
            target_widget = event.widget.winfo_containing(event.x_root, event.y_root)
            target_row = self._todo_frame_of(target_widget) if target_widget else None
            target_todo = target_row.todo_data if target_row is not None else None

            new_order = [t for t in self.current_todos_data
                         if t["id"] != self.dragged_item_data["id"]]
            insert_index = len(new_order)

            if target_todo and target_todo["id"] != self.dragged_item_data["id"]:
                for i, t in enumerate(new_order):
                    if t["id"] == target_todo["id"]:
                        insert_index = i
                        # Below the target's midpoint means insert after it
                        mouse_y = event.y_root - target_row.winfo_rooty()
                        if mouse_y >= target_row.winfo_height() / 2:
                            insert_index = i + 1
                        break

            new_order.insert(insert_index, self.dragged_item_data)
            if [t["id"] for t in new_order] != [t["id"] for t in self.current_todos_data]:
                self.reorder_todos_backend([t["id"] for t in new_order])

        self.dragged_item_data = None
        self.dragged_item_frame = None
        self._drag_start_x = None
        self._drag_start_y = None
        self._is_dragging_todo_item = False
        return "break"

    def reorder_todos_backend(self, ordered_ids):
        try:
            response = requests.put(f"{API_BASE_URL}/todos/reorder",
                                    json={"ordered_ids": ordered_ids},
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.load_todos()
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error", "Failed to reorder: cannot connect to server.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to reorder todos: {e}")

    # --- Periodic Sync ---
    # Tkinter widgets must only be touched from the main thread, so the
    # periodic refresh uses master.after instead of threading.Timer.
    def schedule_sync(self):
        self.master.after(SYNC_INTERVAL_MS, self.sync_data)

    def sync_data(self):
        self.load_todos()
        self.schedule_sync()

    # --- App Menu ---
    def show_app_menu(self, event):
        try:
            self.app_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.app_menu.grab_release()

    def quit_app(self):
        self.master.destroy()


# --- Application Entry Point ---
if __name__ == "__main__":
    ensure_local_server()
    root = ctk.CTk()
    app = TodoApp(root)
    root.mainloop()
