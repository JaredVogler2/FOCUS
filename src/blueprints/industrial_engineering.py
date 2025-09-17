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
    """
    Resolves a task or a single predecessor from the IE review queue.
    If predecessor_task and predecessor_notes are provided, only that predecessor is removed.
    If the list of predecessors becomes empty, the entire task item is removed from the queue.
    If no predecessor details are provided, the entire task item is removed (legacy behavior).
    """
    data = request.json
    item_id = data.get('flagged_at')
    pred_task = data.get('predecessor_task')
    pred_notes = data.get('predecessor_notes')

    if not item_id:
        return jsonify({'success': False, 'error': 'A unique item ID (flagged_at) is required.'}), 400

    queue = read_queue()
    task_found = False
    new_queue = []
    resolved_task_id = "Unknown"
    message = ""

    for item in queue:
        # Find the correct flagged task by its unique timestamp ID
        if item.get('feedback_id') == item_id or item.get('flagged_at') == item_id:
            task_found = True
            resolved_task_id = item.get('task_id', 'Unknown')

            # If predecessor details are provided, we're resolving a single predecessor
            is_predecessor_resolution = pred_task is not None and pred_notes is not None

            if is_predecessor_resolution and 'predecessors' in item and isinstance(item['predecessors'], list):
                # Filter out the resolved predecessor
                original_predecessor_count = len(item['predecessors'])
                item['predecessors'] = [
                    p for p in item['predecessors']
                    if not (p.get('predecessorTask') == pred_task and p.get('notes') == pred_notes)
                ]

                if len(item['predecessors']) < original_predecessor_count:
                    current_app.logger.info(f"Resolved predecessor '{pred_task}' for task {resolved_task_id}.")
                    message = f"Predecessor '{pred_task}' for task {resolved_task_id} resolved."
                else:
                    current_app.logger.warning(f"Could not find predecessor '{pred_task}' to resolve for task {resolved_task_id}.")
                    # Still append the item back to the queue
                    new_queue.append(item)
                    continue

                # If there are still other predecessors, keep the item in the queue.
                # Otherwise, it will be fully removed by not appending it to new_queue.
                if item['predecessors']:
                    new_queue.append(item)
                else:
                    current_app.logger.info(f"All predecessors for task {resolved_task_id} resolved. Removing item from queue.")
                    message = f"Final predecessor for task {resolved_task_id} resolved. Task removed from queue."

            else:
                # This block handles two cases:
                # 1. Legacy behavior: No predecessor info sent, so resolve the whole item.
                # 2. Non-predecessor delays: These don't have a `predecessors` list to begin with.
                current_app.logger.info(f"Resolved entire IE Queue item for task {resolved_task_id} with ID {item_id}")
                message = f"Task {resolved_task_id} resolved and removed from the review queue."
                # By not appending the item to new_queue, it gets removed.
        else:
            # This item is not the one we're looking for, so keep it.
            new_queue.append(item)

    if task_found:
        write_queue(new_queue)
        return jsonify({'success': True, 'message': message})
    else:
        current_app.logger.error(f"Could not find IE Queue item with ID {item_id} to resolve.")
        return jsonify({'success': False, 'error': f'Task with ID {item_id} not found in the review queue.'}), 404
