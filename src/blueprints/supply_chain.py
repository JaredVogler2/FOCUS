# src/blueprints/supply_chain.py

from flask import Blueprint, jsonify, current_app
from collections import defaultdict

supply_chain_bp = Blueprint('supply_chain', __name__, url_prefix='/api/supply_chain')

@supply_chain_bp.route('/late_parts_analysis')
def get_late_parts_analysis():
    """
    Provides a detailed analysis of late parts, their scheduled times,
    and the tasks that depend on them, including a full impact analysis.
    """
    scheduler = current_app.scheduler
    if not scheduler or not scheduler.task_schedule:
        return jsonify({'error': 'Scheduler not initialized or no schedule found'}), 500

    late_parts_data = []

    for task_id, is_late in scheduler.late_part_tasks.items():
        if not is_late:
            continue

        schedule_info = scheduler.task_schedule.get(task_id)
        task_info = scheduler.tasks.get(task_id, {})
        original_task_id = scheduler.instance_to_original_task.get(task_id, task_id)
        on_dock_date = scheduler.on_dock_dates.get(original_task_id)

        # New: Get all successors for impact analysis
        all_downstream_tasks = scheduler.get_all_successors(task_id)
        affected_task_count = len(all_downstream_tasks)

        # Calculate the total duration of all affected downstream tasks
        total_downstream_duration = 0
        for downstream_task_id in all_downstream_tasks:
            downstream_task_info = scheduler.tasks.get(downstream_task_id, {})
            total_downstream_duration += downstream_task_info.get('duration', 0)

        # Convert duration from minutes to hours for readability
        total_downstream_duration_hours = total_downstream_duration / 60

        # The impact score can be a combination of the number of tasks and their total duration
        # This is a simple example; a more complex formula could be used.
        impact_score = affected_task_count * total_downstream_duration_hours

        late_parts_data.append({
            'part_id': task_id,
            'product': task_info.get('product', 'N/A'),
            'on_dock_date': on_dock_date.isoformat() if on_dock_date else None,
            'scheduled_start': schedule_info['start_time'].isoformat() if schedule_info else None,
            'duration': task_info.get('duration'),
            'team': task_info.get('team'),
            'dependent_tasks': scheduler.get_successors(task_id),  # Immediate successors
            'affected_task_count': affected_task_count,
            'total_downstream_duration_hours': round(total_downstream_duration_hours, 2),
            'impact_score': round(impact_score, 2)
        })

    # Sort by impact score in descending order to show the most critical parts first
    late_parts_data.sort(key=lambda x: x['impact_score'], reverse=True)

    return jsonify(late_parts_data)
