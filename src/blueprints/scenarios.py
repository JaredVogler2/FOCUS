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
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON body'}), 400
        product_to_prioritize = data.get('product_to_prioritize')

        if not product_to_prioritize:
            return jsonify({'error': 'product_to_prioritize is required'}), 400

        scheduler = current_app.scheduler
        if not scheduler:
            return jsonify({'error': 'Scheduler not initialized'}), 500

        # Run the what-if scenario
        what_if_scheduler = run_what_if_scenario(scheduler, product_to_prioritize)

        if not what_if_scheduler:
            return jsonify({'error': 'Failed to run what-if scenario. The solver might not have found a feasible solution.'}), 500

        # Export the results of the new scenario
        what_if_results = export_scenario_with_capacities(what_if_scheduler, f"what_if_{product_to_prioritize}")

        # Get the baseline results for comparison
        baseline_scenario_id = data.get('baseline_scenario_id', 'baseline')
        baseline_results = current_app.scenario_results.get(baseline_scenario_id)

        if not baseline_results:
            return jsonify({'error': f'Baseline scenario "{baseline_scenario_id}" not found.'}), 404

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
    except Exception as e:
        # Log the full exception for debugging
        current_app.logger.error(f"Error in /run_what_if: {e}", exc_info=True)
        # Return a generic but informative JSON error to the frontend
        return jsonify({'error': f'An unexpected server error occurred: {str(e)}'}), 500


@scenarios_bp.route('/products')
def get_products():
    """Get a list of all unique product lines for scenario planning."""
    # The scheduler object can be unreliable with the dev server's reloader.
    # Instead, we source the product list from the 'baseline' scenario results,
    # which are computed and stored at startup. This ensures consistency with other
    # dashboard views.
    if 'baseline' in current_app.scenario_results:
        baseline_results = current_app.scenario_results['baseline']
        if 'products' in baseline_results and baseline_results['products']:
            # Extract unique product names from the list of product objects
            product_names = sorted(list(set(p['name'] for p in baseline_results['products'])))
            return jsonify(product_names)

    # Fallback if baseline or products are not available
    return jsonify([])


@scenarios_bp.route('/scenarios/saved')
def get_saved_scenarios():
    """Get a list of all saved what-if scenarios."""
    return jsonify(current_app.saved_scenarios)


@scenarios_bp.route('/task/<scenario_id>/<task_id>/chain')
def get_task_chain(scenario_id, task_id):
    """
    Get the full upstream (predecessor) and downstream (successor) chain for a given task,
    respecting product-specific networks. Returns a simple, ordered chain.
    """
    scenario_results = current_app.scenario_results
    if scenario_id not in scenario_results:
        return jsonify({'error': f'Scenario {scenario_id} not found'}), 404

    all_tasks = scenario_results[scenario_id].get('tasks', [])
    task_map = {t['taskId']: t for t in all_tasks}

    target_task = task_map.get(task_id)
    if not target_task:
        return jsonify({'error': f'Task {task_id} not found in scenario {scenario_id}'}), 404

    product_line = target_task.get('product')
    if not product_line:
        return jsonify({'error': f'Task {task_id} does not have a product line specified.'}), 400

    # Filter tasks to only include those from the same product line
    product_tasks = [t for t in all_tasks if t.get('product') == product_line]
    product_task_map = {t['taskId']: t for t in product_tasks}

    # Build predecessor and successor graphs from product-specific tasks
    predecessors_graph = {t['taskId']: t.get('dependencies', []) for t in product_tasks}
    successors_graph = {t['taskId']: [] for t in product_tasks}
    for task, deps in predecessors_graph.items():
        for dep in deps:
            if dep in successors_graph:
                successors_graph[dep].append(task)

    def get_ordered_chain(start_node_id, graph, is_predecessor_chain):
        """
        Iteratively traverses the graph to build a simple, linear chain.
        Selects the first dependency at each step to simplify branching.
        """
        chain = []
        visited = {start_node_id}
        current_node_id = start_node_id

        for _ in range(len(product_tasks)):  # Safety break for cycles
            next_nodes = graph.get(current_node_id, [])
            if not next_nodes:
                break

            # To keep the chain simple, we just follow the first dependency.
            # In a real-world scenario, you might want to handle branches differently.
            next_node_id = next_nodes[0]

            if next_node_id in visited:
                # Cycle detected
                break

            task_info = product_task_map.get(next_node_id)
            if task_info:
                chain.append(task_info)
                visited.add(next_node_id)
                current_node_id = next_node_id
            else:
                # Dependency points to a task not in the product line
                break

        # For predecessors, we want the chain going "back in time" from the target
        if is_predecessor_chain:
            chain.reverse()

        return chain

    # Get chains
    upstream_chain = get_ordered_chain(task_id, predecessors_graph, is_predecessor_chain=True)
    downstream_chain = get_ordered_chain(task_id, successors_graph, is_predecessor_chain=False)

    # Add the main task to both chains for context
    upstream_chain.append(target_task)
    downstream_chain.insert(0, target_task)

    def format_task_details(task_list):
        return [
            {
                'taskId': t.get('taskId'),
                'type': t.get('type', 'Unknown'),
                'product': t.get('product', 'Unknown'),
                'team': t.get('team', 'Unknown'),
                'startTime': t.get('startTime')
            } for t in task_list
        ]

    return jsonify({
        'task_id': task_id,
        'predecessors': format_task_details(upstream_chain),
        'successors': format_task_details(downstream_chain),
        'product_line': product_line
    })
