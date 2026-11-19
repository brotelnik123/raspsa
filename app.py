import os
import threading
from flask import Flask

app = Flask('')


@app.route('/')
def home():
    return "Bot active"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)


def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
