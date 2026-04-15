import os

from app import create_app


app = create_app()


if __name__ == "__main__":
    # На macOS с host=0.0.0.0 иногда зависает или падает в socket.getfqdn → Unknown host.
    # Для доступа из сети: FLASK_RUN_HOST=0.0.0.0 python run.py
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", "5001"))
    app.run(host=host, port=port, debug=True)
