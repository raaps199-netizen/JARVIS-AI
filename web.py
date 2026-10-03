from flask import Flask, jsonify, render_template, request

from main import create_client, ask_jarvis

app = Flask(__name__)

try:
    client = create_client()
    startup_error = None
except Exception as error:
    client = None
    startup_error = str(error)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify({
        "online": client is not None,
        "error": startup_error,
    })


@app.post("/api/chat")
def chat():
    global client, startup_error

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        return jsonify({"error": "Pesan kosong."}), 400

    if client is None:
        try:
            client = create_client()
            startup_error = None
        except Exception as error:
            startup_error = str(error)
            return jsonify({"error": startup_error}), 500

    try:
        reply = ask_jarvis(client, message)
        return jsonify({"reply": reply})
    except Exception as error:
        return jsonify({"error": str(error)}), 500


if __name__ == "__main__":
    print("JARVIS dashboard running at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
