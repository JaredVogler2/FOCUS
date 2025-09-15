# src/blueprints/scenarios.py

from flask import Blueprint, jsonify, current_app, request
from src.scheduler.scenarios import run_what_if_scenario
from src.server_utils import export_scenario_with_capacities
from datetime import datetime, timedelta
from src.scheduler import constraints


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

    # Make a copy to avoid modifying the cached results
    scenario_data = scenario_results[scenario_id].copy()

    # Get the scheduler instance from the app context
    scheduler = current_app.scheduler
    if scheduler:
        # Build and add the dependency maps
        predecessors_map, successors_map = constraints.get_dependency_maps(scheduler)
        scenario_data['predecessors_map'] = predecessors_map
        scenario_data['successors_map'] = successors_map

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
    using the pre-computed dynamic dependency maps.
    """
    scenario_data = current_app.scenario_results.get(scenario_id)
    if not scenario_data:
        return jsonify({'error': f'Scenario {scenario_id} not found'}), 404

    # Use the comprehensive dependency maps from the scenario data
    predecessors_map = scenario_data.get('predecessors_map', {})
    successors_map = scenario_data.get('successors_map', {})
    task_map = {t['taskId']: t for t in scenario_data.get('tasks', [])}

    target_task = task_map.get(task_id)
    if not target_task:
        # Fallback to searching all tasks if not in the dashboard's top 1000
        all_tasks_from_scheduler = current_app.scheduler.tasks
        if task_id in all_tasks_from_scheduler:
            target_task = all_tasks_from_scheduler[task_id]
            target_task['taskId'] = task_id # Ensure taskId is present
        else:
            return jsonify({'error': f'Task {task_id} not found in scenario {scenario_id}'}), 404


    def get_ordered_chain(start_node_id, graph, is_predecessor_chain):
        """
        Iteratively traverses the graph to build a simple, linear chain.
        Selects the first dependency at each step to simplify branching.
        """
        chain = []
        visited = {start_node_id}
        current_node_id = start_node_id

        # Limit chain length to prevent infinite loops from cycles
        for _ in range(len(task_map) + 50):
            next_nodes = graph.get(current_node_id, [])
            if not next_nodes:
                break

            # To keep the chain simple, we just follow the first dependency.
            next_node_id = next_nodes[0]

            if next_node_id in visited:
                # Cycle detected, break the loop
                break

            # The task might not be in the top 1000 tasks sent to the dashboard,
            # so we fetch its details from the main scheduler object as a fallback.
            task_info = task_map.get(next_node_id)
            if not task_info and hasattr(current_app, 'scheduler'):
                 # Create a minimal task object if not in dashboard list
                 raw_task = current_app.scheduler.tasks.get(next_node_id, {})
                 task_info = {
                     'taskId': next_node_id,
                     'type': raw_task.get('type', 'Unknown'),
                     'product': raw_task.get('product'),
                     'team': raw_task.get('team'),
                     'startTime': None # Not available for tasks outside dashboard view
                 }


            if task_info:
                chain.append(task_info)
                visited.add(next_node_id)
                current_node_id = next_node_id
            else:
                # Dependency points to a task that doesn't exist anywhere
                break

        if is_predecessor_chain:
            chain.reverse()

        return chain

    # Get chains using the correct maps
    upstream_chain = get_ordered_chain(task_id, predecessors_map, is_predecessor_chain=True)
    downstream_chain = get_ordered_chain(task_id, successors_map, is_predecessor_chain=False)

    # Filter chains to a directional 5-day window from the target task's start time
    if target_task.get('startTime'):
        try:
            target_start_time = datetime.fromisoformat(target_task['startTime'])
            time_window = timedelta(days=5)

            # Filter upstream chain: -5 days from target start
            filtered_upstream = []
            for task in upstream_chain:
                if task.get('startTime'):
                    task_start_time = datetime.fromisoformat(task['startTime'])
                    if target_start_time - time_window <= task_start_time < target_start_time:
                        filtered_upstream.append(task)
            upstream_chain = filtered_upstream

            # Filter downstream chain: +5 days from target start
            filtered_downstream = []
            for task in downstream_chain:
                if task.get('startTime'):
                    task_start_time = datetime.fromisoformat(task['startTime'])
                    if target_start_time < task_start_time <= target_start_time + time_window:
                        filtered_downstream.append(task)
            downstream_chain = filtered_downstream

        except (ValueError, TypeError):
            # If date parsing fails, fall back to unfiltered chains
            pass


    # Add the main task to both chains for context
    upstream_chain.append(target_task)
    downstream_chain.insert(0, target_task)

    def format_task_details(task_list):
        # The task objects are already in the correct format
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
        'product_line': target_task.get('product')
    })
