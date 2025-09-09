# src/blueprints/industrial_engineering.py

from flask import Blueprint, jsonify, request, current_app
from datetime import datetime
import os
import json

ie_bp = Blueprint('industrial_engineering', __name__, url_prefix='/api/ie')

IE_QUEUE_FILE = 'ie_review_queue.json'

def read_queue():
    """
    Reads the review queue from the JSON file.
    Handles file not found and JSON decoding errors.
    """
    if not os.path.exists(IE_QUEUE_FILE):
        return []
    try:
        with open(IE_QUEUE_FILE, 'r') as f:
            content = f.read()
            if not content:
                return []
            return json.loads(content)
    except (IOError, json.JSONDecodeError):
        # If file is unreadable or corrupt, treat as empty
        return []

def write_queue(data):
    """Writes the review queue to the JSON file."""
    try:
        with open(IE_QUEUE_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except IOError:
        # In a real app, you might want more robust error handling here
        pass

@ie_bp.route('/flag_task', methods=['POST'])
def flag_task_for_review():
    """
    Flags a task for review by the Industrial Engineering team.
    This now handles a consolidated payload which may contain multiple predecessors.
    """
    data = request.json
    task_id = data.get('taskId')

    if not task_id:
        return jsonify({'success': False, 'error': 'Task ID is required'}), 400

    # Extract all data from the consolidated payload
    priority = data.get('priority', 999)
    scenario = data.get('scenario', 'baseline')
    reason = data.get('reason', 'Unknown')
    general_notes = data.get('generalNotes', '')
    predecessors = data.get('predecessors', [])
    mechanic_name = data.get('mechanicName', 'Unknown')
    delay_minutes = data.get('delayMinutes', 0)

    queue = read_queue()

    # Create a unique ID for this feedback submission
    flagged_at = datetime.utcnow().isoformat()

    # Get additional task details from the main data source
    task_details = {}
    scenario_data = current_app.scenario_results.get(scenario, {})
    task_info = next((task for task in scenario_data.get('tasks', []) if task['taskId'] == task_id), None)
    if task_info:
        task_details = {
            'product': task_info.get('product'),
            'team': task_info.get('team'),
            'duration': task_info.get('duration'),
        }

    review_item = {
        'feedback_id': flagged_at, # Use the timestamp as a unique ID for the feedback
        'task_id': task_id,
        'priority': priority,
        'reason': reason,
        'general_notes': general_notes,
        'predecessors': predecessors,
        'delay_minutes': delay_minutes,
        'scenario': scenario,
        'flagged_at': flagged_at,
        'status': 'open',
        'details': task_details,
        'mechanic_name': mechanic_name
    }

    # Simple check to prevent exact duplicate submissions in a short time frame
    # In a real application, this could be more robust.
    if any(item.get('task_id') == task_id and item.get('mechanic_name') == mechanic_name and item.get('reason') == reason for item in queue):
        current_app.logger.info(f"Duplicate-like feedback for task {task_id} by {mechanic_name}")

    queue.append(review_item)
    write_queue(queue)

    return jsonify({
        'success': True,
        'message': f'Task {task_id} successfully flagged for IE review.',
        'review_item': review_item
    }), 201

@ie_bp.route('/review_queue', methods=['GET'])
def get_review_queue():
    """Returns the current list of tasks awaiting IE review from the file."""
    queue = read_queue()
    # Sort by priority (lower is higher priority)
    sorted_queue = sorted(
        queue,
        key=lambda x: (x.get('priority', 999) if isinstance(x.get('priority'), (int, float)) else 999)
    )
    return jsonify(sorted_queue)

@ie_bp.route('/resolve_task', methods=['POST'])
def resolve_task():
    """Resolves a task from the file-based IE review queue using its unique timestamp."""
    data = request.json
    item_id = data.get('flagged_at')

    if not item_id:
        return jsonify({'success': False, 'error': 'A unique item ID (flagged_at) is required.'}), 400

    queue = read_queue()
    task_found = False
    new_queue = []
    resolved_task_id = None
    for item in queue:
        if item.get('feedback_id') == item_id:
            task_found = True
            resolved_task_id = item.get('task_id', 'Unknown')
        else:
            new_queue.append(item)

    if task_found:
        write_queue(new_queue)
        return jsonify({'success': True, 'message': f'Task {resolved_task_id} ({item_id}) resolved and removed from the review queue.'})
    else:
        return jsonify({'success': False, 'error': f'Task with ID {item_id} not found in the review queue.'}), 404
