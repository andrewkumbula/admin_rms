from flask import Flask, redirect, request, session, url_for

from app.auth.routes import bp as auth_bp
from app.config import Config
from app.extensions import csrf
from app.rms_errors import error_suggests_token_refresh
from app.modules.appointments.routes import bp as appointments_bp
from app.modules.dashboard.routes import bp as dashboard_bp
from app.modules.departments.routes import bp as departments_bp
from app.modules.dictionaries.routes import bp as dictionaries_bp
from app.modules.franchisees.routes import bp as franchisees_bp
from app.modules.service_centers.routes import bp as service_centers_bp
from app.modules.warehouses.routes import bp as warehouses_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    csrf.init_app(app)

    app.jinja_env.globals["rms_error_suggests_token_refresh"] = error_suggests_token_refresh

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(franchisees_bp)
    app.register_blueprint(service_centers_bp)
    app.register_blueprint(departments_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(dictionaries_bp)
    app.register_blueprint(warehouses_bp)

    @app.before_request
    def require_auth():
        public_endpoints = {"auth.login", "auth.callback", "auth.login_with_token", "auth.select_environment", "static"}
        if request.endpoint in public_endpoints:
            return None

        if not session.get("access_token"):
            return redirect(url_for("auth.login"))
        return None

    return app
