# src/blueprints/supply_chain.py

from flask import Blueprint, jsonify, current_app
from collections import defaultdict

supply_chain_bp = Blueprint('supply_chain', __name__, url_prefix='/api/supply_chain')

@supply_chain_bp.route('/late_parts_analysis')
def get_late_parts_analysis():
    """
    Provides a detailed analysis of late parts, their scheduled times,
    and the tasks that depend on them.
    """
    scheduler = current_app.scheduler
    if not scheduler or not scheduler.task_schedule:
        return jsonify({'error': 'Scheduler not initialized or no schedule found'}), 500

    late_parts_data = []

    # Create a mapping from a task to the tasks that depend on it
    successors = defaultdict(list)
    for const in scheduler.build_dynamic_dependencies():
        successors[const['First']].append(const['Second'])

    for task_id, is_late in scheduler.late_part_tasks.items():
        if not is_late:
            continue

        schedule_info = scheduler.task_schedule.get(task_id)
        task_info = scheduler.tasks.get(task_id, {})
        original_task_id = scheduler.instance_to_original_task.get(task_id, task_id)
        on_dock_date = scheduler.on_dock_dates.get(original_task_id)

        # Find the next task in the chain that this late part feeds into
        dependent_tasks = successors.get(task_id, [])

        late_parts_data.append({
            'part_id': task_id,
            'product': task_info.get('product', 'N/A'),
            'on_dock_date': on_dock_date.isoformat() if on_dock_date else None,
            'scheduled_start': schedule_info['start_time'].isoformat() if schedule_info else None,
            'duration': task_info.get('duration'),
            'team': task_info.get('team'),
            'dependent_tasks': dependent_tasks
        })

    # Sort by on-dock date
    late_parts_data.sort(key=lambda x: x['on_dock_date'] or '9999-12-31')

    return jsonify(late_parts_data)
