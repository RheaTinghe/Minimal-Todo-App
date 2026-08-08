# server.py
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import datetime
import os

MAX_CONTENT_LENGTH = 200

# Initialize Flask application
app = Flask(__name__)

# Configure SQLite database
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'todos.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy database object
db = SQLAlchemy(app)

# Define Todo item model
class Todo(db.Model):
    __tablename__ = 'todos'
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(MAX_CONTENT_LENGTH), nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    completed_at = db.Column(db.DateTime, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True)
    priority = db.Column(db.Integer, default=0)
    position = db.Column(db.Integer, nullable=False, default=0)  # Position for ordering
    parent_id = db.Column(db.Integer, nullable=True)  # One level of subtasks

    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'is_completed': self.is_completed,
            'created_at': self.created_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'priority': self.priority,
            'position': self.position,
            'parent_id': self.parent_id
        }


class Completion(db.Model):
    """Completion log for the heatmap. Rows outlive their todos, so the
    history survives deleting finished tasks."""
    __tablename__ = 'completions'
    id = db.Column(db.Integer, primary_key=True)
    todo_id = db.Column(db.Integer, nullable=False)
    completed_on = db.Column(db.Date, nullable=False, default=datetime.date.today)


def parse_due_date(value):
    """Parse an ISO-format due date. Returns (datetime_or_None, error_or_None)."""
    if not value:
        return None, None
    try:
        return datetime.datetime.fromisoformat(value), None
    except (TypeError, ValueError):
        return None, "Invalid 'due_date'. Expected an ISO-format datetime string."


# Create database tables at import time so the schema exists no matter how
# the server is launched (python server.py, flask run, WSGI, subprocess, ...)
with app.app_context():
    db.create_all()
    # Lightweight migration: add columns that databases created by older
    # versions of this app don't have yet (create_all never alters tables)
    with db.engine.connect() as conn:
        existing = [row[1] for row in conn.execute(db.text("PRAGMA table_info(todos)"))]
        if 'completed_at' not in existing:
            conn.execute(db.text("ALTER TABLE todos ADD COLUMN completed_at DATETIME"))
        if 'parent_id' not in existing:
            conn.execute(db.text("ALTER TABLE todos ADD COLUMN parent_id INTEGER"))
        conn.commit()


# --- API Interface Definitions ---

# Get all todo items
@app.route('/todos', methods=['GET'])
def get_todos():
    # Query all todo items, order by position, then by creation time for tie-breaking
    todos = Todo.query.order_by(Todo.position.asc(), Todo.created_at.desc()).all()
    return jsonify([todo.to_dict() for todo in todos])

# Add a new todo item
@app.route('/todos', methods=['POST'])
def add_todo():
    data = request.get_json(silent=True)
    if not data or 'content' not in data or not str(data['content']).strip():
        return jsonify({"error": "Content is required"}), 400

    content = str(data['content']).strip()
    if len(content) > MAX_CONTENT_LENGTH:
        return jsonify({"error": f"Content must be at most {MAX_CONTENT_LENGTH} characters."}), 400

    due_date, err = parse_due_date(data.get('due_date'))
    if err:
        return jsonify({"error": err}), 400

    parent_id = data.get('parent_id')
    if parent_id is not None:
        parent = Todo.query.get(parent_id)
        if parent is None:
            return jsonify({"error": "Parent todo not found."}), 404
        if parent.parent_id is not None:
            return jsonify({"error": "Subtasks cannot be nested further."}), 400
        # Subtasks append below their siblings
        max_sibling = db.session.query(db.func.max(Todo.position)) \
            .filter(Todo.parent_id == parent_id).scalar()
        new_position = (max_sibling if max_sibling is not None else -1) + 1
    else:
        # New top-level todos go to the top of the list (and become the active task)
        min_position = db.session.query(db.func.min(Todo.position)).scalar()
        new_position = (min_position if min_position is not None else 1) - 1

    new_todo = Todo(
        content=content,
        due_date=due_date,
        priority=data.get('priority', 0),
        position=new_position,
        parent_id=parent_id
    )
    db.session.add(new_todo)
    db.session.commit()
    return jsonify(new_todo.to_dict()), 201

# Update a todo item
@app.route('/todos/<int:todo_id>', methods=['PUT'])
def update_todo(todo_id):
    todo = Todo.query.get_or_404(todo_id)
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "A JSON body is required."}), 400

    if 'content' in data:
        content = str(data['content']).strip()
        if not content:
            return jsonify({"error": "Content cannot be empty."}), 400
        if len(content) > MAX_CONTENT_LENGTH:
            return jsonify({"error": f"Content must be at most {MAX_CONTENT_LENGTH} characters."}), 400
        todo.content = content
    if 'is_completed' in data:
        new_state = bool(data['is_completed'])
        if new_state and not todo.is_completed:
            todo.completed_at = datetime.datetime.now()
            db.session.add(Completion(todo_id=todo.id, completed_on=datetime.date.today()))
        elif not new_state and todo.is_completed:
            todo.completed_at = None
            Completion.query.filter_by(todo_id=todo.id).delete()
        todo.is_completed = new_state
    if 'due_date' in data:
        due_date, err = parse_due_date(data['due_date'])
        if err:
            return jsonify({"error": err}), 400
        todo.due_date = due_date
    if 'priority' in data:
        todo.priority = data['priority']
    if 'position' in data:
        todo.position = data['position']

    db.session.commit()
    return jsonify(todo.to_dict())

# Reorder todos
@app.route('/todos/reorder', methods=['PUT'])
def reorder_todos():
    data = request.get_json(silent=True)
    if not data or 'ordered_ids' not in data or not isinstance(data['ordered_ids'], list):
        return jsonify({"error": "Invalid request. 'ordered_ids' (list of integers) is required."}), 400

    ordered_ids = data['ordered_ids']
    if not all(isinstance(todo_id, int) for todo_id in ordered_ids):
        return jsonify({"error": "'ordered_ids' must contain only integers."}), 400
    if len(set(ordered_ids)) != len(ordered_ids):
        return jsonify({"error": "'ordered_ids' must not contain duplicates."}), 400

    # Fetch all todos that are being reordered to ensure they exist
    todos_map = {todo.id: todo for todo in Todo.query.filter(Todo.id.in_(ordered_ids)).all()}

    if len(todos_map) != len(ordered_ids):
        return jsonify({"error": "One or more todo IDs not found."}), 404

    # Update positions based on the new order
    for index, todo_id in enumerate(ordered_ids):
        todos_map[todo_id].position = index

    db.session.commit()
    return jsonify({"message": "Todos reordered successfully."}), 200

# Delete a todo item (and its subtasks; completion history is kept)
@app.route('/todos/<int:todo_id>', methods=['DELETE'])
def delete_todo(todo_id):
    todo = Todo.query.get_or_404(todo_id)
    Todo.query.filter_by(parent_id=todo.id).delete()
    db.session.delete(todo)
    db.session.commit()
    return '', 204

# Daily completion counts for the heatmap
@app.route('/stats/completions', methods=['GET'])
def completion_stats():
    try:
        days = min(int(request.args.get('days', 119)), 366)
    except ValueError:
        return jsonify({"error": "'days' must be an integer."}), 400
    start = datetime.date.today() - datetime.timedelta(days=days)
    rows = db.session.query(Completion.completed_on, db.func.count()) \
        .filter(Completion.completed_on >= start) \
        .group_by(Completion.completed_on).all()
    return jsonify({d.isoformat(): c for d, c in rows})

# Run Flask application
if __name__ == '__main__':
    # Bind to localhost by default; set TODO_HOST=0.0.0.0 to allow LAN sync.
    # Debug mode is off by default (the Werkzeug debugger must never be
    # exposed on a network-reachable interface); set TODO_DEBUG=1 to enable.
    host = os.environ.get('TODO_HOST', '127.0.0.1')
    port = int(os.environ.get('TODO_PORT', '5000'))
    debug = os.environ.get('TODO_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(debug=debug, host=host, port=port)
