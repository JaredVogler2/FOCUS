# app.py - Updated Flask Web Server for Production Scheduling Dashboard
# Compatible with corrected ProductionScheduler with product-task instances

from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS
import pandas as pd
import json
from datetime import datetime, timedelta
import os
from collections import defaultdict
import traceback

# Import the corrected scheduler
from scheduler import ProductionScheduler

app = Flask(__name__)
CORS(app)  # Enable CORS for API calls

app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}

# Global scheduler instance
scheduler = None
scenario_results = {}  # Make sure this is initialized as empty dict, not None
mechanic_assignments = {}  # Store assignments per scenario for conflict-free scheduling


def initialize_scheduler():
    """Initialize the scheduler with product-task instances"""
    global scheduler, scenario_results

    try:
        print("=" * 80)
        print("Initializing Production Scheduler Dashboard")
        print("With Product-Task Instance Architecture")
        print("=" * 80)

        # Initialize scheduler
        scheduler = ProductionScheduler('scheduling_data.csv', debug=False, late_part_delay_days=1.0)
        scheduler.load_data_from_csv()

        print("\nScheduler loaded successfully!")
        print(f"Total task instances: {len(scheduler.tasks)}")
        print(f"Product lines: {len(scheduler.delivery_dates)}")

        # Count task instances by type and product
        task_type_counts = defaultdict(int)
        product_instance_counts = defaultdict(int)

        for instance_id, task_info in scheduler.tasks.items():
            task_type_counts[task_info['task_type']] += 1
            if 'product' in task_info and task_info['product']:
                product_instance_counts[task_info['product']] += 1

        print(f"\nTask Instance Structure:")
        for task_type, count in sorted(task_type_counts.items()):
            print(f"- {task_type}: {count} instances")

        print(f"\nTask Instances per Product:")
        for product in sorted(scheduler.delivery_dates.keys()):
            count = product_instance_counts.get(product, 0)
            start, end = scheduler.product_remaining_ranges.get(product, (0, 0))
            print(f"- {product}: {count} instances (tasks {start}-{end} remaining)")

        # ========== RUN BASELINE SCENARIO ==========
        print("\n" + "-" * 40)
        print("Running BASELINE scenario...")

        # Baseline uses original CSV capacities
        scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
        scenario_results['baseline'] = export_scenario_with_capacities(scheduler, 'baseline')
        print(f"✓ Baseline complete: {scenario_results['baseline']['makespan']} days makespan")

        # ========== RUN SCENARIO 1 ==========
        print("\nRunning SCENARIO 1 (CSV Headcount)...")

        # Reset to original capacities before running scenario 1
        for team, capacity in scheduler._original_team_capacity.items():
            scheduler.team_capacity[team] = capacity
        for team, capacity in scheduler._original_quality_capacity.items():
            scheduler.quality_team_capacity[team] = capacity

        # Run scenario 1 (which uses CSV capacities)
        result1 = scheduler.scenario_1_csv_headcount()

        # Capture the state with CSV capacities
        scenario_results['scenario1'] = export_scenario_with_capacities(scheduler, 'scenario1')
        print(f"✓ Scenario 1 complete: {scenario_results['scenario1']['makespan']} days makespan")

        # ========== RUN SCENARIO 2 ==========
        print("\nRunning SCENARIO 2 (Minimize Makespan)...")

        # Run scenario 2 optimization
        result2 = scheduler.scenario_2_minimize_makespan(min_mechanics=1, max_mechanics=30, min_quality=1,
                                                         max_quality=10)

        if result2:
            # Store optimal values for reference
            scheduler._scenario2_optimal_mechanics = result2['optimal_mechanics']
            scheduler._scenario2_optimal_quality = result2['optimal_quality']

            # Set the uniform capacities that were found optimal
            for team in scheduler.team_capacity:
                scheduler.team_capacity[team] = result2['optimal_mechanics']
            for team in scheduler.quality_team_capacity:
                scheduler.quality_team_capacity[team] = result2['optimal_quality']

            # Re-run scheduling with these uniform capacities to ensure consistency
            scheduler.task_schedule = {}
            scheduler._critical_path_cache = {}
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            # Capture the state with uniform capacities
            scenario_results['scenario2'] = export_scenario_with_capacities(scheduler, 'scenario2')
            scenario_results['scenario2']['optimalMechanics'] = result2['optimal_mechanics']
            scenario_results['scenario2']['optimalQuality'] = result2['optimal_quality']
        else:
            # Fallback if scenario 2 fails
            scenario_results['scenario2'] = export_scenario_with_capacities(scheduler, 'scenario2')

        print(f"✓ Scenario 2 complete: {scenario_results['scenario2']['makespan']} days makespan")

        # ========== RUN SCENARIO 3 ==========
        print("\nRunning SCENARIO 3 (Multi-Dimensional Optimization)...")

        # Run scenario 3 optimization
        result3 = scheduler.scenario_3_multidimensional_optimization(
            target_lateness=-1,
            scenario2_results=result2,
            max_iterations=200
        )

        if result3 and result3.get('config'):
            # Set the optimized capacities (different per team)
            for team, capacity in result3['config']['mechanic'].items():
                scheduler.team_capacity[team] = capacity
            for team, capacity in result3['config']['quality'].items():
                scheduler.quality_team_capacity[team] = capacity

            # Re-run scheduling with optimized capacities
            scheduler.task_schedule = {}
            scheduler._critical_path_cache = {}
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            # Capture the state with optimized capacities
            scenario_results['scenario3'] = export_scenario_with_capacities(scheduler, 'scenario3')

            # Add achieved lateness if available
            if result3.get('max_lateness') is not None:
                scenario_results['scenario3']['achievedMaxLateness'] = result3['max_lateness']

            print(f"✓ Scenario 3 complete: {scenario_results['scenario3']['makespan']} days makespan")
            print(f"  Maximum lateness: {result3.get('max_lateness', 'N/A')} days")
        else:
            print("✗ Scenario 3 failed to find solution")
            # Use scenario 2 results as fallback but mark as scenario3
            scenario_results['scenario3'] = scenario_results['scenario2'].copy()
            scenario_results['scenario3']['scenarioId'] = 'scenario3'
            scenario_results['scenario3']['description'] = 'Scenario 3: Failed - using Scenario 2 results'

        # ========== RESTORE ORIGINAL CAPACITIES ==========
        # Important: Restore original capacities after all scenarios complete
        for team, capacity in scheduler._original_team_capacity.items():
            scheduler.team_capacity[team] = capacity
        for team, capacity in scheduler._original_quality_capacity.items():
            scheduler.quality_team_capacity[team] = capacity

        print("\n" + "=" * 80)
        print("All scenarios completed successfully!")
        print("=" * 80)

        # Print summary of team capacities for each scenario
        print("\nTeam Capacity Summary by Scenario:")
        for scenario_id in ['baseline', 'scenario1', 'scenario2', 'scenario3']:
            if scenario_id in scenario_results:
                total = sum(scenario_results[scenario_id]['teamCapacities'].values())
                print(f"  {scenario_id}: {total} total workforce")
                # Show a sample team to verify capacities are different
                sample_team = 'Mechanic Team 1'
                if sample_team in scenario_results[scenario_id]['teamCapacities']:
                    print(f"    {sample_team}: {scenario_results[scenario_id]['teamCapacities'][sample_team]} capacity")

        return scenario_results

    except Exception as e:
        print(f"\n✗ ERROR during initialization: {str(e)}")
        traceback.print_exc()
        raise


def export_scenario_with_capacities(scheduler, scenario_name):
    """Export scenario results including current team capacities and shift information"""

    # Get current team capacities from scheduler state
    team_capacities = {}
    team_capacities.update(scheduler.team_capacity.copy())
    team_capacities.update(scheduler.quality_team_capacity.copy())

    # Get team shifts information
    team_shifts = {}

    # Add mechanic team shifts
    for team in scheduler.team_shifts:
        team_shifts[team] = scheduler.team_shifts[team]

    # Add quality team shifts
    for team in scheduler.quality_team_shifts:
        team_shifts[team] = scheduler.quality_team_shifts[team]

    # Create task list for export
    tasks = []

    # Use global_priority_list if available, otherwise use task_schedule
    if hasattr(scheduler, 'global_priority_list') and scheduler.global_priority_list:
        for priority_item in scheduler.global_priority_list:
            task_instance_id = priority_item.get('task_instance_id')
            if task_instance_id in scheduler.task_schedule:
                schedule = scheduler.task_schedule[task_instance_id]
                task_info = scheduler.tasks.get(task_instance_id, {})

                tasks.append({
                    'taskId': task_instance_id,
                    'type': priority_item.get('task_type', 'Production'),
                    'product': priority_item.get('product_line', 'Unknown'),
                    'team': schedule.get('team', ''),
                    'startTime': schedule['start_time'].isoformat() if schedule.get('start_time') else '',
                    'endTime': schedule['end_time'].isoformat() if schedule.get('end_time') else '',
                    'duration': schedule.get('duration', 60),
                    'mechanics': schedule.get('mechanics_required', 1),
                    'shift': schedule.get('shift', '1st'),
                    'priority': priority_item.get('global_priority', 999),
                    'dependencies': [],  # Could be populated from constraints
                    'isLatePartTask': task_instance_id in scheduler.late_part_tasks,
                    'isReworkTask': task_instance_id in scheduler.rework_tasks,
                    'isCritical': priority_item.get('slack_hours', 999) < 24,
                    'slackHours': priority_item.get('slack_hours', 999)
                })
    else:
        # Fallback to task_schedule
        for task_instance_id, schedule in scheduler.task_schedule.items():
            task_info = scheduler.tasks.get(task_instance_id, {})

            tasks.append({
                'taskId': task_instance_id,
                'type': schedule.get('task_type', 'Production'),
                'product': schedule.get('product', 'Unknown'),
                'team': schedule.get('team', ''),
                'startTime': schedule['start_time'].isoformat() if schedule.get('start_time') else '',
                'endTime': schedule['end_time'].isoformat() if schedule.get('end_time') else '',
                'duration': schedule.get('duration', 60),
                'mechanics': schedule.get('mechanics_required', 1),
                'shift': schedule.get('shift', '1st'),
                'priority': 999,
                'dependencies': [],
                'isLatePartTask': task_instance_id in scheduler.late_part_tasks,
                'isReworkTask': task_instance_id in scheduler.rework_tasks,
                'isCritical': False,
                'slackHours': 999
            })

    # Calculate makespan and metrics
    makespan = scheduler.calculate_makespan()
    lateness_metrics = scheduler.calculate_lateness_metrics()

    # Calculate utilization based on scheduled tasks and current capacities
    utilization = {}
    team_task_minutes = {}

    # Calculate total scheduled minutes per team
    for task_id, schedule in scheduler.task_schedule.items():
        team = schedule.get('team')
        if team:
            if team not in team_task_minutes:
                team_task_minutes[team] = 0
            team_task_minutes[team] += schedule.get('duration', 0)

    # Calculate utilization percentage for each team
    total_available_minutes = 8 * 60 * makespan  # 8 hours per day * makespan days

    for team, capacity in team_capacities.items():
        if capacity > 0:
            task_minutes = team_task_minutes.get(team, 0)
            available_minutes = total_available_minutes * capacity
            if available_minutes > 0:
                utilization[team] = min(100, round((task_minutes / available_minutes) * 100, 1))
            else:
                utilization[team] = 0
        else:
            utilization[team] = 0

    # Calculate average utilization
    avg_utilization = sum(utilization.values()) / len(utilization) if utilization else 0

    # Process products data
    products = []
    for product, metrics in lateness_metrics.items():
        products.append({
            'name': product,
            'totalTasks': metrics['total_tasks'],
            'completedTasks': 0,  # Would need tracking
            'latePartsCount': metrics['task_breakdown'].get('Late Part', 0),
            'reworkCount': metrics['task_breakdown'].get('Rework', 0),
            'deliveryDate': metrics['delivery_date'].isoformat() if metrics['delivery_date'] else '',
            'projectedCompletion': metrics['projected_completion'].isoformat() if metrics[
                'projected_completion'] else '',
            'onTime': metrics['on_time'],
            'latenessDays': metrics['lateness_days'] if metrics['lateness_days'] < 999999 else 0,
            'progress': 0,  # Would need calculation
            'daysRemaining': (metrics['delivery_date'] - datetime.now()).days if metrics['delivery_date'] else 999,
            'criticalPath': sum(1 for t in tasks if t['product'] == product and t['isCritical'])
        })

    # Calculate on-time rate
    on_time_products = sum(1 for p in products if p['onTime'])
    on_time_rate = round((on_time_products / len(products) * 100) if products else 0, 1)

    # Calculate max lateness
    max_lateness = max((p['latenessDays'] for p in products if p['latenessDays'] < 999999), default=0)

    # Count total workforce
    total_workforce = sum(team_capacities.values())
    total_mechanics = sum(cap for team, cap in team_capacities.items() if 'Quality' not in team)
    total_quality = sum(cap for team, cap in team_capacities.items() if 'Quality' in team)

    # Build the complete scenario data
    scenario_data = {
        'scenarioId': scenario_name,
        'tasks': tasks,
        'teamCapacities': team_capacities,  # Dynamic capacities from current scheduler state
        'teams': sorted(list(team_capacities.keys())),
        'teamShifts': team_shifts,  # Include team shift assignments
        'products': products,
        'utilization': utilization,
        'totalWorkforce': total_workforce,
        'totalMechanics': total_mechanics,
        'totalQuality': total_quality,
        'avgUtilization': round(avg_utilization, 1),
        'makespan': makespan,
        'onTimeRate': on_time_rate,
        'maxLateness': max_lateness,
        'totalTasks': len(tasks),
        'metrics': {
            'totalMechanics': total_mechanics,
            'totalQuality': total_quality,
            'totalCapacity': total_workforce,
            'criticalTaskCount': sum(1 for t in tasks if t['isCritical']),
            'latePartTaskCount': sum(1 for t in tasks if t['isLatePartTask']),
            'reworkTaskCount': sum(1 for t in tasks if t['isReworkTask'])
        }
    }

    # Add scenario-specific information
    if scenario_name == 'baseline':
        scenario_data['description'] = 'Baseline scenario using CSV capacity data'
    elif scenario_name == 'scenario1':
        scenario_data['description'] = 'Scenario 1: CSV Headcount optimization'
    elif scenario_name == 'scenario2':
        scenario_data['description'] = 'Scenario 2: Minimize Makespan with uniform capacity'
        # Add optimal values if available
        if hasattr(scheduler, '_scenario2_optimal_mechanics'):
            scenario_data['optimalMechanics'] = scheduler._scenario2_optimal_mechanics
            scenario_data['optimalQuality'] = scheduler._scenario2_optimal_quality
    elif scenario_name == 'scenario3':
        scenario_data['description'] = 'Scenario 3: Multi-Dimensional optimization'
        # Add achieved lateness if available
        if max_lateness < 0:
            scenario_data['achievedMaxLateness'] = max_lateness

    return scenario_data


# ========== NEW AUTO-ASSIGN ENDPOINTS ==========

@app.route('/api/auto_assign', methods=['POST'])
def auto_assign_tasks():
    """Auto-assign tasks to mechanics avoiding conflicts"""
    global mechanic_assignments

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
                (team_filter == 'all-mechanics' and 'Mechanic' in team) or \
                (team_filter == 'all-quality' and 'Quality' in team) or \
                team_filter == team:

            is_quality = 'Quality' in team
            for i in range(capacity):
                mechanic_info = {
                    'id': f"{'qual' if is_quality else 'mech'}_{mechanic_id}",
                    'name': f"{'Inspector' if is_quality else 'Mechanic'} {mechanic_id}",
                    'team': team,
                    'busy_until': None,  # Track when mechanic becomes available
                    'assigned_tasks': []
                }
                available_mechanics.append(mechanic_info)
                mechanic_id += 1

    # Get tasks to assign (filtered by team)
    tasks_to_assign = []
    for task in scenario_data.get('tasks', []):
        if team_filter == 'all' or \
                (team_filter == 'all-mechanics' and task['team'] in [m['team'] for m in available_mechanics if
                                                                     'Mechanic' in m['team']]) or \
                (team_filter == 'all-quality' and task['team'] in [m['team'] for m in available_mechanics if
                                                                   'Quality' in m['team']]) or \
                task['team'] == team_filter:
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
                    'product': task['product']
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
                    'shift': task.get('shift', '1st')
                })

            assignments.append({
                'taskId': task['taskId'],
                'mechanics': assigned_names,
                'startTime': task['startTime'],
                'conflict': False
            })
        else:
            # Record conflict - not enough free mechanics
            conflicts.append({
                'taskId': task['taskId'],
                'reason': f'Need {mechanics_needed} mechanics but only {len(free_mechanics)} available',
                'startTime': task['startTime']
            })

            # Try to assign whatever mechanics are available
            if free_mechanics:
                for mech in free_mechanics:
                    mech['busy_until'] = task_end
                    mech['assigned_tasks'].append({
                        'taskId': task['taskId'],
                        'conflict': True
                    })

    # Calculate statistics
    total_assigned = len(assignments)
    total_conflicts = len(conflicts)

    # Build mechanic summary
    mechanic_summary = []
    for mech in available_mechanics:
        if mech['assigned_tasks']:
            mechanic_summary.append({
                'id': mech['id'],
                'name': mech['name'],
                'team': mech['team'],
                'tasksAssigned': len(mech['assigned_tasks']),
                'lastTaskEnd': mech['busy_until'].isoformat() if mech['busy_until'] else None
            })

    return jsonify({
        'success': True,
        'totalAssigned': total_assigned,
        'totalConflicts': total_conflicts,
        'assignments': assignments[:50],  # Return first 50 for display
        'conflicts': conflicts[:20],  # Return first 20 conflicts
        'mechanicSummary': mechanic_summary,
        'message': f'Assigned {total_assigned} tasks with {total_conflicts} conflicts'
    })


@app.route('/api/mechanic/<mechanic_id>/assigned_tasks')
def get_mechanic_assigned_tasks(mechanic_id):
    """Get assigned tasks for a specific mechanic"""
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


# ... previous code continues ...

# ========== FLASK ROUTES ==========

@app.route('/')
def index():
    """Serve the main dashboard page"""
    return render_template('dashboard2.html')


@app.route('/api/scenarios')
def get_scenarios():
    """Get list of available scenarios with descriptions"""
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
                'description': 'Optimize per-team capacity for minimum lateness'
            }
        ],
        'architecture': 'Product-Task Instances',
        'totalInstances': len(scheduler.tasks) if scheduler else 0
    })


@app.route('/api/scenario/<scenario_id>')
def get_scenario_data(scenario_id):
    """Get data for a specific scenario"""
    global scenario_results

    if scenario_id not in scenario_results:
        return jsonify({'error': f'Scenario {scenario_id} not found', 'available': list(scenario_results.keys())}), 404

    # Get the scenario data (already has teamCapacities from export_scenario_with_capacities)
    scenario_data = scenario_results[scenario_id].copy()

    # Verify teamCapacities exists and is properly formatted
    if 'teamCapacities' not in scenario_data or not scenario_data['teamCapacities']:
        print(f"WARNING: Scenario {scenario_id} missing teamCapacities! Attempting to reconstruct...")

        # Try to reconstruct from tasks if needed (fallback)
        team_capacities = {}

        if 'tasks' in scenario_data:
            unique_teams = set()
            for task in scenario_data['tasks']:
                if 'team' in task and task['team']:
                    unique_teams.add(task['team'])

            # Assign default capacities based on team type
            for team in unique_teams:
                if 'Mechanic' in team:
                    team_capacities[team] = 7  # Default mechanic capacity
                elif 'Quality' in team:
                    team_capacities[team] = 3  # Default quality capacity
                else:
                    team_capacities[team] = 1

        scenario_data['teamCapacities'] = team_capacities

    # Ensure teams list is included and sorted
    if 'teams' not in scenario_data:
        scenario_data['teams'] = sorted(list(scenario_data.get('teamCapacities', {}).keys()))

    # Verify all teams mentioned in tasks are in teamCapacities
    if 'tasks' in scenario_data and scenario_data['tasks']:
        teams_in_tasks = set()
        for task in scenario_data['tasks']:
            if 'team' in task and task['team']:
                teams_in_tasks.add(task['team'])

        # Add any missing teams with appropriate default capacity
        for team in teams_in_tasks:
            if team not in scenario_data['teamCapacities']:
                print(f"WARNING: Team {team} in tasks but not in teamCapacities, adding with default capacity")
                if 'Mechanic' in team:
                    scenario_data['teamCapacities'][team] = 7
                elif 'Quality' in team:
                    scenario_data['teamCapacities'][team] = 3
                else:
                    scenario_data['teamCapacities'][team] = 1

        # Update teams list to include all teams
        scenario_data['teams'] = sorted(list(scenario_data['teamCapacities'].keys()))

    # Ensure required fields exist with defaults if missing
    scenario_data.setdefault('scenarioId', scenario_id)
    scenario_data.setdefault('tasks', [])
    scenario_data.setdefault('products', [])
    scenario_data.setdefault('utilization', {})
    scenario_data.setdefault('totalWorkforce', sum(scenario_data.get('teamCapacities', {}).values()))
    scenario_data.setdefault('makespan', 0)
    scenario_data.setdefault('onTimeRate', 0)
    scenario_data.setdefault('avgUtilization', 0)
    scenario_data.setdefault('maxLateness', 0)
    scenario_data.setdefault('totalTasks', len(scenario_data.get('tasks', [])))

    # Add metrics if missing
    if 'metrics' not in scenario_data:
        total_mechanics = sum(cap for team, cap in scenario_data['teamCapacities'].items() if 'Quality' not in team)
        total_quality = sum(cap for team, cap in scenario_data['teamCapacities'].items() if 'Quality' in team)

        scenario_data['metrics'] = {
            'totalMechanics': total_mechanics,
            'totalQuality': total_quality,
            'totalCapacity': scenario_data['totalWorkforce'],
            'criticalTaskCount': sum(1 for t in scenario_data.get('tasks', []) if t.get('isCritical', False)),
            'latePartTaskCount': sum(1 for t in scenario_data.get('tasks', []) if t.get('isLatePartTask', False)),
            'reworkTaskCount': sum(1 for t in scenario_data.get('tasks', []) if t.get('isReworkTask', False))
        }

    # Log what we're returning for debugging
    print(f"Returning scenario {scenario_id}:")
    print(f"  - Tasks: {len(scenario_data.get('tasks', []))}")
    print(f"  - Teams: {len(scenario_data.get('teamCapacities', {}))}")
    print(f"  - Total workforce: {scenario_data.get('totalWorkforce', 0)}")

    # Return the complete scenario data
    return jsonify(scenario_data)


@app.route('/api/scenario/<scenario_id>/summary')
def get_scenario_summary(scenario_id):
    """Get summary statistics for a scenario"""
    if scenario_id not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    data = scenario_results[scenario_id]

    # Calculate product-specific summaries
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


@app.route('/api/team/<team_name>/tasks')
def get_team_tasks(team_name):
    """Get tasks for a specific team"""
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


@app.route('/api/product/<product_name>/tasks')
def get_product_tasks(product_name):
    """Get all tasks for a specific product"""
    scenario = request.args.get('scenario', 'baseline')

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    tasks = scenario_results[scenario]['tasks']

    # Filter by product (each task instance belongs to exactly one product)
    product_tasks = [task for task in tasks if task['product'] == product_name]

    # Separate by task type
    task_breakdown = defaultdict(list)
    for task in product_tasks:
        task_breakdown[task['type']].append(task)

    # Sort each type by start time
    for task_type in task_breakdown:
        task_breakdown[task_type].sort(key=lambda x: x['startTime'])

    # Get product info
    product_info = next((p for p in scenario_results[scenario]['products']
                         if p['name'] == product_name), None)

    # Add task range information
    if scheduler and product_name in scheduler.product_remaining_ranges:
        start_task, end_task = scheduler.product_remaining_ranges[product_name]
        task_range_info = {
            'startTask': start_task,
            'endTask': end_task,
            'remainingCount': end_task - start_task + 1,
            'completedTasks': list(range(1, start_task)) if start_task > 1 else []
        }
    else:
        task_range_info = None

    return jsonify({
        'productName': product_name,
        'productInfo': product_info,
        'taskRangeInfo': task_range_info,
        'tasks': product_tasks,
        'taskBreakdown': {k: len(v) for k, v in task_breakdown.items()},
        'tasksByType': dict(task_breakdown),
        'totalTasks': len(product_tasks),
        'instanceBased': True
    })


@app.route('/api/task/<task_instance_id>')
def get_task_details(task_instance_id):
    """Get detailed information about a specific task instance"""
    scenario = request.args.get('scenario', 'baseline')

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    # Find the task
    task = next((t for t in scenario_results[scenario]['tasks']
                 if t['taskId'] == task_instance_id), None)

    if not task:
        return jsonify({'error': 'Task not found'}), 404

    # Get additional details from scheduler
    task_details = {
        'taskInstanceId': task_instance_id,
        'originalTaskId': task.get('originalTaskId'),
        'product': task['product'],
        'type': task['type'],
        'team': task['team'],
        'startTime': task['startTime'],
        'endTime': task['endTime'],
        'duration': task['duration'],
        'mechanics': task['mechanics'],
        'shift': task['shift'],
        'slackHours': task['slackHours'],
        'dependencies': task.get('dependencies', [])
    }

    # Get predecessors and successors from dynamic constraints
    if scheduler:
        dynamic_constraints = scheduler.build_dynamic_dependencies()
        predecessors = []
        successors = []

        for constraint in dynamic_constraints:
            if constraint['Second'] == task_instance_id:
                predecessors.append({
                    'taskId': constraint['First'],
                    'relationship': constraint['Relationship'],
                    'product': constraint.get('Product')
                })
            elif constraint['First'] == task_instance_id:
                successors.append({
                    'taskId': constraint['Second'],
                    'relationship': constraint['Relationship'],
                    'product': constraint.get('Product')
                })

        task_details['predecessors'] = predecessors
        task_details['successors'] = successors

    return jsonify(task_details)


@app.route('/api/mechanic/<mechanic_id>/tasks')
def get_mechanic_tasks(mechanic_id):
    """Get tasks assigned to a specific mechanic (legacy - for compatibility)"""
    scenario = request.args.get('scenario', 'baseline')
    date = request.args.get('date', datetime.now().isoformat())

    # First check if we have auto-assigned tasks
    if scenario in mechanic_assignments and mechanic_id in mechanic_assignments[scenario]:
        return get_mechanic_assigned_tasks(mechanic_id)

    # Otherwise fall back to demo assignment
    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    # For demo purposes, assign tasks based on mechanic ID pattern
    tasks = scenario_results[scenario]['tasks']

    # Simple assignment logic for demo
    mechanic_num = int(''.join(filter(str.isdigit, mechanic_id))) if any(c.isdigit() for c in mechanic_id) else 1
    assigned_tasks = []

    # Filter tasks by date
    target_date = datetime.fromisoformat(date).date()
    daily_tasks = [t for t in tasks if datetime.fromisoformat(t['startTime']).date() == target_date]

    # Assign every Nth task to this mechanic (simple demo logic)
    for i, task in enumerate(daily_tasks):
        if i % 8 == (mechanic_num - 1):  # Distribute among 8 mechanics
            assigned_tasks.append(task)
            if len(assigned_tasks) >= 6:  # Max 6 tasks per day
                break

    # Sort by start time
    assigned_tasks.sort(key=lambda x: x['startTime'])

    return jsonify({
        'mechanicId': mechanic_id,
        'tasks': assigned_tasks,
        'shift': '1st',  # Would be determined by actual assignment
        'date': date,
        'totalAssigned': len(assigned_tasks),
        'instanceBased': True
    })


@app.route('/api/export/<scenario_id>')
def export_scenario(scenario_id):
    """Export scenario data to CSV"""
    if scenario_id not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    data = scenario_results[scenario_id]

    # Create DataFrame from tasks
    df = pd.DataFrame(data['tasks'])

    # Add additional columns
    df['Scenario'] = scenario_id
    df['MaxLateness'] = data.get('maxLateness', 0)
    df['TotalLateness'] = data.get('totalLateness', 0)
    df['InstanceBased'] = True

    # Save to CSV
    filename = f'export_{scenario_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    filepath = os.path.join('exports', filename)

    # Create exports directory if it doesn't exist
    os.makedirs('exports', exist_ok=True)

    df.to_csv(filepath, index=False)

    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/api/assign_task', methods=['POST'])
def assign_task():
    """Assign a task to a mechanic"""
    data = request.json
    task_instance_id = data.get('taskId')
    mechanic_id = data.get('mechanicId')
    scenario = data.get('scenario', 'baseline')

    # Store the assignment
    if scenario not in mechanic_assignments:
        mechanic_assignments[scenario] = {}

    if mechanic_id not in mechanic_assignments[scenario]:
        mechanic_assignments[scenario][mechanic_id] = []

    # Find the task details
    if scenario in scenario_results:
        task = next((t for t in scenario_results[scenario]['tasks']
                     if t['taskId'] == task_instance_id), None)
        if task:
            mechanic_assignments[scenario][mechanic_id].append({
                'taskId': task_instance_id,
                'taskType': task['type'],
                'product': task['product'],
                'startTime': task['startTime'],
                'endTime': task['endTime'],
                'duration': task['duration'],
                'team': task['team'],
                'shift': task.get('shift', '1st')
            })

    return jsonify({
        'success': True,
        'taskInstanceId': task_instance_id,
        'mechanicId': mechanic_id,
        'message': f'Task instance {task_instance_id} assigned to {mechanic_id}',
        'instanceBased': True
    })


@app.route('/api/refresh', methods=['POST'])
def refresh_data():
    """Refresh all scenario data"""
    try:
        initialize_scheduler()
        return jsonify({
            'success': True,
            'message': 'All scenarios refreshed with product-task instances',
            'timestamp': datetime.now().isoformat(),
            'totalInstances': len(scheduler.tasks) if scheduler else 0,
            'instanceBased': True
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'instanceBased': True
        }), 500


@app.route('/api/teams')
def get_teams():
    """Get list of all teams with their capacities"""
    teams = []

    if scheduler:
        # Add mechanic teams
        for team in scheduler.team_capacity:
            teams.append({
                'id': team,
                'type': 'mechanic',
                'capacity': scheduler.team_capacity[team],
                'shifts': scheduler.team_shifts.get(team, [])
            })

        # Add quality teams
        for team in scheduler.quality_team_capacity:
            teams.append({
                'id': team,
                'type': 'quality',
                'capacity': scheduler.quality_team_capacity[team],
                'shifts': scheduler.quality_team_shifts.get(team, [])
            })

    return jsonify({'teams': teams, 'instanceBased': True})


@app.route('/api/mechanics')
def get_mechanics():
    """Get list of all mechanics"""
    # In production, this would come from a database
    mechanics = [
        {'id': 'mech1', 'name': 'John Smith', 'team': 'Mechanic Team 1'},
        {'id': 'mech2', 'name': 'Jane Doe', 'team': 'Mechanic Team 1'},
        {'id': 'mech3', 'name': 'Bob Johnson', 'team': 'Mechanic Team 2'},
        {'id': 'mech4', 'name': 'Alice Williams', 'team': 'Mechanic Team 2'},
        {'id': 'mech5', 'name': 'Charlie Brown', 'team': 'Mechanic Team 3'},
        {'id': 'mech6', 'name': 'Diana Prince', 'team': 'Mechanic Team 3'},
        {'id': 'mech7', 'name': 'Frank Castle', 'team': 'Mechanic Team 4'},
        {'id': 'mech8', 'name': 'Grace Lee', 'team': 'Mechanic Team 4'},
        {'id': 'qual1', 'name': 'Tom Wilson', 'team': 'Quality Team 1'},
        {'id': 'qual2', 'name': 'Sarah Connor', 'team': 'Quality Team 2'},
        {'id': 'qual3', 'name': 'Mike Ross', 'team': 'Quality Team 3'}
    ]
    return jsonify({'mechanics': mechanics, 'instanceBased': True})


@app.route('/api/stats')
def get_statistics():
    """Get overall statistics across all scenarios"""
    stats = {
        'scenarios': {},
        'comparison': {},
        'instanceBased': True
    }

    for scenario_id, data in scenario_results.items():
        stats['scenarios'][scenario_id] = {
            'workforce': data['totalWorkforce'],
            'makespan': data['makespan'],
            'onTimeRate': data['onTimeRate'],
            'utilization': data['avgUtilization'],
            'maxLateness': data.get('maxLateness', 0),
            'totalLateness': data.get('totalLateness', 0),
            'totalTaskInstances': data.get('totalTaskInstances', 0),
            'scheduledTaskInstances': data.get('scheduledTaskInstances', 0)
        }

    # Calculate comparisons
    if 'baseline' in scenario_results:
        baseline_workforce = scenario_results['baseline']['totalWorkforce']
        baseline_makespan = scenario_results['baseline']['makespan']

        for scenario_id, data in scenario_results.items():
            if scenario_id != 'baseline':
                workforce_diff = data['totalWorkforce'] - baseline_workforce
                makespan_diff = data['makespan'] - baseline_makespan

                stats['comparison'][scenario_id] = {
                    'workforceDiff': workforce_diff,
                    'workforcePercent': round((workforce_diff / baseline_workforce) * 100,
                                              1) if baseline_workforce > 0 else 0,
                    'makespanDiff': makespan_diff,
                    'makespanPercent': round((makespan_diff / baseline_makespan) * 100,
                                             1) if baseline_makespan > 0 else 0
                }

    return jsonify(stats)


@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'scheduler_loaded': scheduler is not None,
        'scenarios_loaded': len(scenario_results),
        'timestamp': datetime.now().isoformat(),
        'totalTaskInstances': len(scheduler.tasks) if scheduler else 0,
        'instanceBased': True
    })

# Continue with more routes...

# ... continuing from previous code ...

@app.route('/api/task_structure')
def get_task_structure():
    """Get information about the task instance structure"""
    if not scheduler:
        return jsonify({'error': 'Scheduler not loaded'}), 500

    # Task type breakdown
    task_type_counts = defaultdict(int)
    product_instance_counts = defaultdict(int)

    for instance_id, task_info in scheduler.tasks.items():
        task_type_counts[task_info['task_type']] += 1
        if 'product' in task_info and task_info['product']:
            product_instance_counts[task_info['product']] += 1

    # Product remaining ranges
    product_ranges = {}
    for product, (start, end) in scheduler.product_remaining_ranges.items():
        product_ranges[product] = {
            'startTask': start,
            'endTask': end,
            'remainingCount': end - start + 1,
            'instanceCount': product_instance_counts.get(product, 0),
            'completedRange': f"1-{start - 1}" if start > 1 else "None"
        }

    # Calculate total baseline instances
    total_baseline_instances = sum(end - start + 1 for start, end in scheduler.product_remaining_ranges.values())

    return jsonify({
        'totalTaskInstances': len(scheduler.tasks),
        'taskTypeCounts': dict(task_type_counts),
        'productInstanceCounts': dict(product_instance_counts),
        'productRemainingRanges': product_ranges,
        'totalBaselineInstances': total_baseline_instances,
        'latePartInstances': len(scheduler.late_part_tasks),
        'reworkInstances': len(scheduler.rework_tasks),
        'qualityInspectionInstances': len(scheduler.quality_inspections),
        'instanceBased': True,
        'explanation': {
            'architecture': 'Product-Task Instance Based',
            'baselineTasks': 'Each baseline task (1-100) creates separate instances per product',
            'taskInstance': 'Format: {Product}_{TaskID} for baseline, LP_{ID} for late parts, RW_{ID} for rework',
            'dependencies': 'Dependencies are product-specific (Product A Task 1 → Product A Task 2)',
            'scheduling': 'Each instance is scheduled independently'
        }
    })


@app.route('/api/instance_mapping')
def get_instance_mapping():
    """Get mapping between task instances and original tasks"""
    if not scheduler:
        return jsonify({'error': 'Scheduler not loaded'}), 500

    # Sample of instance mappings
    sample_mappings = []
    count = 0

    for instance_id, task_info in scheduler.tasks.items():
        if count >= 50:  # Limit to 50 examples
            break

        sample_mappings.append({
            'instanceId': instance_id,
            'product': task_info.get('product', 'N/A'),
            'originalTaskId': task_info.get('original_task_id', 'N/A'),
            'taskType': task_info['task_type'],
            'duration': task_info['duration'],
            'team': task_info['team']
        })
        count += 1

    return jsonify({
        'totalInstances': len(scheduler.tasks),
        'sampleMappings': sample_mappings,
        'products': list(scheduler.delivery_dates.keys()),
        'instanceBased': True
    })


@app.route('/api/dependencies')
def get_dependencies():
    """Get dependency graph information"""
    if not scheduler:
        return jsonify({'error': 'Scheduler not loaded'}), 500

    dynamic_constraints = scheduler.build_dynamic_dependencies()

    # Count dependencies by type
    dependency_counts = defaultdict(int)
    product_dependency_counts = defaultdict(int)

    for constraint in dynamic_constraints:
        dep_type = constraint.get('Type', 'Precedence')
        dependency_counts[dep_type] += 1

        product = constraint.get('Product')
        if product:
            product_dependency_counts[product] += 1

    # Sample dependencies
    sample_dependencies = []
    for i, constraint in enumerate(dynamic_constraints[:20]):  # First 20
        sample_dependencies.append({
            'from': constraint['First'],
            'to': constraint['Second'],
            'relationship': constraint['Relationship'],
            'type': constraint.get('Type', 'Precedence'),
            'product': constraint.get('Product', 'N/A')
        })

    return jsonify({
        'totalDependencies': len(dynamic_constraints),
        'dependencyTypeCounts': dict(dependency_counts),
        'productDependencyCounts': dict(product_dependency_counts),
        'sampleDependencies': sample_dependencies,
        'instanceBased': True
    })


@app.route('/api/gantt/<product_name>')
def get_gantt_data(product_name):
    """Get Gantt chart data for a specific product"""
    scenario = request.args.get('scenario', 'baseline')

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    tasks = scenario_results[scenario]['tasks']

    # Filter by product
    product_tasks = [t for t in tasks if t['product'] == product_name]

    # Format for Gantt chart
    gantt_data = []
    for task in product_tasks:
        gantt_data.append({
            'id': task['taskId'],
            'name': task.get('displayName', task['taskId']),
            'start': task['startTime'],
            'end': task['endTime'],
            'type': task['type'],
            'team': task['team'],
            'originalTaskId': task.get('originalTaskId'),
            'dependencies': [d['predecessor'] for d in task.get('dependencies', [])]
        })

    # Sort by start time
    gantt_data.sort(key=lambda x: x['start'])

    return jsonify({
        'product': product_name,
        'tasks': gantt_data,
        'totalTasks': len(gantt_data),
        'instanceBased': True
    })


@app.route('/api/critical_path/<product_name>')
def get_critical_path(product_name):
    """Get critical path for a specific product"""
    scenario = request.args.get('scenario', 'baseline')

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    tasks = scenario_results[scenario]['tasks']

    # Filter by product and low slack (critical path)
    critical_tasks = [t for t in tasks
                      if t['product'] == product_name and t['slackHours'] < 24]

    # Sort by start time
    critical_tasks.sort(key=lambda x: x['startTime'])

    return jsonify({
        'product': product_name,
        'criticalTasks': critical_tasks,
        'totalCriticalTasks': len(critical_tasks),
        'instanceBased': True
    })


@app.route('/api/workload/<team_name>')
def get_team_workload(team_name):
    """Get workload distribution for a team"""
    scenario = request.args.get('scenario', 'baseline')

    if scenario not in scenario_results:
        return jsonify({'error': 'Scenario not found'}), 404

    tasks = scenario_results[scenario]['tasks']

    # Filter by team
    team_tasks = [t for t in tasks if t['team'] == team_name]

    # Group by date
    workload_by_date = defaultdict(lambda: {'tasks': 0, 'minutes': 0, 'mechanics': 0})

    for task in team_tasks:
        date = datetime.fromisoformat(task['startTime']).date().isoformat()
        workload_by_date[date]['tasks'] += 1
        workload_by_date[date]['minutes'] += task['duration']
        workload_by_date[date]['mechanics'] += task['mechanics']

    # Convert to list
    workload_data = []
    for date, data in sorted(workload_by_date.items()):
        workload_data.append({
            'date': date,
            'taskCount': data['tasks'],
            'totalMinutes': data['minutes'],
            'totalMechanics': data['mechanics'],
            'utilizationHours': round(data['minutes'] / 60, 1)
        })

    return jsonify({
        'team': team_name,
        'workload': workload_data,
        'totalDays': len(workload_data),
        'capacity': scenario_results[scenario]['teamCapacities'].get(team_name, 0),
        'instanceBased': True
    })


@app.route('/api/debug')
def debug_endpoint():
    """Debug endpoint to check what's loaded"""
    global scenario_results, scheduler

    debug_info = {
        'scenario_results_exists': scenario_results is not None,
        'scenarios_available': list(scenario_results.keys()) if scenario_results else [],
        'scheduler_exists': scheduler is not None,
        'scheduler_tasks': len(scheduler.tasks) if scheduler else 0,
        'scheduler_schedule': len(scheduler.task_schedule) if scheduler else 0,
        'scheduler_global_priority': len(scheduler.global_priority_list) if scheduler else 0,
    }

    # Check each scenario
    for scenario_id in scenario_results.keys():
        debug_info[f'{scenario_id}_tasks'] = len(scenario_results[scenario_id].get('tasks', []))
        debug_info[f'{scenario_id}_products'] = len(scenario_results[scenario_id].get('products', []))

    return jsonify(debug_info)


# ========== HELPER FUNCTIONS ==========

def calculate_team_utilization(scheduler):
    """Calculate utilization percentage for each team"""
    utilization = {}

    if not scheduler.task_schedule:
        return utilization

    # Working minutes per shift
    minutes_per_shift = 8.5 * 60
    makespan_days = scheduler.calculate_makespan()

    if makespan_days == 0 or makespan_days >= 999999:
        return utilization

    # Calculate for mechanic teams
    for team, capacity in scheduler.team_capacity.items():
        scheduled_minutes = 0
        task_count = 0

        for task_instance_id, schedule in scheduler.task_schedule.items():
            if schedule['team'] == team:
                scheduled_minutes += schedule['duration'] * schedule['mechanics_required']
                task_count += 1

        shifts_per_day = len(scheduler.team_shifts.get(team, []))
        available_minutes = capacity * shifts_per_day * minutes_per_shift * makespan_days

        if available_minutes > 0:
            util_percent = min(100, int((scheduled_minutes / available_minutes) * 100))
            utilization[team] = util_percent
        else:
            utilization[team] = 0

    # Calculate for quality teams
    for team, capacity in scheduler.quality_team_capacity.items():
        scheduled_minutes = 0
        task_count = 0

        for task_instance_id, schedule in scheduler.task_schedule.items():
            if schedule['team'] == team:
                scheduled_minutes += schedule['duration'] * schedule['mechanics_required']
                task_count += 1

        shifts_per_day = len(scheduler.quality_team_shifts.get(team, []))
        available_minutes = capacity * shifts_per_day * minutes_per_shift * makespan_days

        if available_minutes > 0:
            util_percent = min(100, int((scheduled_minutes / available_minutes) * 100))
            utilization[team] = util_percent
        else:
            utilization[team] = 0

    return utilization


def create_failed_scenario_data():
    """Create placeholder data for failed scenarios"""
    return {
        'scenarioName': 'scenario3',
        'totalWorkforce': 0,
        'makespan': 999999,
        'onTimeRate': 0,
        'avgUtilization': 0,
        'maxLateness': 999999,
        'totalLateness': 999999,
        'teamCapacities': {},
        'tasks': [],
        'products': [],
        'utilization': {},
        'totalTaskInstances': 0,
        'scheduledTaskInstances': 0,
        'instanceBased': True,
        'error': 'Failed to find solution within constraints'
    }


# ========== ERROR HANDLERS ==========

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found', 'instanceBased': True}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error', 'instanceBased': True}), 500


# ========== MAIN EXECUTION ==========

import sys
import socket
import os
import subprocess
import platform


def kill_port(port=5000):
    """Kill any process using the specified port"""
    system = platform.system()

    try:
        if system == 'Windows':
            # Find process using the port
            command = f'netstat -ano | findstr :{port}'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)

            if result.stdout:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if f':{port}' in line and 'LISTENING' in line:
                        # Extract PID (last column)
                        parts = line.split()
                        pid = parts[-1]

                        # Kill the process
                        kill_command = f'taskkill /F /PID {pid}'
                        subprocess.run(kill_command, shell=True, capture_output=True)
                        print(f"✓ Killed process {pid} using port {port}")

                        # Give it a moment to release the port
                        import time
                        time.sleep(1)

        else:  # Linux/Mac
            # Find and kill process using lsof
            command = f'lsof -ti:{port}'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)

            if result.stdout:
                pid = result.stdout.strip()
                kill_command = f'kill -9 {pid}'
                subprocess.run(kill_command, shell=True)
                print(f"✓ Killed process {pid} using port {port}")

                # Give it a moment to release the port
                import time
                time.sleep(1)

    except Exception as e:
        print(f"Warning: Could not auto-kill port {port}: {e}")
        print("You may need to manually kill the process if the port is in use.")


def check_and_kill_port(port=5000):
    """Check if port is in use and kill the process if it is"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()

    if result == 0:
        print(f"Port {port} is in use. Attempting to free it...")
        kill_port(port)

        # Double-check that the port is now free
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()

        if result == 0:
            print(f"✗ Failed to free port {port}. Please manually kill the process.")
            sys.exit(1)
        else:
            print(f"✓ Port {port} successfully freed!")


if __name__ == '__main__':
    try:
        # Initialize scheduler on startup
        print("\nStarting Production Scheduling Dashboard Server...")
        print("Using Product-Task Instance Architecture")
        print("-" * 80)

        # Auto-kill any process using port 5000
        # Only do this in the parent process, not the reloader
        if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            check_and_kill_port(5000)

        # Initialize with error catching
        try:
            scenario_results = initialize_scheduler()
            if not scenario_results:
                print("\n✗ ERROR: No scenarios were initialized!")
                print("Creating empty scenario data...")
                scenario_results = {
                    'baseline': {
                        'scenarioName': 'baseline',
                        'tasks': [],
                        'products': [],
                        'totalWorkforce': 0,
                        'makespan': 0,
                        'onTimeRate': 0,
                        'avgUtilization': 0,
                        'utilization': {},
                        'teamCapacities': {}
                    }
                }
        except Exception as e:
            print(f"\n✗ ERROR during initialization: {str(e)}")
            import traceback

            traceback.print_exc()

            # Create minimal scenario data so the server can still run
            scenario_results = {
                'baseline': {
                    'scenarioName': 'baseline',
                    'tasks': [],
                    'products': [],
                    'totalWorkforce': 0,
                    'makespan': 0,
                    'onTimeRate': 0,
                    'avgUtilization': 0,
                    'utilization': {},
                    'teamCapacities': {}
                }
            }

        print("\n" + "=" * 80)
        if scenario_results:
            print(f"Scenarios initialized: {list(scenario_results.keys())}")
        else:
            print("WARNING: No scenarios initialized!")
        print("Server ready! Open your browser to: http://localhost:5000")
        print("=" * 80 + "\n")

        # Run Flask app
        app.run(debug=True, host='0.0.0.0', port=5000)

    except Exception as e:
        print(f"\n✗ Failed to start server: {str(e)}")
        import traceback

        traceback.print_exc()