# 🌸 Minimal Desktop Todo App

A lightweight, aesthetic desktop todo application designed to enhance **focus** and **intentional digital use**, especially for ADHD users.
Built with **Python Tkinter + Flask + SQLite**, this app provides a persistent desktop overlay and intuitive task interactions — all synced locally.

---

## ✨ Original Intention

> *"Why did I open this computer/app/website?"*

This app serves as a **Digital Purpose Reminder** — a visual anchor on your screen that gently reminds you of your current goal. It aims to reduce digital distraction and help users stick to a singular task at a time.

It was specifically designed to support users with ADHD and other attention fragmentation challenges, combining persistence, minimalism, and seamless task management to help you regain control of your original intent.


## ✨ Features

- 🧊 Always-on-top floating overlay
- 🎨 Rounded white UI, soft accent colors, minimal aesthetic
- ✅ Active task mode — completed items get a strikethrough
- 🔁 Local sync via Flask + SQLite (backend starts automatically)
- 🧲 Drag-and-drop sorting, intuitive UX

## 🏗️ Architecture

- **Frontend**: Python Tkinter (`desktop_app.py`)
- **Backend**: Flask + SQLite REST API (`server.py`), launched automatically by the frontend when needed

## 🚀 Quick Start

### 1. Install dependencies (first time only)

```bash
pip install -r requirements.txt
```

### 2. Launch

**Windows** — just double-click **`start.bat`**. No PowerShell, no terminal windows: the app starts the backend server by itself in the background and stops it when you quit.

**Mac / Linux** — run:

```bash
python desktop_app.py
```

That's it. The database (`todos.db`) is created automatically on first run.

### Everyday use

- **Click** the floating bar to expand / collapse the todo list
- **Drag** the bar to move it anywhere on screen
- **Drag** a todo onto the bar to make it the active task
- **Right-click** anywhere on the app for the menu (refresh / quit)

## ⚙️ Configuration (optional)

By default everything runs on your own machine (`http://127.0.0.1:5000`) — **no configuration or code edits are needed**.

To sync with a server on another device in your LAN, copy `config.example.json` to `config.json` and point it at that machine:

```json
{
  "api_url": "http://192.168.1.100:5000"
}
```

(You can also set the `TODO_API_URL` environment variable, which takes priority over `config.json`.)

On the machine that hosts the data, allow LAN access by starting the server with:

```bash
TODO_HOST=0.0.0.0 python server.py
```

Server environment variables: `TODO_HOST` (default `127.0.0.1`), `TODO_PORT` (default `5000`), `TODO_DEBUG` (default off — never enable on a network-reachable interface).

> Find your local IP with `ipconfig` (Windows) or `ifconfig` / `ip addr` (Mac/Linux), and allow the port through your firewall when sharing on LAN.

## ⚠️ Notes

- Data is stored locally in `todos.db` (ignored by git)
- If the frontend can't reach a remote `api_url`, check that the server machine is running `server.py` with `TODO_HOST=0.0.0.0`
