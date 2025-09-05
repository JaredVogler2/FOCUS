# src/blueprints/scenarios.py

from flask import Blueprint, jsonify, current_app

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
