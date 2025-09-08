# src/blueprints/main.py

from flask import Blueprint, render_template, jsonify, current_app
import webbrowser
import threading

main_bp = Blueprint('main', __name__)

def open_browser():
    """Open the web browser to the dashboard."""
    webbrowser.open_new("http://127.0.0.1:5000/dashboard")

@main_bp.route('/')
def landing_page():
    return render_template('landing_page.html')

@main_bp.route('/dashboard')
def index():
    """Serve the main dashboard page"""
    # Open the browser automatically when the dashboard is first accessed
    if not hasattr(main_bp, 'browser_opened'):
        threading.Timer(1.25, open_browser).start()
        main_bp.browser_opened = True
    return render_template('dashboard2.html')

@main_bp.app_errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@main_bp.app_errorhandler(500)
def internal_error(error):
    # It's good practice to log the error here
    # import traceback
    # traceback.print_exc()
    return jsonify({'error': 'Internal server error'}), 500
