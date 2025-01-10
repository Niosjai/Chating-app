from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, disconnect
from flask_cors import CORS
import sqlite3
from datetime import datetime
import requests

app = Flask(__name__)
app.config["SECRET_KEY"] = "your_secret_key"
socketio = SocketIO(app, cors_allowed_origins="*")  # Enable CORS for SocketIO
CORS(app)  # Enable CORS for Flask

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect("chat_app.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_room_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# Save a message to the database
def save_message(chat_room_id, sender, message):
    conn = sqlite3.connect("chat_app.db")
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO chat (chat_room_id, sender, message, timestamp) VALUES (?, ?, ?, ?)",
                   (chat_room_id, sender, message, timestamp))
    conn.commit()
    conn.close()

# Load chat history
def load_chat_history(chat_room_id):
    conn = sqlite3.connect("chat_app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT sender, message, timestamp FROM chat WHERE chat_room_id = ? ORDER BY id", (chat_room_id,))
    messages = cursor.fetchall()
    conn.close()
    return messages
@app.route("/chat")
def chat():
    chat_room_id = request.args.get("chat_room_id")
    name = request.args.get("name")
    if not chat_room_id or not name:
        return redirect(url_for("index"))
    return render_template("chat.html", chat_room_id=chat_room_id, name=name)
    
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        chat_room_id = request.form["chat_room_id"]
        name = request.form["name"]
        return redirect(url_for("chat", chat_room_id=chat_room_id, name=name))
    return render_template("index.html")
    
# Track users in rooms
users_in_room = {}
active_rooms = {}

@app.route("/active_rooms", methods=["GET"])
def get_active_rooms():
    return jsonify([{"room": room, "users": len(users)} for room, users in active_rooms.items()])

@socketio.on("join_room")
def handle_join_room_event(data):
    chat_room_id = data["chat_room_id"]
    name = data["name"]

    # Check if username is already taken in the room
    if chat_room_id in active_rooms and name in active_rooms[chat_room_id]:
        emit("username_taken", {"error": "Username already active. Please use another name."}, to=request.sid)
        return

    join_room(chat_room_id)
    users_in_room[request.sid] = {"chat_room_id": chat_room_id, "name": name}

    # Add user to active_rooms
    if chat_room_id not in active_rooms:
        active_rooms[chat_room_id] = set()
    active_rooms[chat_room_id].add(name)

    emit("join_announcement", f"{name} has joined the room!", to=chat_room_id)

    # Load chat history and send to the user who joined
    chat_history = load_chat_history(chat_room_id)
    emit("chat_history", {"history": chat_history}, to=request.sid)

@socketio.on("disconnect")
def handle_disconnect():
    user = users_in_room.pop(request.sid, None)
    if user:
        chat_room_id = user["chat_room_id"]
        name = user["name"]
        emit("leave_announcement", f"{name} has left the room.", to=chat_room_id)

        # Remove user from active_rooms
        active_rooms[chat_room_id].remove(name)
        if not active_rooms[chat_room_id]:
            del active_rooms[chat_room_id]

# Replace with your actual Gemini API key
GEMINI_API_KEY = "AIzaSyD8benFClKMkil2awLA9nWCwu_z76wIcbE"

@socketio.on("send_message")
def handle_send_message_event(data):
    message = data["message"]
    chat_room_id = data["chat_room_id"]
    sender = data["sender"]

    # Save the original message
    save_message(chat_room_id, sender, message)

    # Emit the original message to the room
    emit("receive_message", data, to=chat_room_id)

    # Check if the message mentions @google
    if "@google" in message:
        query = message.replace("@google", "").strip()
        gemini_response = get_gemini_response(query)
        
        if gemini_response:
            gemini_data = {
                "chat_room_id": chat_room_id,
                "sender": "Google Gemini",
                "message": gemini_response
            }
            save_message(chat_room_id, "Google Gemini", gemini_response)
            emit("receive_message", gemini_data, to=chat_room_id)

def get_gemini_response(query):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [{
            "parts": [{"text": query}]
        }]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        print("Gemini API Response:", response.json())  # Log the full response for debugging
        response_data = response.json()

        # Extract the generated text from the updated response structure
        if "candidates" in response_data and len(response_data["candidates"]) > 0:
            return response_data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"Error fetching Gemini response: {e}")

    return "Sorry, I couldn't process your request."

typing_users = {}

@socketio.on("start_typing")
def handle_start_typing(data):
    chat_room_id = data["chat_room_id"]
    name = data["name"]
    
    if chat_room_id not in typing_users:
        typing_users[chat_room_id] = set()
        
    typing_users[chat_room_id].add(name)
    
    emit_typing_status(chat_room_id)

@socketio.on("stop_typing")
def handle_stop_typing(data):
    chat_room_id = data["chat_room_id"]
    name = data["name"]
    
    if chat_room_id in typing_users:
        typing_users[chat_room_id].discard(name)
        
    emit_typing_status(chat_room_id)

def emit_typing_status(chat_room_id):
    users_typing = typing_users.get(chat_room_id, set())

    for sid, user_info in users_in_room.items():
        if user_info["chat_room_id"] == chat_room_id:
            current_user = user_info["name"]
            users_typing_excluding_current = [user for user in users_typing if user != current_user]

            if users_typing_excluding_current:
                if len(users_typing_excluding_current) == 1:
                    status = f"{users_typing_excluding_current[0]} is typing..."
                elif len(users_typing_excluding_current) == 2:
                    status = f"{users_typing_excluding_current[0]} & {users_typing_excluding_current[1]} are typing..."
                else:
                    status = f"{users_typing_excluding_current[0]} + {len(users_typing_excluding_current) - 1} others are typing..."
            else:
                status = ""
            
            emit("typing_status", {"status": status}, to=sid)
    
if __name__ == "__main__":
    init_db()
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
