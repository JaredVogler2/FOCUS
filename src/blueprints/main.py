# src/blueprints/main.py

from flask import Blueprint, render_template, jsonify, current_app

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def landing_page():
    return render_template('landing_page.html')

@main_bp.route('/dashboard')
def index():
    """Serve the main dashboard page"""
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
