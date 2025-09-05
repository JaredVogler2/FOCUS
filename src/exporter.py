# src/exporter.py

from datetime import datetime

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
