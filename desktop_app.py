# desktop_app.py
import atexit
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font, messagebox
from urllib.parse import urlparse

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

# --- Tkinter Application Class ---
class TodoApp:
    def __init__(self, master):
        self.master = master
        master.title("Minimal Todo")

        # --- Window attribute settings for floating effect with rounded corners ---
        # A specific color that will be made transparent to create rounded corners.
        # "-transparentcolor" only exists on Windows; elsewhere fall back to an
        # opaque white background (square corners instead of a crash).
        self.transparent_color = "#000001"
        try:
            master.attributes("-transparentcolor", self.transparent_color)
        except tk.TclError:
            self.transparent_color = "#FFFFFF"
        master.overrideredirect(True)  # Remove window border and title bar
        master.attributes("-topmost", True)  # Keep window on top
        try:
            master.attributes("-alpha", 0.95) # Slightly less transparent for white background
        except tk.TclError:
            pass  # Alpha compositing unavailable on some window managers
        master.geometry("300x50+100+100") # Initial size (width x height) and position (x+y)

        self.is_expanded = False # Flag for expanded state
        self.initial_height = 50 # Height when collapsed
        self.expanded_height = 300 # Height when expanded

        # Pick the nicest installed UI font instead of assuming "Inter" exists
        available_fonts = set(font.families())
        ui_font_family = next(
            (f for f in ("Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei UI",
                         "Helvetica Neue", "Helvetica")
             if f in available_fonts),
            "TkDefaultFont",
        )
        self.font_main = font.Font(family=ui_font_family, size=14, weight="bold")
        self.font_todo = font.Font(family=ui_font_family, size=12)
        self.font_todo_done = font.Font(family=ui_font_family, size=12, overstrike=1)
        self.font_small = font.Font(family=ui_font_family, size=10)

        # --- Colors for the new aesthetic ---
        self.bg_color_light = "#FFFFFF"        # Card / collapsed bar background
        self.bg_color_medium = "#F6F6F8"       # List background
        self.bg_color_dark = "#EFEFF3"         # Input background
        self.text_color_dark = "#1C1C1E"       # Primary text
        self.text_color_gray = "#9B9BA3"       # Secondary / completed text
        self.accent_color = "#5B8DEF"          # Accent (Add button, insertion line)
        self.accent_active_color = "#3E6FD9"   # Accent pressed state
        self.active_dot_color = "#34C759"      # Green dot next to the active task
        self.delete_idle_color = "#C7C7CC"     # Delete cross at rest
        self.delete_hover_color = "#FF5252"    # Delete cross on hover
        self.button_bg_color = "#E4E4EA"       # Neutral controls / scrollbar
        self.button_active_bg_color = "#D6D6DE"

        self.original_bg_color = self.bg_color_light   # Todo row background
        self.highlight_bg_color = "#E8EEFC"            # Drop-target highlight (soft blue)
        self.dragged_item_highlight_color = "#A9BFF5"  # Border of the row being dragged
        self.insertion_indicator_color = self.accent_color

        # --- Main Canvas for Rounded Corners ---
        self.bg_canvas = tk.Canvas(master, bg=self.transparent_color, highlightthickness=0)
        self.bg_canvas.pack(fill="both", expand=True)

        # Draw initial rounded rectangle on canvas (will be redrawn on resize)
        self.rounded_rect_id = None # Will store the ID of the rounded rectangle item
        self.draw_rounded_rectangle(self.master.winfo_width(), self.master.winfo_height(), self.bg_color_light) # Initial draw

        # --- Floating display section (collapsed state) - placed inside bg_canvas ---
        self.main_frame = tk.Frame(self.bg_canvas, bg=self.bg_color_light, bd=0, relief="flat")
        # Use create_window to place frames on the canvas
        self.main_frame_window = self.bg_canvas.create_window(
            0, 0, anchor="nw", window=self.main_frame, width=300, height=50 # Initial size, will adjust
        )

        # Frame to hold the circle and the todo label for the active task
        self.active_todo_display_frame = tk.Frame(self.main_frame, bg=self.bg_color_light)
        self.active_todo_display_frame.pack(pady=10)

        self.circle_label = tk.Label(self.active_todo_display_frame, text="●", fg=self.active_dot_color, bg=self.bg_color_light, font=self.font_small)
        self.circle_label.pack(side="left", padx=(0, 5))
        
        self.label_current_todo = tk.Label(self.active_todo_display_frame, text="Loading todos...", fg=self.text_color_dark, bg=self.bg_color_light,
                                            font=self.font_main, cursor="hand2")
        self.label_current_todo.pack(side="left")

        # Bind events to the master window for global response
        # This allows clicking/dragging anywhere on the window background
        self.master.bind("<ButtonPress-1>", self.on_window_button_press)
        self.master.bind("<B1-Motion>", self.on_window_mouse_drag)
        self.master.bind("<ButtonRelease-1>", self.on_window_button_release)

        # Right-click anywhere for the app menu (the borderless window has no
        # title bar, so this is the way to quit without killing the process)
        self.app_menu = tk.Menu(master, tearoff=0)
        self.app_menu.add_command(label="Refresh now", command=self.load_todos)
        self.app_menu.add_separator()
        self.app_menu.add_command(label="Quit Minimal Todo", command=self.quit_app)
        self.master.bind_all("<Button-3>", self.show_app_menu)
        self.master.bind_all("<Button-2>", self.show_app_menu)  # macOS middle/right variants

        # --- Expanded todo list section - placed inside bg_canvas ---
        self.todo_list_frame = tk.Frame(self.bg_canvas, bg=self.bg_color_medium)
        self.todo_list_frame_window = self.bg_canvas.create_window(
            0, 0, anchor="nw", window=self.todo_list_frame, width=300, height=300 # Initial size, will adjust
        )
        self.bg_canvas.itemconfigure(self.todo_list_frame_window, state='hidden') # Initially hidden

        # Input field and Add button
        self.input_frame = tk.Frame(self.todo_list_frame, bg=self.bg_color_medium)
        self.input_frame.pack(fill="x", pady=(10, 6), padx=10)

        self.todo_input = tk.Entry(self.input_frame, bg=self.bg_color_dark, fg=self.text_color_dark,
                                   insertbackground=self.text_color_dark, font=self.font_todo,
                                   relief="flat", bd=0, highlightbackground=self.button_bg_color,
                                   highlightcolor=self.accent_color, highlightthickness=1)
        self.todo_input.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=5)
        self.todo_input.bind("<Return>", self.add_todo_event)

        self.add_button = tk.Button(self.input_frame, text="Add", command=self.add_todo,
                                    bg=self.accent_color, fg="white", relief="flat", font=self.font_todo,
                                    activebackground=self.accent_active_color, activeforeground="white",
                                    bd=0, padx=12, pady=5, cursor="hand2")
        self.add_button.pack(side="right")

        # Todo list display area (using Canvas and Scrollbar for scrollable list)
        self.todo_canvas = tk.Canvas(self.todo_list_frame, bg=self.bg_color_medium, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.todo_list_frame, orient="vertical", command=self.todo_canvas.yview,
                                      troughcolor=self.bg_color_dark, bg=self.button_bg_color, activebackground=self.button_active_bg_color)
        self.scrollable_frame = tk.Frame(self.todo_canvas, bg=self.bg_color_medium)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.todo_canvas.configure(
                scrollregion=self.todo_canvas.bbox("all")
            )
        )

        self.todo_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.todo_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.todo_canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.scrollbar.pack(side="right", fill="y")

        # --- Drag and Drop for Todo Items ---
        self.dragged_item_data = None # Stores data of the item being dragged
        self.dragged_item_frame = None # Stores the Tkinter frame of the original item being dragged
        self.drag_toplevel = None # Toplevel window for visual drag (the "ghost" item)
        
        # Create insertion indicator line on the todo_canvas once in __init__
        self.insertion_indicator_line_id = self.todo_canvas.create_line(
            0, 0, 0, 0, # Initial coordinates (will be updated)
            fill=self.insertion_indicator_color, width=5, state='hidden', arrow="last" # Hidden initially, increased width, added arrow
        )
        
        self.todo_frames = [] # List to keep track of todo item frames and their data
        self.current_todos_data = [] # Stores the current order of todo data (dictionaries)

        # Window drag properties
        self._offset_x = None # Mouse X position relative to the widget when pressed
        self._offset_y = None # Mouse Y position relative to the widget when pressed
        self._is_dragging_window = False # Flag for window dragging
        self._is_dragging_todo_item = False # Flag to indicate if a todo item is being dragged
        self._click_threshold = 5 # Pixel threshold to distinguish click from drag (for window and todo items)

        # Bind resize event to redraw rounded rectangle
        self.master.bind("<Configure>", self.on_window_configure)

        # Initial load of todos and start periodic sync
        self.load_todos()
        self.schedule_sync()

    def on_window_configure(self, event):
        # Redraw the rounded rectangle when the window size changes
        current_width = self.master.winfo_width()
        current_height = self.master.winfo_height()
        if current_width > 0 and current_height > 0: # Ensure valid dimensions
            self.draw_rounded_rectangle(current_width, current_height, self.bg_color_light)
            
            # Adjust the size and position of the frames placed on the canvas
            if self.is_expanded:
                self.bg_canvas.coords(self.todo_list_frame_window, 0, 0)
                self.bg_canvas.itemconfigure(self.todo_list_frame_window, width=current_width, height=current_height)
            else:
                self.bg_canvas.coords(self.main_frame_window, 0, 0)
                self.bg_canvas.itemconfigure(self.main_frame_window, width=current_width, height=current_height)

    def draw_rounded_rectangle(self, width, height, fill_color, radius=20):
        self.bg_canvas.delete("rounded_bg_shape") # Delete old shape
        
        # Draw a filled rounded rectangle directly using create_polygon with smooth=True
        # This creates a visually rounded shape, though it's technically a polygon
        self.bg_canvas.create_polygon(
            radius, 0,
            width - radius, 0,
            width, radius,
            width, height - radius,
            width - radius, height,
            radius, height,
            0, height - radius,
            0, radius,
            smooth=True, fill=fill_color, outline="", tags="rounded_bg_shape"
        )
        self.bg_canvas.tag_lower("rounded_bg_shape") # Ensure it's at the bottom layer


    # --- Window Drag and Click Event Handlers ---
    def on_window_button_press(self, event):
        # Store initial mouse position relative to the *window*
        self._offset_x = event.x
        self._offset_y = event.y
        self._is_dragging_window = False

    def on_window_mouse_drag(self, event):
        if self._offset_x is not None and self._offset_y is not None:
            # Calculate distance moved from initial press point on screen
            current_x_root = self.master.winfo_pointerx()
            current_y_root = self.master.winfo_pointery()
            
            distance_moved_x = current_x_root - (self.master.winfo_x() + self._offset_x)
            distance_moved_y = current_y_root - (self.master.winfo_y() + self._offset_y)
            
            distance = (distance_moved_x**2 + distance_moved_y**2)**0.5

            if distance > self._click_threshold:
                self._is_dragging_window = True
                # Move window to new position: current mouse screen position - initial offset within window
                self.master.geometry(f"+{event.x_root - self._offset_x}+{event.y_root - self._offset_y}")

    def _is_part_of_todo_item(self, widget):
        # Ensure widget is a Tkinter widget before trying to access attributes like master
        if not isinstance(widget, tk.Widget):
            return False

        current_widget = widget
        # Traverse up the widget hierarchy to see if any ancestor is a todo_frame
        while current_widget is not None and current_widget != self.master:
            if hasattr(current_widget, 'todo_data'):
                return True
            # Safely get master, handling cases where it might not exist or be a string
            # Use try-except to gracefully handle widgets without a 'master' attribute or if it's not a widget
            try:
                if hasattr(current_widget, 'master') and isinstance(current_widget.master, tk.Widget):
                    current_widget = current_widget.master
                else:
                    current_widget = None # Break loop if master is not a widget or doesn't exist
            except AttributeError:
                current_widget = None # Break loop on AttributeError
        return False

    def on_window_button_release(self, event):
        # Only toggle if it's a genuine click on the window's background/label, not an interactive widget
        # Also ensure it's not a click on a todo item frame (which handles its own release)
        # New: Added check for _is_dragging_todo_item to prevent collapse after todo drag
        if not self._is_dragging_window and not self._is_dragging_todo_item:
            is_interactive_widget = isinstance(event.widget, (tk.Entry, tk.Button, tk.Checkbutton, tk.Scrollbar))
            is_todo_item_part = self._is_part_of_todo_item(event.widget)
            
            # If the click was not on an interactive widget and not on a todo item part,
            # then it's a click on the general window background.
            if not is_interactive_widget and not is_todo_item_part:
                self.toggle_expand()
        self._offset_x = None
        self._offset_y = None
        self._is_dragging_window = False

    # --- Expand/Collapse Window Function ---
    def toggle_expand(self, event=None):
        if self.is_expanded:
            # Collapse window
            self.master.geometry(f"300x{self.initial_height}+{self.master.winfo_x()}+{self.master.winfo_y()}")
            self.bg_canvas.itemconfigure(self.todo_list_frame_window, state='hidden')
            self.bg_canvas.itemconfigure(self.main_frame_window, state='normal')
            # self.main_frame.pack(fill="both", expand=True) # No need to pack, handled by create_window
        else:
            # Expand window
            self.master.geometry(f"300x{self.expanded_height}+{self.master.winfo_x()}+{self.master.winfo_y()}")
            self.bg_canvas.itemconfigure(self.main_frame_window, state='hidden')
            self.bg_canvas.itemconfigure(self.todo_list_frame_window, state='normal')
            # self.todo_list_frame.pack(fill="both", expand=True) # No need to pack, handled by create_window
            self.load_todos() # Refresh todo list on expand to ensure latest data
        self.is_expanded = not self.is_expanded
        self.on_window_configure(None) # Redraw rounded corners for new size

    # --- Load Todo Items ---
    def load_todos(self):
        try:
            response = requests.get(f"{API_BASE_URL}/todos", timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            todos = response.json()
            self.current_todos_data = todos # Update the internal list of todo data
            self.update_todo_display(todos)
        except requests.exceptions.ConnectionError:
            self.label_current_todo.config(text="Cannot connect to server", fg="red")
            self.circle_label.pack_forget() # Hide circle if no connection
            print("Error: Could not connect to the backend server. Please ensure it is running.")
        except requests.exceptions.HTTPError as e:
            self.label_current_todo.config(text="Load failed", fg="red")
            self.circle_label.pack_forget() # Hide circle if load failed
            print(f"HTTP Error loading todos: {e}")
        except Exception as e:
            self.label_current_todo.config(text="Load failed", fg="red")
            self.circle_label.pack_forget() # Hide circle if load failed
            print(f"Error loading todos: {e}")

    # --- Update Todo Item Display ---
    def update_todo_display(self, todos):
        # Clear old todo display
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.todo_frames.clear() # Clear the list of todo frames

        # Filter incomplete todos for the floating window
        incomplete_todos = [todo for todo in todos if not todo['is_completed']]

        if incomplete_todos:
            self.label_current_todo.config(text=incomplete_todos[0]['content'], fg=self.text_color_dark)
            self.circle_label.pack(side="left", padx=(0, 5)) # Show circle next to active todo
        else:
            self.label_current_todo.config(text="No todos yet", fg=self.text_color_gray)
            self.circle_label.pack_forget() # Hide circle if no todos

        # Display all todos in the expanded list
        for todo in todos:
            frame = tk.Frame(self.scrollable_frame, bg=self.original_bg_color, bd=0, relief="flat",
                             highlightbackground=self.highlight_bg_color, highlightthickness=0)
            frame.pack(fill="x", pady=3, padx=6, ipady=4)

            # Store todo data directly on the frame for drag-and-drop
            frame.todo_data = todo

            # Bind drag and drop events to the todo item frame
            frame.bind("<ButtonPress-1>", self.on_todo_item_press)
            frame.bind("<B1-Motion>", self.on_todo_item_drag)
            frame.bind("<ButtonRelease-1>", self.on_todo_item_release)
            frame.bind("<Enter>", self.on_todo_item_enter) # For drop target highlighting
            frame.bind("<Leave>", self.on_todo_item_leave) # For drop target un-highlighting

            check_var = tk.BooleanVar(value=todo['is_completed'])
            checkbox = tk.Checkbutton(frame, variable=check_var,
                                      command=lambda t=todo, v=check_var: self.toggle_complete(t, v),
                                      bg=self.original_bg_color, fg=self.text_color_dark, selectcolor=self.button_bg_color,
                                      activebackground=self.original_bg_color, bd=0, relief="flat")
            checkbox.pack(side="left", padx=(6, 5))

            label = tk.Label(frame, text=todo['content'], bg=self.original_bg_color, fg=self.text_color_dark, font=self.font_todo, anchor="w", wraplength=220) # Added wraplength
            label.pack(side="left", fill="x", expand=True)
            # Bind drag events to label as well, so dragging on text works
            label.bind("<ButtonPress-1>", self.on_todo_item_press)
            label.bind("<B1-Motion>", self.on_todo_item_drag)
            label.bind("<ButtonRelease-1>", self.on_todo_item_release)
            label.bind("<Enter>", self.on_todo_item_enter)
            label.bind("<Leave>", self.on_todo_item_leave)


            if todo['is_completed']:
                label.config(fg=self.text_color_gray, font=self.font_todo_done)

            # Quiet gray cross that turns red on hover — less visually noisy
            # than a permanently red button on every row
            delete_button = tk.Button(frame, text="✕", command=lambda t=todo: self.delete_todo(t),
                                      bg=self.original_bg_color, fg=self.delete_idle_color, relief="flat",
                                      font=self.font_small, activebackground=self.original_bg_color,
                                      activeforeground=self.delete_hover_color, bd=0, padx=8, pady=2,
                                      cursor="hand2")
            delete_button.pack(side="right", padx=(0, 4))
            delete_button.bind("<Enter>", lambda e, b=delete_button: b.config(fg=self.delete_hover_color))
            delete_button.bind("<Leave>", lambda e, b=delete_button: b.config(fg=self.delete_idle_color))

            # Store the frame and its associated todo data
            self.todo_frames.append(frame)

        self.scrollable_frame.update_idletasks()
        self.todo_canvas.config(scrollregion=self.todo_canvas.bbox("all"))

    # --- Add Todo Item ---
    def add_todo_event(self, event=None):
        self.add_todo()

    def add_todo(self):
        content = self.todo_input.get().strip()
        if content:
            try:
                response = requests.post(f"{API_BASE_URL}/todos", json={'content': content}, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                self.todo_input.delete(0, tk.END)
                self.load_todos()
            except requests.exceptions.ConnectionError:
                messagebox.showerror("Error", "Cannot connect to server, please check network or server status.")
            except requests.exceptions.HTTPError as e:
                messagebox.showerror("Error", f"Failed to add todo: {e}")
            except Exception as e:
                messagebox.showerror("Error", f"Unknown error adding todo: {e}")
        else:
            messagebox.showwarning("Warning", "Todo content cannot be empty.")

    # --- Toggle Todo Complete Status ---
    def toggle_complete(self, todo, var):
        is_completed = var.get()
        try:
            response = requests.put(f"{API_BASE_URL}/todos/{todo['id']}", json={'is_completed': is_completed}, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.load_todos()
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error", "Cannot connect to server, please check network or server status.")
            var.set(not is_completed)
        except requests.exceptions.HTTPError as e:
            messagebox.showerror("Error", f"Failed to update todo: {e}")
            var.set(not is_completed)
        except Exception as e:
            messagebox.showerror("Error", f"Unknown error updating todo: {e}")
            var.set(not is_completed)

    # --- Delete Todo Item ---
    def delete_todo(self, todo):
        if messagebox.askyesno("Delete Confirmation", f"Are you sure you want to delete todo: '{todo['content']}'?"):
            try:
                response = requests.delete(f"{API_BASE_URL}/todos/{todo['id']}", timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                self.load_todos()
            except requests.exceptions.ConnectionError:
                messagebox.showerror("Error", "Cannot connect to server, please check network or server status.")
            except requests.exceptions.HTTPError as e:
                messagebox.showerror("Error", f"Failed to delete todo: {e}")
            except Exception as e:
                messagebox.showerror("Error", f"Unknown error deleting todo: {e}")

    # --- Todo Item Drag and Drop Handlers ---
    def on_todo_item_press(self, event):
        # Determine the actual todo item frame being clicked
        target_frame = None
        if hasattr(event.widget, 'todo_data'):
            target_frame = event.widget
        elif hasattr(event.widget.master, 'todo_data'):
            target_frame = event.widget.master
        
        if target_frame:
            self.dragged_item_data = target_frame.todo_data
            self.dragged_item_frame = target_frame

            self._is_dragging_todo_item = True # Set flag when drag starts

            # Highlight the original dragged item with a border
            self.dragged_item_frame.config(highlightbackground=self.dragged_item_highlight_color, highlightthickness=2)
            
            # Create a Toplevel window for visual dragging (the "ghost" item)
            self.drag_toplevel = tk.Toplevel(self.master)
            self.drag_toplevel.overrideredirect(True) # No border
            self.drag_toplevel.attributes("-alpha", 0.8) # Semi-transparent, adjusted for better visibility
            self.drag_toplevel.attributes("-topmost", True) # Make the dragged item always on top
            
            # Create a frame inside the toplevel to hold the item's content
            # Use a slightly lighter color for the ghost item to make it stand out
            drag_content_frame = tk.Frame(self.drag_toplevel, bg="#D0D0D0", bd=1, relief="solid", highlightbackground="#A0A0A0", highlightthickness=1) 
            drag_content_frame.pack(fill="both", expand=True)

            # Copy relevant content (label text) to the toplevel
            drag_label = tk.Label(drag_content_frame, text=self.dragged_item_data['content'], fg=self.text_color_dark, bg="#D0D0D0", font=self.font_todo)
            drag_label.pack(padx=10, pady=5)
            
            # Position the toplevel at the mouse cursor
            # event.x and event.y are relative to the widget that received the event
            # event.x_root and event.y_root are absolute screen coordinates
            self.drag_toplevel.geometry(f"+{event.x_root - event.x}+{event.y_root - event.y}")

            # The insertion_indicator_line_id is already created in __init__
            self.todo_canvas.itemconfigure(self.insertion_indicator_line_id, state='hidden') # Ensure it's hidden before drag starts


            # Store initial mouse position relative to the item for smooth dragging
            self._drag_start_x = event.x
            self._drag_start_y = event.y
            
            return "break" # Crucial: Stop event propagation to the master window

        self.dragged_item_data = None
        self.dragged_item_frame = None
        self.drag_toplevel = None # Ensure toplevel is reset
        # insertion_indicator_frame is persistent, no need to reset here
        return # Allow propagation if not a todo item

    def on_todo_item_drag(self, event):
        if self.dragged_item_frame and self.drag_toplevel and self._drag_start_x is not None:
            # Update the Toplevel window's position to follow the mouse
            self.drag_toplevel.geometry(f"+{event.x_root - self._drag_start_x}+{event.y_root - self._drag_start_y}")
            
            # Reset all highlights first
            self.main_frame.config(bg=self.bg_color_light)
            self.label_current_todo.config(bg=self.bg_color_light)
            self.active_todo_display_frame.config(bg=self.bg_color_light)
            self.circle_label.config(bg=self.bg_color_light)
            for frame in self.todo_frames:
                if frame != self.dragged_item_frame: # Don't reset the original dragged item's highlight (it's hidden anyway)
                    frame.config(bg=self.original_bg_color)
                    for child in frame.winfo_children():
                        if isinstance(child, (tk.Label, tk.Checkbutton, tk.Button)):
                            child.config(bg=self.original_bg_color)
            # Hide insertion indicator line initially for this drag event
            self.todo_canvas.itemconfigure(self.insertion_indicator_line_id, state='hidden')


            # Determine current target widget
            target_widget = event.widget.winfo_containing(event.x_root, event.y_root)
            
            # Check if hovering over the main floating frame (active task drop zone)
            is_hovering_active_zone = (target_widget == self.main_frame or 
                                       target_widget == self.label_current_todo or 
                                       target_widget == self.active_todo_display_frame or 
                                       target_widget == self.circle_label or
                                       # Also check if hovering over the canvas window of main_frame
                                       (self.bg_canvas.itemcget(self.main_frame_window, 'state') == 'normal' and 
                                        self.bg_canvas.find_withtag("current") and 
                                        self.bg_canvas.find_withtag("current")[0] == self.main_frame_window))


            if is_hovering_active_zone:
                # Highlight the main frame
                self.main_frame.config(bg=self.highlight_bg_color)
                self.label_current_todo.config(bg=self.highlight_bg_color)
                self.active_todo_display_frame.config(bg=self.highlight_bg_color)
                self.circle_label.config(bg=self.highlight_bg_color)
            else:
                # Check if hovering over another todo item for reordering within the list
                # target_widget is None when the pointer is outside any of our
                # windows (e.g. dragged off-screen), so guard before .master
                current_target_frame = None
                if hasattr(target_widget, 'todo_data'):
                    current_target_frame = target_widget
                elif target_widget is not None and hasattr(target_widget.master, 'todo_data'):
                    current_target_frame = target_widget.master
                
                if current_target_frame and current_target_frame != self.dragged_item_frame:
                    # Highlight the target frame
                    current_target_frame.config(bg=self.highlight_bg_color)
                    for child in current_target_frame.winfo_children():
                        if isinstance(child, (tk.Label, tk.Checkbutton, tk.Button)):
                            child.config(bg=self.highlight_bg_color)
                    
                    # Position insertion indicator line
                    # Get y-coordinate of the target frame relative to the todo_canvas
                    # Ensure current_target_frame is mapped to screen to get accurate info
                    current_target_frame.update_idletasks() 
                    
                    # Convert target frame's y-coordinates to canvas coordinates
                    target_frame_y_on_canvas = self.todo_canvas.canvasy(current_target_frame.winfo_y())
                    target_frame_height = current_target_frame.winfo_height()

                    # Determine if inserting before or after the target
                    # Mouse Y relative to the top of the target frame
                    mouse_y_relative_to_target_frame_top = event.y_root - current_target_frame.winfo_rooty()
                    
                    line_y_pos = 0
                    if mouse_y_relative_to_target_frame_top < target_frame_height / 2:
                        # Insert before target: position line at the top of the target frame
                        line_y_pos = target_frame_y_on_canvas
                    else:
                        # Insert after target: position line at the bottom of the target frame
                        line_y_pos = target_frame_y_on_canvas + target_frame_height

                    # Update the coordinates of the insertion line
                    # Draw from right to left to make arrow point left
                    self.todo_canvas.coords(
                        self.insertion_indicator_line_id,
                        self.todo_canvas.winfo_width(), line_y_pos, # Start X (right), Start Y
                        0, line_y_pos # End X (left), End Y
                    )
                    self.todo_canvas.itemconfigure(self.insertion_indicator_line_id, state='normal') # Show the line
                    self.todo_canvas.tag_raise(self.insertion_indicator_line_id) # Ensure it's on top
                else:
                    # If not hovering over any specific todo item, hide indicator
                    self.todo_canvas.itemconfigure(self.insertion_indicator_line_id, state='hidden')


    def on_todo_item_release(self, event):
        # Destroy the Toplevel window when drag ends
        if self.drag_toplevel:
            self.drag_toplevel.destroy()
            self.drag_toplevel = None

        # Hide insertion indicator
        self.todo_canvas.itemconfigure(self.insertion_indicator_line_id, state='hidden')

        # Reset original dragged item highlight (rows have no border at rest)
        if self.dragged_item_frame:
            self.dragged_item_frame.config(highlightbackground=self.highlight_bg_color, highlightthickness=0)


        if self.dragged_item_data:
            target_widget = event.widget.winfo_containing(event.x_root, event.y_root)
            target_todo_data = None
            is_dropped_on_active_zone = False

            # Check if dropped on the main floating frame (to set as active task)
            is_dropped_on_active_zone = (target_widget == self.main_frame or 
                                       target_widget == self.label_current_todo or 
                                       target_widget == self.active_todo_display_frame or 
                                       target_widget == self.circle_label or
                                       (self.bg_canvas.itemcget(self.main_frame_window, 'state') == 'normal' and 
                                        self.bg_canvas.find_withtag("current") and 
                                        self.bg_canvas.find_withtag("current")[0] == self.main_frame_window))


            if not is_dropped_on_active_zone:
                if hasattr(target_widget, 'todo_data'):
                    target_todo_data = target_widget.todo_data
                elif target_widget is not None and hasattr(target_widget.master, 'todo_data'):
                    target_todo_data = target_widget.master.todo_data

            # Reset highlight for all potential targets (including main_frame)
            self.main_frame.config(bg=self.bg_color_light) # Ensure main frame highlight is off
            self.label_current_todo.config(bg=self.bg_color_light)
            self.active_todo_display_frame.config(bg=self.bg_color_light)
            self.circle_label.config(bg=self.bg_color_light)
            for frame in self.todo_frames:
                frame.config(bg=self.original_bg_color)
                for child in frame.winfo_children():
                    if isinstance(child, (tk.Label, tk.Checkbutton, tk.Button)):
                        child.config(bg=self.original_bg_color)


            # --- Reordering Logic ---
            # Create a mutable copy of the current todos data
            new_ordered_todos = [t for t in self.current_todos_data if t['id'] != self.dragged_item_data['id']] # Filter out dragged item
            
            item_to_move = self.dragged_item_data
            
            insert_index = len(new_ordered_todos) # Default: append to end

            if is_dropped_on_active_zone:
                insert_index = 0
            elif target_todo_data:
                # Find the index of the target item in the *modified* list (after removal)
                current_target_index_in_new_list = -1
                for i, todo in enumerate(new_ordered_todos):
                    if todo['id'] == target_todo_data['id']:
                        current_target_index_in_new_list = i
                        break
                
                if current_target_index_in_new_list != -1:
                    # Determine if dropping before or after the target based on mouse Y position
                    target_frame_for_pos = None
                    # Find the actual Tkinter frame for the target data from the currently packed frames
                    for frame in self.todo_frames: 
                        if hasattr(frame, 'todo_data') and frame.todo_data['id'] == target_todo_data['id']:
                            target_frame_for_pos = frame
                            break
                    
                    if target_frame_for_pos:
                        # Calculate mouse Y relative to the target frame's root Y
                        mouse_y_relative_to_target = event.y_root - target_frame_for_pos.winfo_rooty()
                        if mouse_y_relative_to_target < target_frame_for_pos.winfo_height() / 2:
                            insert_index = current_target_index_in_new_list # Insert before
                        else:
                            insert_index = current_target_index_in_new_list + 1 # Insert after
                    else:
                        # Fallback if target frame not found (e.g., it was the dragged item itself, or not packed)
                        insert_index = len(new_ordered_todos) 
                else:
                    # Fallback if target not found in new_ordered_todos (e.g., dropped on a non-todo widget within scrollable_frame)
                    insert_index = len(new_ordered_todos)
            else:
                # Dropped on empty space within the scrollable frame, append to end
                insert_index = len(new_ordered_todos)

            # Insert the dragged item at the determined position
            new_ordered_todos.insert(insert_index, item_to_move)

            # Update the backend with the new order
            ordered_ids = [todo['id'] for todo in new_ordered_todos]
            self.reorder_todos_backend(ordered_ids)
            
            self._reset_drag_state() # Reset drag flags and variables
            return "break" # Crucial: Stop event propagation to the master window

    def _reset_drag_state(self):
        self.dragged_item_data = None
        self.dragged_item_frame = None
        self._drag_start_x = None
        self._drag_start_y = None
        self._is_dragging_todo_item = False # Reset flag when drag ends
        self.todo_canvas.itemconfigure(self.insertion_indicator_line_id, state='hidden') # Ensure indicator is hidden

    def on_todo_item_enter(self, event):
        # Highlight target if dragging
        if self.dragged_item_data: # Only highlight if an item is currently being dragged
            target_frame = None
            if hasattr(event.widget, 'todo_data'):
                target_frame = event.widget
            elif hasattr(event.widget.master, 'todo_data'):
                target_frame = event.widget.master

            if target_frame and target_frame != self.dragged_item_frame:
                target_frame.config(bg=self.highlight_bg_color)
                for child in target_frame.winfo_children():
                    if isinstance(child, (tk.Label, tk.Checkbutton, tk.Button)):
                        child.config(bg=self.highlight_bg_color)

    def on_todo_item_leave(self, event):
        # Remove highlight when leaving
        if self.dragged_item_data: # Only unhighlight if an item is currently being dragged
            target_frame = None
            if hasattr(event.widget, 'todo_data'):
                target_frame = event.widget
            elif hasattr(event.widget.master, 'todo_data'):
                target_frame = event.widget.master

            if target_frame and target_frame.cget('bg') == self.highlight_bg_color:
                target_frame.config(bg=self.original_bg_color)
                for child in target_frame.winfo_children():
                    if isinstance(child, (tk.Label, tk.Checkbutton, tk.Button)):
                        child.config(bg=self.original_bg_color)

    # --- Backend Reorder Call ---
    def reorder_todos_backend(self, ordered_ids):
        try:
            response = requests.put(f"{API_BASE_URL}/todos/reorder", json={'ordered_ids': ordered_ids}, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.load_todos() # Reload to ensure UI matches backend
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error", "Failed to reorder: Cannot connect to server.")
        except requests.exceptions.HTTPError as e:
            messagebox.showerror("Error", "Failed to reorder todos: " + str(e)) # Convert error to string
        except Exception as e:
            messagebox.showerror("Error", "Unknown error reordering todos: " + str(e)) # Convert error to string

    # --- Schedule Data Sync ---
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
if __name__ == '__main__':
    ensure_local_server()
    root = tk.Tk()
    app = TodoApp(root)
    root.mainloop()
