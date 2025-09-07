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

# Import the corrected scheduler
from src.scheduler.main import ProductionScheduler

def create_app():
    """Create and configure an instance of the Flask application."""
    app = Flask(__name__, template_folder='../../templates', static_folder='../../static')
    CORS(app)

    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.jinja_env.auto_reload = True
    app.jinja_env.cache = {}

    # Use app context to store shared data
    with app.app_context():
        app.scheduler = None
        app.scenario_results = {}
        app.mechanic_assignments = {}



    from src.blueprints.main import main_bp
    from src.blueprints.scenarios import scenarios_bp
    from src.blueprints.assignments import assignments_bp

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

        # Scenario 2
        result2 = scheduler.scenario_2_minimize_makespan(min_mechanics=1, max_mechanics=30, min_quality=1, max_quality=10)
        if result2:
            scheduler._scenario2_optimal_mechanics = result2['optimal_mechanics']
            scheduler._scenario2_optimal_quality = result2['optimal_quality']
            for team in scheduler.team_capacity: scheduler.team_capacity[team] = result2['optimal_mechanics']
            for team in scheduler.quality_team_capacity: scheduler.quality_team_capacity[team] = result2['optimal_quality']
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
            scenario_results['scenario2'] = export_scenario_with_capacities(scheduler, 'scenario2')
            scenario_results['scenario2']['optimalMechanics'] = result2['optimal_mechanics']
            scenario_results['scenario2']['optimalQuality'] = result2['optimal_quality']
        print(f"✓ Scenario 2 complete: {scenario_results.get('scenario2', {}).get('makespan', 'N/A')} days makespan")

        # Scenario 3
        result3 = scheduler.scenario_3_simulated_annealing(target_earliness=-1, max_iterations=100, initial_temp=100, cooling_rate=0.95)
        if result3:
            for team, capacity in result3['config']['mechanic'].items(): scheduler.team_capacity[team] = capacity
            for team, capacity in result3['config']['quality'].items(): scheduler.quality_team_capacity[team] = capacity
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
            scenario_results['scenario3'] = export_scenario_with_capacities(scheduler, 'scenario3')
        print(f"✓ Scenario 3 complete: {scenario_results.get('scenario3', {}).get('makespan', 'N/A')} days makespan")

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

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500

    return app