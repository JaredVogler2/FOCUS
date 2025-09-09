# src/blueprints/assignments.py

from flask import Blueprint, jsonify, request, current_app
from datetime import datetime

assignments_bp = Blueprint('assignments', __name__, url_prefix='/api')

@assignments_bp.route('/debug/tasks')
def debug_tasks():
    """Debug endpoint to see task team assignments"""
    scenario = request.args.get('scenario', 'baseline')
    scenario_results = current_app.scenario_results

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    tasks = scenario_results[scenario]['tasks'][:20]  # First 20 tasks

    task_info = []
    for task in tasks:
        task_info.append({
            'taskId': task['taskId'],
            'type': task['type'],
            'team': task.get('team', 'NO TEAM'),
            'teamSkill': task.get('teamSkill', 'NO TEAM_SKILL'),
            'skill': task.get('skill', 'NO SKILL'),
            'product': task['product']
        })

    return jsonify({
        'scenario': scenario,
        'taskCount': len(scenario_results[scenario]['tasks']),
        'sampleTasks': task_info,
        'teamCapacities': list(scenario_results[scenario]['teamCapacities'].keys())[:10]
    })


@assignments_bp.route('/auto_assign', methods=['POST'])
def auto_assign_tasks():
    """Auto-assign tasks to mechanics avoiding conflicts"""
    mechanic_assignments = current_app.mechanic_assignments
    scenario_results = current_app.scenario_results

    data = request.json
    scenario_id = data.get('scenario', 'baseline')
    team_filter = data.get('team', 'all')

    if scenario_id not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    # Initialize assignments for this scenario if not exists
    if scenario_id not in mechanic_assignments:
        mechanic_assignments[scenario_id] = {}

    scenario_data = scenario_results[scenario_id]
    team_capacities = scenario_data.get('teamCapacities', {})

    # Build list of available mechanics based on team filter
    available_mechanics = []
    mechanic_id = 1

    for team, capacity in sorted(team_capacities.items()):
        # Filter based on team selection
        if team_filter == 'all' or \
                (
                        team_filter == 'all-mechanics' and 'Mechanic' in team and 'Quality' not in team and 'Customer' not in team) or \
                (team_filter == 'all-quality' and 'Quality' in team) or \
                (team_filter == 'all-customer' and 'Customer' in team) or \
                team_filter == team:

            is_quality = 'Quality' in team
            is_customer = 'Customer' in team

            # Determine role name based on team type
            if is_customer:
                role_name = 'Customer'  # Customer inspectors
                id_prefix = 'cust'
            elif is_quality:
                role_name = 'QC'  # Quality control
                id_prefix = 'qual'
            else:
                role_name = 'Mechanic'
                id_prefix = 'mech'

            for i in range(capacity):
                mechanic_info = {
                    'id': f"{id_prefix}_{mechanic_id}",
                    'name': f"{role_name} {mechanic_id}",
                    'team': team,
                    'busy_until': None,  # Track when mechanic becomes available
                    'assigned_tasks': [],
                    'is_quality': is_quality,
                    'is_customer': is_customer
                }
                available_mechanics.append(mechanic_info)
                mechanic_id += 1

    # Get tasks to assign (filtered by team)
    tasks_to_assign = []
    for task in scenario_data.get('tasks', []):
        # Check team filter matches
        task_team = task.get('team', '')
        include_task = False

        if team_filter == 'all':
            include_task = True
        elif team_filter == 'all-mechanics':
            include_task = ('Mechanic' in task_team and
                            'Quality' not in task_team and
                            'Customer' not in task_team)
        elif team_filter == 'all-quality':
            include_task = 'Quality' in task_team
        elif team_filter == 'all-customer':
            include_task = 'Customer' in task_team
        elif task_team == team_filter:
            include_task = True

        if include_task:
            tasks_to_assign.append(task)

    # Sort tasks by start time and priority
    tasks_to_assign.sort(key=lambda x: (x['startTime'], x.get('priority', 999)))

    # Track assignments
    assignments = []
    conflicts = []

    for task in tasks_to_assign[:100]:  # Limit to first 100 tasks for performance
        task_start = datetime.fromisoformat(task['startTime'])
        task_end = datetime.fromisoformat(task['endTime'])
        mechanics_needed = task.get('mechanics', 1)

        # Find available mechanics from the same team as the task
        team_mechanics = [m for m in available_mechanics if m['team'] == task['team']]

        # Find mechanics who are free at task start time
        free_mechanics = []
        for mechanic in team_mechanics:
            if mechanic['busy_until'] is None or mechanic['busy_until'] <= task_start:
                free_mechanics.append(mechanic)

        if len(free_mechanics) >= mechanics_needed:
            # Assign the required number of mechanics
            assigned_mechs = free_mechanics[:mechanics_needed]
            assigned_names = []

            for mech in assigned_mechs:
                # Update mechanic's busy time
                mech['busy_until'] = task_end
                mech['assigned_tasks'].append({
                    'taskId': task['taskId'],
                    'startTime': task['startTime'],
                    'endTime': task['endTime'],
                    'duration': task['duration'],
                    'type': task['type'],
                    'product': task['product'],
                    'isQualityTask': task.get('isQualityTask', False),
                    'isCustomerTask': task.get('isCustomerTask', False)
                })
                assigned_names.append(mech['id'])

                # Store in global assignments
                if mech['id'] not in mechanic_assignments[scenario_id]:
                    mechanic_assignments[scenario_id][mech['id']] = []

                mechanic_assignments[scenario_id][mech['id']].append({
                    'taskId': task['taskId'],
                    'taskType': task['type'],
                    'product': task['product'],
                    'startTime': task['startTime'],
                    'endTime': task['endTime'],
                    'duration': task['duration'],
                    'team': task['team'],
                    'shift': task.get('shift', '1st'),
                    'isQualityTask': task.get('isQualityTask', False),
                    'isCustomerTask': task.get('isCustomerTask', False)
                })

            assignments.append({
                'taskId': task['taskId'],
                'mechanics': assigned_names,
                'startTime': task['startTime'],
                'conflict': False,
                'taskType': task['type'],
                'team': task['team']
            })
        else:
            # Record conflict - not enough free mechanics
            conflicts.append({
                'taskId': task['taskId'],
                'reason': f'Need {mechanics_needed} {task["team"]} personnel but only {len(free_mechanics)} available',
                'startTime': task['startTime'],
                'team': task['team'],
                'available': len(free_mechanics),
                'needed': mechanics_needed
            })

            # Try to assign whatever mechanics are available (partial assignment)
            if free_mechanics:
                assigned_names = []
                for mech in free_mechanics:
                    mech['busy_until'] = task_end
                    mech['assigned_tasks'].append({
                        'taskId': task['taskId'],
                        'conflict': True,
                        'partial': True
                    })
                    assigned_names.append(mech['id'])

                    if mech['id'] not in mechanic_assignments[scenario_id]:
                        mechanic_assignments[scenario_id][mech['id']] = []

                    mechanic_assignments[scenario_id][mech['id']].append({
                        'taskId': task['taskId'],
                        'taskType': task['type'],
                        'product': task['product'],
                        'startTime': task['startTime'],
                        'endTime': task['endTime'],
                        'duration': task['duration'],
                        'team': task['team'],
                        'shift': task.get('shift', '1st'),
                        'partial': True,
                        'isQualityTask': task.get('isQualityTask', False),
                        'isCustomerTask': task.get('isCustomerTask', False)
                    })

                assignments.append({
                    'taskId': task['taskId'],
                    'mechanics': assigned_names,
                    'startTime': task['startTime'],
                    'conflict': True,
                    'partial': True,
                    'taskType': task['type'],
                    'team': task['team']
                })

    # Calculate statistics
    total_assigned = len([a for a in assignments if not a.get('conflict', False)])
    partial_assigned = len([a for a in assignments if a.get('partial', False)])
    total_conflicts = len(conflicts)

    # Build mechanic summary
    mechanic_summary = []
    for mech in available_mechanics:
        if mech['assigned_tasks']:
            # Count different task types
            quality_tasks = sum(1 for t in mech['assigned_tasks'] if t.get('isQualityTask', False))
            customer_tasks = sum(1 for t in mech['assigned_tasks'] if t.get('isCustomerTask', False))
            regular_tasks = len(mech['assigned_tasks']) - quality_tasks - customer_tasks

            mechanic_summary.append({
                'id': mech['id'],
                'name': mech['name'],
                'team': mech['team'],
                'tasksAssigned': len(mech['assigned_tasks']),
                'regularTasks': regular_tasks,
                'qualityTasks': quality_tasks,
                'customerTasks': customer_tasks,
                'lastTaskEnd': mech['busy_until'].isoformat() if mech['busy_until'] else None,
                'utilizationHours': sum(t.get('duration', 0) for t in mech['assigned_tasks']) / 60
            })

    # Sort mechanic summary by utilization
    mechanic_summary.sort(key=lambda x: x['utilizationHours'], reverse=True)

    # Calculate team statistics
    team_stats = {}
    for team in team_capacities.keys():
        team_tasks = [a for a in assignments if a['team'] == team]
        team_conflicts = [c for c in conflicts if c['team'] == team]

        team_stats[team] = {
            'capacity': team_capacities[team],
            'tasksAssigned': len(team_tasks),
            'conflicts': len(team_conflicts),
            'successRate': (len(team_tasks) - len(team_conflicts)) / len(team_tasks) * 100 if team_tasks else 0
        }

    return jsonify({
        'success': True,
        'totalAssigned': total_assigned,
        'partialAssigned': partial_assigned,
        'totalConflicts': total_conflicts,
        'assignments': assignments[:50],  # Return first 50 for display
        'conflicts': conflicts[:20],  # Return first 20 conflicts
        'mechanicSummary': mechanic_summary,
        'teamStatistics': team_stats,
        'message': f'Assigned {total_assigned} tasks fully, {partial_assigned} partially, with {total_conflicts} conflicts'
    })


@assignments_bp.route('/mechanic/<mechanic_id>/assigned_tasks')
def get_mechanic_assigned_tasks(mechanic_id):
    """Get assigned tasks for a specific mechanic"""
    mechanic_assignments = current_app.mechanic_assignments
    scenario = request.args.get('scenario', 'baseline')
    date = request.args.get('date', None)

    if scenario not in mechanic_assignments:
        return jsonify({'tasks': [], 'message': 'No assignments for this scenario'})

    if mechanic_id not in mechanic_assignments[scenario]:
        return jsonify({'tasks': [], 'message': 'No assignments for this mechanic'})

    tasks = mechanic_assignments[scenario][mechanic_id]

    # Filter by date if provided
    if date:
        target_date = datetime.fromisoformat(date).date()
        tasks = [t for t in tasks if datetime.fromisoformat(t['startTime']).date() == target_date]

    # Sort by start time
    tasks.sort(key=lambda x: x['startTime'])

    # Check for conflicts (overlapping tasks)
    conflicts = []
    for i in range(len(tasks) - 1):
        current_end = datetime.fromisoformat(tasks[i]['endTime'])
        next_start = datetime.fromisoformat(tasks[i + 1]['startTime'])
        if current_end > next_start:
            conflicts.append({
                'task1': tasks[i]['taskId'],
                'task2': tasks[i + 1]['taskId'],
                'overlap': (current_end - next_start).total_seconds() / 60
            })

    # Get shift information if available
    shift = '1st Shift'  # Default
    if tasks:
        shift = tasks[0].get('shift', '1st Shift')

    return jsonify({
        'mechanicId': mechanic_id,
        'tasks': tasks,
        'totalTasks': len(tasks),
        'conflicts': conflicts,
        'hasConflicts': len(conflicts) > 0,
        'shift': shift
    })


@assignments_bp.route('/team/<team_name>/tasks')
def get_team_tasks(team_name):
    """Get tasks for a specific team"""
    scenario_results = current_app.scenario_results
    scheduler = current_app.scheduler

    scenario = request.args.get('scenario', 'baseline')
    shift = request.args.get('shift', 'all')
    limit = int(request.args.get('limit', 30))
    start_date = request.args.get('date', None)

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    tasks = scenario_results[scenario]['tasks']

    # Filter by team
    if team_name != 'all':
        tasks = [t for t in tasks if t['team'] == team_name]

    # Filter by shift
    if shift != 'all':
        tasks = [t for t in tasks if t['shift'] == shift]

    # Filter by date if provided
    if start_date:
        target_date = datetime.fromisoformat(start_date).date()
        tasks = [t for t in tasks
                 if datetime.fromisoformat(t['startTime']).date() == target_date]

    # Sort by start time and limit
    tasks.sort(key=lambda x: x['startTime'])
    tasks = tasks[:limit]

    # Add team capacity info
    team_capacity = scenario_results[scenario]['teamCapacities'].get(team_name, 0)
    team_shifts = []
    if scheduler and team_name in scheduler.team_shifts:
        team_shifts = scheduler.team_shifts[team_name]
    elif scheduler and team_name in scheduler.quality_team_shifts:
        team_shifts = scheduler.quality_team_shifts[team_name]

    return jsonify({
        'tasks': tasks,
        'total': len(tasks),
        'teamCapacity': team_capacity,
        'teamShifts': team_shifts,
        'utilization': scenario_results[scenario]['utilization'].get(team_name, 0),
        'instanceBased': True
    })
