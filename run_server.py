# ============================================================
# run_server.py — Production WSGI Server for Tayseer Agri-SaT
# ============================================================
import logging
from app import app
from config import Config

# Configure Waitress logging
logger = logging.getLogger("waitress")
logger.setLevel(logging.INFO)

if __name__ == "__main__":
    print("====================================================")
    print("Starting Tayseer Agri-SaT in PRODUCTION mode...")
    print(f"Listening on http://0.0.0.0:{Config.PORT}")
    print("====================================================")
    
    # Use Waitress for production-ready serving on Windows/Linux
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=Config.PORT, threads=6)
    except ImportError:
        print("Waitress not found. Falling back to Flask dev server.")
        print("Run: pip install waitress")
        app.run(host="0.0.0.0", port=Config.PORT, debug=False)
