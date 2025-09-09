# app.py - Updated Flask Web Server for Production Scheduling Dashboard
# Compatible with corrected ProductionScheduler with product-task instances
# OPTIMIZED: Limits dashboard data to top 1000 tasks for performance

from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS
import pandas as pd
import json
from datetime import datetime, timedelta
import os
from collections import defaultdict
import traceback
import re
from src.server_utils import export_scenario_with_capacities

# Import the corrected scheduler
from src.scheduler.main import ProductionScheduler

def create_app():
    """Create and configure an instance of the Flask application."""
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    CORS(app)
    app.config['JSON_AS_ASCII'] = False

    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.jinja_env.auto_reload = True
    app.jinja_env.cache = {}

    # Use app context to store shared data
    with app.app_context():
        app.scheduler = None
        app.scenario_results = {}
        app.saved_scenarios = {}
        app.mechanic_assignments = {}



    from src.blueprints.main import main_bp
    from src.blueprints.scenarios import scenarios_bp
    from src.blueprints.assignments import assignments_bp
    from src.blueprints.supply_chain import supply_chain_bp
    from src.blueprints.industrial_engineering import ie_bp

    def initialize_scheduler_with_context():
        """Initialize the scheduler within the application context."""
        with app.app_context():
            try:
                print("=" * 80)
                print("Initializing Production Scheduler Dashboard")
                print("=" * 80)

                scheduler = ProductionScheduler('scheduling_data.csv', debug=False, late_part_delay_days=1.0)
                scheduler.load_data_from_csv()
                app.scheduler = scheduler

                print("\nScheduler loaded successfully!")
                print(f"Total task instances: {len(scheduler.tasks)}")

                # Run scenarios
                run_all_scenarios(app)

            except Exception as e:
                print(f"\n✗ ERROR during initialization: {str(e)}")
                traceback.print_exc()
                # Create minimal data so server can run
                app.scenario_results = {'baseline': {'tasks': [], 'products': []}}


    def run_all_scenarios(app):
        """Run all scheduling scenarios and store the results in the app context."""
        scheduler = app.scheduler
        scenario_results = {}

        print("\n" + "-" * 40)
        print("Running ALL scenarios...")

        # Baseline
        scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
        scenario_results['baseline'] = export_scenario_with_capacities(scheduler, 'baseline')
        print(f"✓ Baseline complete: {scenario_results['baseline']['makespan']} days makespan")

        # Scenario 1
        result1 = scheduler.scenario_1_csv_headcount()
        scenario_results['scenario1'] = export_scenario_with_capacities(scheduler, 'scenario1')
        print(f"✓ Scenario 1 complete: {scenario_results['scenario1']['makespan']} days makespan")

        # Scenario 3
        result3 = scheduler.scenario_3_optimal_schedule()
        if result3 and result3.get('status') == 'SUCCESS':
            # The new scenario will directly modify the scheduler's state
            scenario_results['scenario3'] = export_scenario_with_capacities(scheduler, 'scenario3')
            print(f"✓ Scenario 3 complete: {scenario_results.get('scenario3', {}).get('makespan', 'N/A')} days makespan")
        else:
            print("✗ Scenario 3 failed to find a valid solution.")
            # Optionally, create a placeholder result for the UI
            scenario_results['scenario3'] = {
                'scenarioId': 'scenario3', 'status': 'FAILED', 'tasks': [], 'products': [],
                'teamCapacities': {}, 'teamShifts': {}, 'utilization': {}, 'totalWorkforce': 0,
                'makespan': 'N/A', 'onTimeRate': 0, 'maxLateness': 'N/A'
            }

        # Restore original capacities
        for team, capacity in scheduler._original_team_capacity.items(): scheduler.team_capacity[team] = capacity
        for team, capacity in scheduler._original_quality_capacity.items(): scheduler.quality_team_capacity[team] = capacity

        app.scenario_results = scenario_results
        print("\n" + "=" * 80)
        print("All scenarios completed successfully!")
        print("=" * 80)


    # Initialize the scheduler
    initialize_scheduler_with_context()

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(scenarios_bp, url_prefix='/api')
    app.register_blueprint(assignments_bp, url_prefix='/api')
    app.register_blueprint(supply_chain_bp)
    app.register_blueprint(ie_bp)

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500

    return app