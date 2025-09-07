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
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
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

        # Scenario 3
        result3 = scheduler.scenario_3_optimal_schedule()
        if result3:
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

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500

    return app


def export_scenario_with_capacities(scheduler, scenario_name):
    """Export scenario results including current team capacities and shift information"""
    team_capacities = {**scheduler.team_capacity, **scheduler.quality_team_capacity, **scheduler.customer_team_capacity}
    team_shifts = {**scheduler.team_shifts, **scheduler.quality_team_shifts, **scheduler.customer_team_shifts}

    tasks = []
    MAX_TASKS_FOR_DASHBOARD = 1000
    total_tasks_available = len(scheduler.global_priority_list) if hasattr(scheduler, 'global_priority_list') else len(scheduler.task_schedule)

    if hasattr(scheduler, 'global_priority_list') and scheduler.global_priority_list:
        sorted_priority_items = sorted(scheduler.global_priority_list, key=lambda x: x.get('global_priority', 999))[:MAX_TASKS_FOR_DASHBOARD]
        for item in sorted_priority_items:
            task_id = item.get('task_instance_id')
            if task_id in scheduler.task_schedule:
                schedule = scheduler.task_schedule[task_id]
                task_info = scheduler.tasks.get(task_id, {})
                tasks.append({
                    'taskId': task_id,
                    'type': item.get('task_type', 'Production'),
                    'product': item.get('product_line', 'Unknown'),
                    'team': schedule.get('team', ''),
                    'teamSkill': schedule.get('team_skill', ''),
                    'skill': schedule.get('skill', ''),
                    'startTime': schedule['start_time'].isoformat(),
                    'endTime': schedule['end_time'].isoformat(),
                    'duration': schedule.get('duration', 60),
                    'mechanics': schedule.get('mechanics_required', 1),
                    'shift': schedule.get('shift', '1st'),
                    'priority': item.get('global_priority', 999),
                    'isLatePartTask': task_id in scheduler.late_part_tasks,
                    'isReworkTask': task_id in scheduler.rework_tasks,
                    'isQualityTask': schedule.get('is_quality', False),
                    'isCustomerTask': schedule.get('is_customer', False),
                    'isCritical': item.get('slack_hours', 999) < 24,
                    'slackHours': item.get('slack_hours', 999)
                })

    makespan = scheduler.calculate_makespan()
    lateness_metrics = scheduler.calculate_lateness_metrics()

    team_task_minutes = defaultdict(int)
    for task_id, schedule in scheduler.task_schedule.items():
        team_for_util = schedule.get('team_skill', schedule.get('team'))
        if team_for_util:
            team_task_minutes[team_for_util] += schedule.get('duration', 0) * schedule.get('mechanics_required', 1)

    utilization = {team: min(100, round((team_task_minutes.get(team, 0) / (8 * 60 * makespan * capacity) * 100), 1)) if capacity > 0 and makespan > 0 else 0 for team, capacity in team_capacities.items()}

    products = []
    for product, metrics in lateness_metrics.items():
        products.append({
            'name': product,
            'totalTasks': metrics['total_tasks'],
            'deliveryDate': metrics['delivery_date'].isoformat(),
            'projectedCompletion': metrics['projected_completion'].isoformat() if metrics['projected_completion'] else '',
            'onTime': metrics['on_time'],
            'latenessDays': metrics['lateness_days'] if metrics['lateness_days'] < 999999 else 0,
        })

    on_time_rate = round(sum(1 for p in products if p['onTime']) / len(products) * 100 if products else 0, 1)
    max_lateness = max((p['latenessDays'] for p in products if p['latenessDays'] < 999999), default=0)
    total_workforce = sum(team_capacities.values())

    return {
        'scenarioId': scenario_name,
        'tasks': tasks,
        'teamCapacities': team_capacities,
        'teamShifts': team_shifts,
        'products': products,
        'utilization': utilization,
        'totalWorkforce': total_workforce,
        'avgUtilization': round(sum(utilization.values()) / len(utilization) if utilization else 0, 1),
        'makespan': makespan,
        'onTimeRate': on_time_rate,
        'maxLateness': max_lateness,
        'totalTasks': total_tasks_available,
        'displayedTasks': len(tasks),
        'truncated': total_tasks_available > MAX_TASKS_FOR_DASHBOARD
    }