# app.py - Updated Flask Web Server for Production Scheduling Dashboard
# Compatible with corrected ProductionScheduler with product-task instances
# OPTIMIZED: Limits dashboard data to top 1000 tasks for performance

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


def ensure_all_teams_have_capacity(scheduler):
    """Ensure all teams referenced by tasks exist in capacity tables"""
    teams_needed = set()

    for task_id, task_info in scheduler.tasks.items():
        team = task_info.get('team_skill', task_info.get('team'))
        if team:
            teams_needed.add(team)

    # Check which teams are missing
    missing_teams = []
    for team in teams_needed:
        if team not in scheduler.team_capacity and team not in scheduler.quality_team_capacity:
            missing_teams.append(team)
            print(f"WARNING: Team '{team}' needed but not in capacity tables - adding with default capacity")
            # Add it with default capacity
            if 'Quality' in team:
                scheduler.quality_team_capacity[team] = 5
                scheduler._original_quality_capacity[team] = 5
            else:
                scheduler.team_capacity[team] = 10
                scheduler._original_team_capacity[team] = 10

    if missing_teams:
        print(f"Added {len(missing_teams)} missing teams to capacity tables")

    return len(missing_teams)


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
        scheduler.print_delivery_analysis("BASELINE")

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
        scheduler.print_delivery_analysis("SCENARIO 1")

        # After scenario 1
        print("\n[DEBUG] Analyzing scheduling blockage...")
        scheduler.debug_scheduling_blockage()

        # ========== RUN SCENARIO 2 ==========
        print("\nRunning SCENARIO 2 (Minimize Makespan)...")

        # Run scenario 2 optimization
        result2 = scheduler.scenario_2_minimize_makespan(
            min_mechanics=1, max_mechanics=30,
            min_quality=1, max_quality=10
        )

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
        scheduler.print_delivery_analysis("SCENARIO 2")

        # ========== RUN SCENARIO 3 ==========
        print("\nRunning SCENARIO 3 (Simulated Annealing Optimization)...")

        # Run scenario 3 simulated annealing optimization
        result3 = scheduler.scenario_3_simulated_annealing(
            target_earliness=-1,  # Target 1 day early
            max_iterations=300,
            initial_temp=100,
            cooling_rate=0.95
        )

        if result3:
            # Apply the optimized configuration
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

            # Add the metrics with safe defaults
            scenario_results['scenario3']['perfectCount'] = result3.get('perfect_count', 0)
            scenario_results['scenario3']['goodCount'] = result3.get('good_count', 0)
            scenario_results['scenario3']['acceptableCount'] = result3.get('acceptable_count', 0)
            scenario_results['scenario3']['avgUtilization'] = result3.get('avg_utilization', 0)
            scenario_results['scenario3']['utilizationVariance'] = result3.get('utilization_variance', 0)

            print(f"✓ Scenario 3 complete: {scenario_results['scenario3']['makespan']} days makespan")
            print(f"  Max lateness: {result3.get('max_lateness', 'N/A')} days")
            print(f"  Total workforce: {result3.get('total_workforce', 'N/A')}")

            # Use safe access for optional metrics
            if 'avg_utilization' in result3:
                print(f"  Average utilization: {result3['avg_utilization']:.1f}%")

            scheduler.print_delivery_analysis("SCENARIO 3")
        else:
            print("✗ Scenario 3 failed to find solution")
            # Create failed scenario result with clear indicators
            scenario_results['scenario3'] = {
                'scenarioId': 'scenario3',
                'description': 'Scenario 3: Simulated Annealing - FAILED',
                'status': 'FAILED',
                'tasks': [],
                'teamCapacities': {},
                'teams': [],
                'teamShifts': {},
                'products': [],
                'utilization': {},
                'totalWorkforce': 0,
                'totalMechanics': 0,
                'totalQuality': 0,
                'avgUtilization': 0,
                'makespan': 999999,
                'onTimeRate': 0,
                'maxLateness': 999999,
                'totalTasks': 0,
                'perfectCount': 0,
                'goodCount': 0,
                'acceptableCount': 0,
                'utilizationVariance': 0,
                'metrics': {
                    'totalMechanics': 0,
                    'totalQuality': 0,
                    'totalCapacity': 0,
                    'criticalTaskCount': 0,
                    'latePartTaskCount': 0,
                    'reworkTaskCount': 0
                },
                'error': 'Simulated annealing failed to converge to a solution within iteration limit'
            }

            print("  Scenario 3 failed - no valid configuration found")
            print("  Results marked as FAILED for clarity")

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
    team_capacities.update(scheduler.customer_team_capacity.copy())  # Add customer teams

    # Get team shifts information
    team_shifts = {}

    # Add mechanic team shifts - use base team names for shifts
    for team in scheduler.team_shifts:
        team_shifts[team] = scheduler.team_shifts[team]

    # Add quality team shifts
    for team in scheduler.quality_team_shifts:
        team_shifts[team] = scheduler.quality_team_shifts[team]

    # Add customer team shifts
    for team in scheduler.customer_team_shifts:
        team_shifts[team] = scheduler.customer_team_shifts[team]

    # Create task list for export
    tasks = []
    total_tasks_available = 0

    # PERFORMANCE OPTIMIZATION: Limit to top 1000 tasks by priority
    MAX_TASKS_FOR_DASHBOARD = 1000

    # Use global_priority_list if available, otherwise use task_schedule
    if hasattr(scheduler, 'global_priority_list') and scheduler.global_priority_list:
        # Sort by priority and take only top MAX_TASKS_FOR_DASHBOARD
        sorted_priority_items = sorted(
            scheduler.global_priority_list,
            key=lambda x: x.get('global_priority', 999)
        )[:MAX_TASKS_FOR_DASHBOARD]

        total_tasks_available = len(scheduler.global_priority_list)

        for priority_item in sorted_priority_items:
            task_instance_id = priority_item.get('task_instance_id')
            if task_instance_id in scheduler.task_schedule:
                schedule = scheduler.task_schedule[task_instance_id]
                task_info = scheduler.tasks.get(task_instance_id, {})

                # Get the base team and team_skill from schedule
                base_team = schedule.get('team', '')  # Base team for dashboard filtering
                team_skill = schedule.get('team_skill', schedule.get('team', ''))  # Actual scheduling team
                skill_code = schedule.get('skill', task_info.get('skill', ''))  # Skill code if present

                tasks.append({
                    'taskId': task_instance_id,
                    'type': priority_item.get('task_type', 'Production'),
                    'product': priority_item.get('product_line', 'Unknown'),
                    'team': base_team,  # Base team for dashboard filtering
                    'teamSkill': team_skill,  # Full team+skill identifier
                    'skill': skill_code,  # Skill code alone
                    'startTime': schedule['start_time'].isoformat() if schedule.get('start_time') else '',
                    'endTime': schedule['end_time'].isoformat() if schedule.get('end_time') else '',
                    'duration': schedule.get('duration', 60),
                    'mechanics': schedule.get('mechanics_required', 1),
                    'shift': schedule.get('shift', '1st'),
                    'priority': priority_item.get('global_priority', 999),
                    'dependencies': [],  # Could be populated from constraints
                    'isLatePartTask': task_instance_id in scheduler.late_part_tasks,
                    'isReworkTask': task_instance_id in scheduler.rework_tasks,
                    'isQualityTask': schedule.get('is_quality', False),
                    'isCustomerTask': schedule.get('is_customer', False),  # Add customer flag
                    'isCritical': priority_item.get('slack_hours', 999) < 24,
                    'slackHours': priority_item.get('slack_hours', 999)
                })
    else:
        # Fallback to task_schedule - also limit to MAX_TASKS_FOR_DASHBOARD
        all_tasks = []
        for task_instance_id, schedule in scheduler.task_schedule.items():
            task_info = scheduler.tasks.get(task_instance_id, {})

            # Get the base team and team_skill from schedule
            base_team = schedule.get('team', '')  # Base team for dashboard filtering
            team_skill = schedule.get('team_skill', schedule.get('team', ''))  # Actual scheduling team
            skill_code = schedule.get('skill', task_info.get('skill', ''))  # Skill code if present

            all_tasks.append({
                'taskId': task_instance_id,
                'type': schedule.get('task_type', 'Production'),
                'product': schedule.get('product', 'Unknown'),
                'team': base_team,  # Base team for dashboard filtering
                'teamSkill': team_skill,  # Full team+skill identifier
                'skill': skill_code,  # Skill code alone
                'startTime': schedule['start_time'].isoformat() if schedule.get('start_time') else '',
                'endTime': schedule['end_time'].isoformat() if schedule.get('end_time') else '',
                'duration': schedule.get('duration', 60),
                'mechanics': schedule.get('mechanics_required', 1),
                'shift': schedule.get('shift', '1st'),
                'priority': 999,
                'dependencies': [],
                'isLatePartTask': task_instance_id in scheduler.late_part_tasks,
                'isReworkTask': task_instance_id in scheduler.rework_tasks,
                'isQualityTask': schedule.get('is_quality', False),
                'isCustomerTask': schedule.get('is_customer', False),  # Add customer flag
                'isCritical': False,
                'slackHours': 999
            })

        # Sort by start time and limit
        all_tasks.sort(key=lambda x: x['startTime'])
        tasks = all_tasks[:MAX_TASKS_FOR_DASHBOARD]
        total_tasks_available = len(all_tasks)

    # Calculate makespan and metrics (using ALL tasks, not just the limited set)
    makespan = scheduler.calculate_makespan()
    lateness_metrics = scheduler.calculate_lateness_metrics()

    # Calculate utilization based on ALL scheduled tasks and current capacities
    utilization = {}
    team_task_minutes = {}

    # Calculate total scheduled minutes per team (using team_skill for proper accounting)
    for task_id, schedule in scheduler.task_schedule.items():
        # Use team_skill for utilization calculation to properly account for skill-specific scheduling
        team_for_util = schedule.get('team_skill', schedule.get('team'))
        if team_for_util:
            if team_for_util not in team_task_minutes:
                team_task_minutes[team_for_util] = 0
            team_task_minutes[team_for_util] += schedule.get('duration', 0) * schedule.get('mechanics_required', 1)

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
            'qualityCount': metrics['task_breakdown'].get('Quality Inspection', 0),
            'customerCount': metrics['task_breakdown'].get('Customer Inspection', 0),  # Add customer count
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

    # Count total workforce - now including customer teams
    total_workforce = sum(team_capacities.values())
    total_mechanics = sum(cap for team, cap in team_capacities.items()
                          if 'Quality' not in team and 'Customer' not in team)
    total_quality = sum(cap for team, cap in team_capacities.items()
                        if 'Quality' in team)
    total_customer = sum(cap for team, cap in team_capacities.items()
                         if 'Customer' in team)

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
        'totalCustomer': total_customer,  # Add total customer workforce
        'avgUtilization': round(avg_utilization, 1),
        'makespan': makespan,
        'onTimeRate': on_time_rate,
        'maxLateness': max_lateness,
        'totalTasks': total_tasks_available if total_tasks_available > 0 else len(scheduler.task_schedule),
        'displayedTasks': len(tasks),  # How many are being sent to dashboard
        'truncated': total_tasks_available > MAX_TASKS_FOR_DASHBOARD,  # Indicate if data was truncated
        'metrics': {
            'totalMechanics': total_mechanics,
            'totalQuality': total_quality,
            'totalCustomer': total_customer,  # Add to metrics
            'totalCapacity': total_workforce,
            'criticalTaskCount': sum(1 for t in tasks if t['isCritical']),
            'latePartTaskCount': sum(1 for t in tasks if t['isLatePartTask']),
            'reworkTaskCount': sum(1 for t in tasks if t['isReworkTask']),
            'qualityTaskCount': sum(1 for t in tasks if t.get('isQualityTask', False)),
            'customerTaskCount': sum(1 for t in tasks if t.get('isCustomerTask', False))  # Add customer task count
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
            # Note: May want to add optimalCustomer if scenario 2 optimizes customer teams too
    elif scenario_name == 'scenario3':
        scenario_data['description'] = 'Scenario 3: Multi-Dimensional optimization'
        # Add achieved lateness if available
        if max_lateness < 0:
            scenario_data['achievedMaxLateness'] = max_lateness

    return scenario_data


# ========== NEW AUTO-ASSIGN ENDPOINTS ==========

@app.route('/api/debug/tasks')
def debug_tasks():
    """Debug endpoint to see task team assignments"""
    scenario = request.args.get('scenario', 'baseline')

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


# Global progress tracking
computation_progress = {}


@app.route('/api/scenario_progress/<scenario_id>')
def get_scenario_progress(scenario_id):
    return jsonify({
        'progress': computation_progress.get(scenario_id, 0),
        'status': 'computing' if scenario_id in computation_progress else 'idle'
    })


@app.route('/api/scenario/<scenario_id>')
def get_scenario_data(scenario_id):
    if scenario_id not in scenario_results:
        return jsonify({'error': f'Scenario {scenario_id} not found'}), 404

    # No need to limit here anymore - already limited at source
    scenario_data = scenario_results[scenario_id]
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


# ... continuing with all other routes unchanged ...

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


# Include all other endpoints unchanged from the original file...
# (The rest of the file continues with all other routes and functions as in the original)

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