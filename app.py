from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from collections import defaultdict

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ----- Game settings -----
ROWS = 5  # dots
COLS = 5
SQR_ROWS = ROWS - 1
SQR_COLS = COLS - 1

# ----- State -----
lines = []  # list of dicts: {"start": {"row": r, "col": c}, "end": {...}, "playerNumber": n}
edge_set = set()  # normalized edges: ((r1,c1), (r2,c2))
player_count = 0
player_sessions = {}  # sessionId -> playerNumber
turn_player_number = 1
game_started = False
scores = defaultdict(int)
squares = [[0 for _ in range(SQR_COLS)] for _ in range(SQR_ROWS)]  # owner player number, 0 if empty

#--2026 best game award winner--


# ----- Helpers -----
def norm_edge(a, b):
    """Normalize an edge between two (row,col) to a canonical ordering."""
    p = (a["row"], a["col"])
    q = (b["row"], b["col"])
    return (p, q) if p <= q else (q, p)


def is_adjacent(a, b):
    return abs(a["row"] - b["row"]) + abs(a["col"] - b["col"]) == 1


def edge_exists(a, b):
    return norm_edge(a, b) in edge_set


def add_edge(a, b):
    edge_set.add(norm_edge(a, b))


def cell_edges(r, c):
    """Return the 4 normalized edges of the square cell with top-left (r,c)."""
    top_a, top_b = {"row": r, "col": c}, {"row": r, "col": c + 1}
    bottom_a, bottom_b = {"row": r + 1, "col": c}, {"row": r + 1, "col": c + 1}
    left_a, left_b = {"row": r, "col": c}, {"row": r + 1, "col": c}
    right_a, right_b = {"row": r, "col": c + 1}, {"row": r + 1, "col": c + 1}
    return [
        norm_edge(top_a, top_b),
        norm_edge(bottom_a, bottom_b),
        norm_edge(left_a, left_b),
        norm_edge(right_a, right_b),
    ]


def check_completed_cells_by_edge(a, b):
    """
    Given an edge a-b, check up to 2 adjacent cells that might be completed.
    Returns list of (r, c) cells completed now.
    """
    completed = []

    # Determine candidate cells around this edge
    r1, c1 = a["row"], a["col"]
    r2, c2 = b["row"], b["col"]

    # Horizontal edge: rows equal
    if r1 == r2:
        r = r1
        c_left = min(c1, c2)
        # Cell above: (r-1, c_left)
        if 0 <= r - 1 < SQR_ROWS and 0 <= c_left < SQR_COLS:
            if all(e in edge_set for e in cell_edges(r - 1, c_left)):
                if squares[r - 1][c_left] == 0:
                    completed.append((r - 1, c_left))
        # Cell below: (r, c_left)
        if 0 <= r < SQR_ROWS and 0 <= c_left < SQR_COLS:
            if all(e in edge_set for e in cell_edges(r, c_left)):
                if squares[r][c_left] == 0:
                    completed.append((r, c_left))

    # Vertical edge: cols equal
    elif c1 == c2:
        c = c1
        r_top = min(r1, r2)
        # Cell left: (r_top, c-1)
        if 0 <= r_top < SQR_ROWS and 0 <= c - 1 < SQR_COLS:
            if all(e in edge_set for e in cell_edges(r_top, c - 1)):
                if squares[r_top][c - 1] == 0:
                    completed.append((r_top, c - 1))
        # Cell right: (r_top, c)
        if 0 <= r_top < SQR_ROWS and 0 <= c < SQR_COLS:
            if all(e in edge_set for e in cell_edges(r_top, c)):
                if squares[r_top][c] == 0:
                    completed.append((r_top, c))

    return completed


def broadcast_state():
    emit('update_state', {
        'lines': lines,
        'squares': squares,
        'scores': dict(scores),
        'turn': turn_player_number
    }, broadcast=True)


# ----- Routes -----
@app.route('/')
def index():
    return render_template('index.html')


# ----- Socket Handlers -----
@socketio.on('new_line')
def handle_new_line(data):
    global turn_player_number, game_started

    if not game_started:
        game_started = True

    # Validate player turn
    if data.get('playerNumber') != turn_player_number:
        return  # ignore out-of-turn actions

    start = data.get('start')
    end = data.get('end')

    # Validate payload
    if start is None or end is None:
        return

    # Validate adjacency on server (safety)
    if not is_adjacent(start, end):
        return

    # Ignore duplicate edge
    if edge_exists(start, end):
        # No change in turn; just rebroadcast state so clients stay in sync
        broadcast_state()
        return

    # Record edge + line
    add_edge(start, end)
    lines.append({
        "start": {"row": start["row"], "col": start["col"]},
        "end": {"row": end["row"], "col": end["col"]},
        "playerNumber": data["playerNumber"]
    })

    # Check for completed cells
    completed_cells = check_completed_cells_by_edge(start, end)

    if completed_cells:
        player = data["playerNumber"]
        for (r, c) in completed_cells:
            squares[r][c] = player
            scores[player] += 1
        # Player gets extra turn (turn does NOT change)
        broadcast_state()
        return

    # No square completed -> advance turn
    if player_count > 0:
        turn_player_number += 1
        if turn_player_number > player_count:
            turn_player_number = 1

    broadcast_state()


@socketio.on('request_state')
def handle_request_state():
    emit('load_state', {
        'lines': lines,
        'squares': squares,
        'scores': dict(scores),
        'turn': turn_player_number
    })


@socketio.on('connect')
def handle_connect():
    global player_count, game_started

    if game_started:
        emit('game_full', {'message': 'Game has already started. No new players allowed.'})
        # Still send current state so late viewers see the board (but they won’t be assigned a number)
        emit('load_state', {
            'lines': lines,
            'squares': squares,
            'scores': dict(scores),
            'turn': turn_player_number
        })
        return

    session_id = request.args.get('sessionId')
    if not session_id:
        emit('game_full', {'message': 'Invalid session.'})
        return

    if session_id not in player_sessions:
        player_count += 1
        player_sessions[session_id] = player_count
        scores[player_count] = scores[player_count]  # initializes to 0 if new

    emit('set_player_number', {'playerNumber': player_sessions[session_id]})
    emit('load_state', {
        'lines': lines,
        'squares': squares,
        'scores': dict(scores),
        'turn': turn_player_number
    })


@socketio.on('disconnect')
def handle_disconnect():
    # Optional: you can reclaim player slots if you want.
    # For now, keep state static to avoid re-numbering chaos mid-game.
    pass


if __name__ == '__main__':
    # Use host 0.0.0.0 if deploying across LAN
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
