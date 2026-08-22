import os
from flask import Flask, render_template
from dotenv import load_dotenv

# Load configurations from .env
load_dotenv()

from routes.api import api_bp

app = Flask(__name__)

# Register API blueprints
app.register_blueprint(api_bp, url_prefix="/api")

@app.route("/")
def index():
    """Serve the dashboard interface."""
    return render_template("index.html")

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    
    print(f"Starting AI-Based Village Pond Planning System on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
