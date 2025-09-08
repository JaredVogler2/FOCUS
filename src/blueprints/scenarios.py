# src/blueprints/scenarios.py

from flask import Blueprint, jsonify, current_app, request
from src.scheduler.scenarios import run_what_if_scenario
from src.server_utils import export_scenario_with_capacities
from datetime import datetime

scenarios_bp = Blueprint('scenarios', __name__, url_prefix='/api')

@scenarios_bp.route('/scenarios')
def get_scenarios():
    """Get list of available scenarios with descriptions"""
    scheduler = current_app.scheduler
    return jsonify({
        'scenarios': [
            {
                'id': 'baseline',
                'name': 'Baseline',
                'description': 'Schedule with CSV-defined headcount using product-task instances'
            },
            {
                'id': 'scenario1',
                'name': 'Scenario 1: CSV Headcount',
                'description': 'Schedule with CSV-defined team capacities'
            },
            {
                'id': 'scenario2',
                'name': 'Scenario 2: Minimize Makespan',
                'description': 'Find uniform headcount for shortest schedule'
            },
            {
                'id': 'scenario3',
                'name': 'Scenario 3: Multi-Dimensional',
                'description': 'Optimize per-team capacity using simulated annealing to achieve target delivery (1 day early)'
            }
        ],
        'architecture': 'Product-Task Instances with Customer Inspections',
        'totalInstances': len(scheduler.tasks) if scheduler else 0,
        'inspectionLayers': {
            'quality': len(scheduler.quality_team_capacity) if scheduler else 0,
            'customer': len(scheduler.customer_team_capacity) if scheduler else 0
        }
    })

@scenarios_bp.route('/scenario_progress/<scenario_id>')
def get_scenario_progress(scenario_id):
    # This logic for progress tracking might need to be re-evaluated
    # as computation_progress is not defined here.
    # For now, returning a placeholder.
    computation_progress = {}
    return jsonify({
        'progress': computation_progress.get(scenario_id, 0),
        'status': 'computing' if scenario_id in computation_progress else 'idle'
    })

@scenarios_bp.route('/scenario/<scenario_id>')
def get_scenario_data(scenario_id):
    scenario_results = current_app.scenario_results
    if scenario_id not in scenario_results:
        return jsonify({'error': f'Scenario {scenario_id} not found'}), 404
    scenario_data = scenario_results[scenario_id]
    return jsonify(scenario_data)

@scenarios_bp.route('/scenario/<scenario_id>/summary')
def get_scenario_summary(scenario_id):
    """Get summary statistics for a scenario"""
    scenario_results = current_app.scenario_results
    if scenario_id not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    data = scenario_results[scenario_id]

    product_summaries = []
    for product in data.get('products', []):
        product_summaries.append({
            'name': product['name'],
            'status': 'On Time' if product['onTime'] else f"Late by {product['latenessDays']} days",
            'taskRange': product.get('taskRange', 'Unknown'),
            'remainingCount': product.get('remainingCount', 0),
            'totalTasks': product['totalTasks'],
            'taskBreakdown': product.get('taskBreakdown', {})
        })

    summary = {
        'scenarioName': data['scenarioId'],
        'totalWorkforce': data['totalWorkforce'],
        'makespan': data['makespan'],
        'onTimeRate': data['onTimeRate'],
        'avgUtilization': data['avgUtilization'],
        'maxLateness': data.get('maxLateness', 0),
        'totalLateness': data.get('totalLateness', 0),
        'achievedMaxLateness': data.get('achievedMaxLateness', data.get('maxLateness', 0)),
        'totalTaskInstances': data.get('totalTaskInstances', 0),
        'scheduledTaskInstances': data.get('scheduledTaskInstances', 0),
        'taskTypeSummary': data.get('taskTypeSummary', {}),
        'productSummaries': product_summaries,
        'instanceBased': True
    }

    return jsonify(summary)


@scenarios_bp.route('/scenarios/run_what_if', methods=['POST'])
def run_what_if():
    """Run a what-if scenario by prioritizing a specific product."""
    data = request.get_json()
    product_to_prioritize = data.get('product_to_prioritize')

    if not product_to_prioritize:
        return jsonify({'error': 'product_to_prioritize is required'}), 400

    scheduler = current_app.scheduler
    if not scheduler:
        return jsonify({'error': 'Scheduler not initialized'}), 500

    # Run the what-if scenario
    what_if_scheduler = run_what_if_scenario(scheduler, product_to_prioritize)

    if not what_if_scheduler:
        return jsonify({'error': 'Failed to run what-if scenario.'}), 500

    # Export the results of the new scenario
    what_if_results = export_scenario_with_capacities(what_if_scheduler, f"what_if_{product_to_prioritize}")

    # Get the baseline results for comparison
    baseline_results = current_app.scenario_results.get('baseline')

    # Structure for side-by-side comparison
    comparison_data = {
        'baseline': baseline_results,
        'what_if': what_if_results,
        'prioritized_product': product_to_prioritize,
        'created_at': datetime.utcnow().isoformat()
    }

    # Save the scenario
    scenario_id = f"whatif_{product_to_prioritize}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    current_app.saved_scenarios[scenario_id] = comparison_data

    return jsonify(comparison_data)


@scenarios_bp.route('/products')
def get_products():
    """Get a list of all unique product lines for scenario planning."""
    if scheduler := current_app.scheduler:
        # TODO: Investigate why product_remaining_ranges is not populated correctly at runtime.
        # Using delivery_dates as a temporary fix to populate the product dropdown.
        # The original intent was to use product_remaining_ranges to include products
        # that might not have delivery dates but do have jobs.
        product_list = list(scheduler.delivery_dates.keys())
        return jsonify(sorted(product_list))
    return jsonify([])


@scenarios_bp.route('/scenarios/saved')
def get_saved_scenarios():
    """Get a list of all saved what-if scenarios."""
    return jsonify(current_app.saved_scenarios)
