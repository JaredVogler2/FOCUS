# src/blueprints/industrial_engineering.py

from flask import Blueprint, jsonify, request, current_app
from datetime import datetime

ie_bp = Blueprint('industrial_engineering', __name__, url_prefix='/api/ie')

def initialize_ie_queue(app):
    """Initializes the IE review queue on the app context."""
    with app.app_context():
        if not hasattr(app, 'industrial_engineering_review_queue'):
            app.industrial_engineering_review_queue = []

@ie_bp.route('/flag_task', methods=['POST'])
def flag_task_for_review():
    """
    Flags a task for review by the Industrial Engineering team.
    This is called when a mechanic saves feedback with the 'predecessor' reason.
    """
    if not hasattr(current_app, 'industrial_engineering_review_queue'):
        initialize_ie_queue(current_app)

    data = request.json
    task_id = data.get('taskId')
    priority = data.get('priority', 999)
    scenario = data.get('scenario', 'baseline')
    predecessor_task = data.get('predecessorTask', '')
    notes = data.get('notes', '') # Get notes from the request

    if not task_id:
        return jsonify({'success': False, 'error': 'Task ID is required'}), 400

    # Prevent duplicate entries but return a success message
    if any(item['task_id'] == task_id for item in current_app.industrial_engineering_review_queue):
        return jsonify({'success': True, 'message': f'Task {task_id} is already in the review queue.'}), 200

    # Get additional task details from the scheduler
    task_details = {}
    # Find the task in the correct scenario's data
    scenario_data = current_app.scenario_results.get(scenario, {})
    task_info = next((task for task in scenario_data.get('tasks', []) if task['taskId'] == task_id), None)

    if task_info:
        task_details = {
            'product': task_info.get('product'),
            'team': task_info.get('team'),
            'duration': task_info.get('duration'),
        }

    review_item = {
        'task_id': task_id,
        'priority': priority,
        'reason': 'Held by Predecessor Task',
        'predecessor_task': predecessor_task,
        'notes': notes,  # Add notes to the review item
        'scenario': scenario,
        'flagged_at': datetime.utcnow().isoformat(),
        'status': 'open',
        'details': task_details
    }

    current_app.industrial_engineering_review_queue.append(review_item)

    return jsonify({
        'success': True,
        'message': f'Task {task_id} successfully flagged for IE review.',
        'review_item': review_item
    }), 201

@ie_bp.route('/review_queue', methods=['GET'])
def get_review_queue():
    """Returns the current list of tasks awaiting IE review."""
    initialize_ie_queue(current_app)

    # Sort by priority (lower is higher priority)
    sorted_queue = sorted(
        current_app.industrial_engineering_review_queue,
        key=lambda x: (x.get('priority', 999) if isinstance(x.get('priority'), (int, float)) else 999)
    )
    return jsonify(sorted_queue)

@ie_bp.route('/resolve_task/<task_id>', methods=['POST'])
def resolve_task(task_id):
    """Resolves a task from the IE review queue."""
    if not hasattr(current_app, 'industrial_engineering_review_queue'):
        return jsonify({'error': 'Review queue not found'}), 404

    queue = current_app.industrial_engineering_review_queue
    task_found = False
    for i, item in enumerate(queue):
        if item['task_id'] == task_id:
            del queue[i]
            task_found = True
            break

    if task_found:
        return jsonify({'success': True, 'message': f'Task {task_id} resolved and removed from the review queue.'})
    else:
        return jsonify({'error': f'Task {task_id} not found in the review queue.'}), 404
