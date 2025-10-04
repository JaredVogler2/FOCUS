# src/scheduler/scenarios.py

from collections import defaultdict
import copy
import random
import math
from typing import TYPE_CHECKING
import re
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from .main import ProductionScheduler

def scenario_1_csv_headcount(scheduler, time_limit_seconds=60):
    """
    Scenario 1: Find an optimal schedule using fixed, CSV-defined resources.
    This scenario uses the CP-SAT solver to minimize total project lateness
    given the resource constraints from the input files.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 1: Optimal Schedule with Fixed CSV Headcount (CP-SAT)")
    print("=" * 80)

    from . import metrics, algorithms
    model = cp_model.CpModel()

    # --- Time Conversion Setup ---
    MINUTES_PER_DAY = 8 * 60
    horizon = sum(task.get('duration', 0) for task in scheduler.tasks.values()) + len(scheduler.tasks) * 2 * MINUTES_PER_DAY
    if not scheduler.on_dock_dates: return None
    project_start_date = min(d for d in scheduler.on_dock_dates.values() if d is not None)

    minutes_to_date_map = {}
    date_to_minutes_map = {}
    generic_product_line = list(scheduler.delivery_dates.keys())[0] if scheduler.delivery_dates else None

    if generic_product_line:
        cumulative_minutes = 0
        for day in range(365 * 5):
            current_date = project_start_date + timedelta(days=day)
            if scheduler.is_working_day(current_date, generic_product_line):
                day_start_time = current_date.replace(hour=6, minute=0, second=0, microsecond=0)
                date_to_minutes_map[current_date.date()] = cumulative_minutes
                for minute_of_day in range(MINUTES_PER_DAY):
                    exact_time = day_start_time + timedelta(minutes=minute_of_day)
                    minutes_to_date_map[cumulative_minutes + minute_of_day] = exact_time
                cumulative_minutes += MINUTES_PER_DAY

    def date_to_minutes(d):
        current_d = d.date()
        while current_d not in date_to_minutes_map:
            current_d += timedelta(days=1)
            if (current_d - d.date()).days > 365: return horizon
        return date_to_minutes_map[current_d]

    def minutes_to_date(m): return minutes_to_date_map.get(m, project_start_date)

    # --- Task Interval Variables ---
    tasks = scheduler.tasks
    task_intervals = {}
    mechanic_blocking_intervals = {}

    for task_id, task_info in tasks.items():
        duration = task_info.get('duration', 0) or 60
        start_var = model.NewIntVar(0, horizon, f'start_{task_id}')
        end_var = model.NewIntVar(0, horizon, f'end_{task_id}')
        interval = model.NewIntervalVar(start_var, duration, end_var, f'interval_{task_id}')
        task_intervals[task_id] = interval

        if task_info.get('task_type') in ['Production', 'Rework', 'Late Part'] and task_id in scheduler.quality_requirements:
            qi_task_id = scheduler.quality_requirements[task_id]
            if qi_task_id in tasks:
                qi_duration = tasks[qi_task_id].get('duration', 0)
                blocking_duration = duration + qi_duration
                blocking_end_var = model.NewIntVar(0, horizon, f'blocking_end_{task_id}')
                blocking_interval = model.NewIntervalVar(start_var, blocking_duration, blocking_end_var, f'blocking_interval_{task_id}')
                mechanic_blocking_intervals[task_id] = blocking_interval

    # --- Resource Modeling (Use original capacities, not optimized vars) ---
    team_capacities = {**scheduler._original_team_capacity,
                       **scheduler._original_quality_capacity,
                       **scheduler._original_customer_team_capacity}

    # --- Constraints ---
    # 1. Precedence Constraints
    dynamic_constraints = scheduler.build_dynamic_dependencies()
    for constraint in dynamic_constraints:
        pred_id, succ_id = constraint.get('First'), constraint.get('Second')
        if pred_id in task_intervals and succ_id in task_intervals:
            model.Add(task_intervals[succ_id].StartExpr() >= task_intervals[pred_id].EndExpr())

    # 2. Late Part Constraints
    for lp_constraint in scheduler.late_part_constraints:
        pred_id = lp_constraint.get('First')
        if pred_id in task_intervals and pred_id in scheduler.on_dock_dates:
            on_dock_date = scheduler.on_dock_dates[pred_id]
            earliest_start_date = on_dock_date + timedelta(days=scheduler.late_part_delay_days)
            earliest_start_minutes = date_to_minutes(earliest_start_date) + MINUTES_PER_DAY - 1
            model.Add(task_intervals[pred_id].StartExpr() >= earliest_start_minutes)
        try:
            succ_str = str(lp_constraint.get('Second'))
            succ_baseline_id = int(re.findall(r'\d+', succ_str)[0])
            product = lp_constraint.get('Product_Line')
            if product:
                succ_id = scheduler.task_instance_map.get((product, succ_baseline_id))
                if pred_id in task_intervals and succ_id in task_intervals:
                    model.Add(task_intervals[succ_id].StartExpr() >= task_intervals[pred_id].EndExpr())
        except (ValueError, IndexError):
            pass

    # 3. Resource Cumulative Constraints
    for team, capacity in team_capacities.items():
        demands = []
        intervals_for_team = []
        is_quality_team = team in scheduler._original_quality_capacity
        is_customer_team = team in scheduler._original_customer_team_capacity

        for task_id, task_info in tasks.items():
            task_assigned_team = None
            # Check team type based on the outer loop's 'team' variable
            if is_quality_team and task_info.get('is_quality', False):
                task_assigned_team = task_info.get('team')
            elif is_customer_team and task_info.get('is_customer', False):
                task_assigned_team = task_info.get('team')
            elif not is_quality_team and not is_customer_team: # It's a mechanic team
                task_assigned_team = task_info.get('team_skill')

            if task_assigned_team == team:
                demands.append(task_info.get('mechanics_required', 1))
                # Use the same condition to check if it's a mechanic team for blocking intervals
                if not is_quality_team and not is_customer_team and task_id in mechanic_blocking_intervals:
                    intervals_for_team.append(mechanic_blocking_intervals[task_id])
                else:
                    intervals_for_team.append(task_intervals[task_id])

        if intervals_for_team:
            model.AddCumulative(intervals_for_team, demands, capacity)


    # --- Objective Function (Minimize Total Lateness) ---
    lateness_vars = []
    product_final_tasks = defaultdict(list)
    for task_id, task_info in tasks.items():
        if not scheduler.get_successors(task_id):
            if product := scheduler.instance_to_product.get(task_id):
                product_final_tasks[product].append(task_id)

    for product, final_tasks in product_final_tasks.items():
        if (delivery_date := scheduler.delivery_dates.get(product)) and final_tasks:
            due_date_minutes = date_to_minutes(delivery_date)
            product_completion_var = model.NewIntVar(0, horizon, f'completion_{product}')
            model.AddMaxEquality(product_completion_var, [task_intervals[tid].EndExpr() for tid in final_tasks])
            lateness = model.NewIntVar(0, horizon, f'lateness_{product}')
            model.AddDivisionEquality(lateness, product_completion_var - due_date_minutes, MINUTES_PER_DAY)
            lateness_vars.append(lateness)

    total_lateness_days = model.NewIntVar(0, horizon, 'total_lateness_days')
    model.Add(total_lateness_days == sum(lateness_vars)) if lateness_vars else model.Add(total_lateness_days == 0)

    model.Minimize(total_lateness_days)

    # --- Solve ---
    print(f"Starting CP-SAT solver with a time limit of {time_limit_seconds} seconds...")
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.max_time_in_seconds = time_limit_seconds
    status = solver.Solve(model)

    # --- Result Extraction ---
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Solver finished with status: {solver.StatusName(status)}")
        scheduler.task_schedule.clear()

        # Restore original capacities to reflect the fixed nature of this scenario
        scheduler.team_capacity = scheduler._original_team_capacity.copy()
        scheduler.quality_team_capacity = scheduler._original_quality_capacity.copy()
        scheduler.customer_team_capacity = scheduler._original_customer_team_capacity.copy()

        for task_id, interval in task_intervals.items():
            task_info = scheduler.tasks[task_id]
            assigned_team = task_info.get('team', '')
            if not task_info.get('is_quality') and not task_info.get('is_customer') and task_info.get('team_skill'):
                assigned_team = task_info.get('team_skill')

            scheduler.task_schedule[task_id] = {
                'task_id': task_id, 'start_time': minutes_to_date(solver.Value(interval.StartExpr())),
                'end_time': minutes_to_date(solver.Value(interval.EndExpr())),
                'duration': task_info.get('duration', 0),
                'team': assigned_team,
                'team_skill': task_info.get('team_skill', ''),
                'mechanics_required': task_info.get('mechanics_required', 1), 'product': task_info.get('product', 'Unknown'),
                'original_task_id': task_info.get('original_task_id', task_id), 'task_type': task_info.get('task_type', 'Production'),
                'skill': task_info.get('skill', ''), 'shift': '1st',
                'is_quality': task_info.get('is_quality', False),
                'is_customer': task_info.get('is_customer', False),
            }

        priority_data = []
        for task_id, schedule in scheduler.task_schedule.items():
            slack_hours = metrics.calculate_slack_time(scheduler, task_id)
            criticality = algorithms.classify_task_criticality(scheduler, task_id)
            task_info = scheduler.tasks.get(task_id, {})
            task_type = task_info.get('task_type', 'Production')
            product = task_info.get('product', 'Unknown')
            original_task_id = scheduler.instance_to_original_task.get(task_id)

            if task_type == 'Quality Inspection':
                primary_task = scheduler.quality_inspections.get(task_id, {}).get('primary_task')
                if primary_task:
                    primary_original = scheduler.instance_to_original_task.get(primary_task, primary_task)
                    display_name = f"{product} QI for Task {primary_original}"
                else:
                    display_name = f"{product} QI {original_task_id}"
            elif task_type == 'Late Part':
                display_name = f"{product} Late Part {original_task_id}"
            elif task_type == 'Rework':
                display_name = f"{product} Rework {original_task_id}"
            else:
                display_name = f"{product} Task {original_task_id}"

            criticality_symbol = {'CRITICAL': '🔴', 'BUFFER': '🟡', 'FLEXIBLE': '🟢'}.get(criticality, '')
            display_name_with_criticality = f"{criticality_symbol} {display_name} [{criticality}]"

            priority_data.append({
                'task_instance_id': task_id,
                'task_type': task_type,
                'display_name': display_name,
                'display_name_with_criticality': display_name_with_criticality,
                'criticality': criticality,
                'product_line': product,
                'original_task_id': original_task_id,
                'team': schedule.get('team'),
                'scheduled_start': schedule.get('start_time'),
                'scheduled_end': schedule.get('end_time'),
                'duration_minutes': schedule.get('duration'),
                'mechanics_required': schedule.get('mechanics_required'),
                'slack_hours': slack_hours,
                'slack_days': slack_hours / 24 if slack_hours != float('inf') else float('inf'),
                'priority_score': algorithms.calculate_task_priority(scheduler, task_id),
                'shift': schedule.get('shift', 'N/A')
            })
        priority_data.sort(key=lambda x: (x['scheduled_start'], x['slack_hours']))
        for i, task in enumerate(priority_data, 1): task['global_priority'] = i
        scheduler.global_priority_list = priority_data

        makespan = scheduler.calculate_makespan()
        metrics = scheduler.calculate_lateness_metrics()
        total_late_days_val = sum(d.get('lateness_days', 0) for d in metrics.values() if d.get('lateness_days', 0) > 0)

        print(f"\nSCENARIO 1 OPTIMIZATION COMPLETE: Lateness={total_late_days_val} days")
        print(f"Makespan: {makespan} working days")

        return {
            'makespan': makespan,
            'metrics': metrics,
            'priority_list': priority_data,
            'team_capacities': dict(scheduler.team_capacity),
            'quality_capacities': dict(scheduler.quality_team_capacity),
            'total_late_days': total_late_days_val,
            'status': 'SUCCESS'
        }
    else:
        print(f"Solver could not find a solution for Scenario 1. Status: {solver.StatusName(status)}")
        return {'status': 'FAILED', 'makespan': 0, 'metrics': {}, 'priority_list': [], 'team_capacities': {}, 'quality_capacities': {}, 'total_late_days': 0}


def scenario_3_optimal_schedule(scheduler, time_limit_seconds=90):
    """
    Scenario 3: Find an optimal schedule and resource allocation using CP-SAT.
    This scenario simplifies the resource model to match the validation script.
    It creates resources for BASE teams only and constrains task assignments against them.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 3: Optimal Schedule and Resource Allocation (CP-SAT)")
    print("=" * 80)

    model = cp_model.CpModel()

    # --- Time Conversion Setup ---
    MINUTES_PER_DAY = 8 * 60
    horizon = sum(task.get('duration', 0) for task in scheduler.tasks.values()) + len(scheduler.tasks) * 2 * MINUTES_PER_DAY
    if not scheduler.on_dock_dates: return None
    project_start_date = min(d for d in scheduler.on_dock_dates.values() if d is not None)

    minutes_to_date_map = {}
    date_to_minutes_map = {}
    generic_product_line = list(scheduler.delivery_dates.keys())[0] if scheduler.delivery_dates else None

    if generic_product_line:
        cumulative_minutes = 0
        for day in range(365 * 5):
            current_date = project_start_date + timedelta(days=day)
            if scheduler.is_working_day(current_date, generic_product_line):
                day_start_time = current_date.replace(hour=6, minute=0, second=0, microsecond=0)
                date_to_minutes_map[current_date.date()] = cumulative_minutes
                for minute_of_day in range(MINUTES_PER_DAY):
                    exact_time = day_start_time + timedelta(minutes=minute_of_day)
                    minutes_to_date_map[cumulative_minutes + minute_of_day] = exact_time
                cumulative_minutes += MINUTES_PER_DAY

    def date_to_minutes(d):
        current_d = d.date()
        while current_d not in date_to_minutes_map:
            current_d += timedelta(days=1)
            if (current_d - d.date()).days > 365: return horizon
        return date_to_minutes_map[current_d]

    def minutes_to_date(m): return minutes_to_date_map.get(m, project_start_date)

    # --- Task Interval Variables ---
    tasks = scheduler.tasks
    task_intervals = {}
    mechanic_blocking_intervals = {}

    for task_id, task_info in tasks.items():
        duration = task_info.get('duration', 0) or 60
        start_var = model.NewIntVar(0, horizon, f'start_{task_id}')
        end_var = model.NewIntVar(0, horizon, f'end_{task_id}')
        interval = model.NewIntervalVar(start_var, duration, end_var, f'interval_{task_id}')
        task_intervals[task_id] = interval

        if task_info.get('task_type') in ['Production', 'Rework', 'Late Part'] and task_id in scheduler.quality_requirements:
            qi_task_id = scheduler.quality_requirements[task_id]
            if qi_task_id in tasks:
                qi_duration = tasks[qi_task_id].get('duration', 0)
                blocking_duration = duration + qi_duration
                blocking_end_var = model.NewIntVar(0, horizon, f'blocking_end_{task_id}')
                blocking_interval = model.NewIntervalVar(start_var, blocking_duration, blocking_end_var, f'blocking_interval_{task_id}')
                mechanic_blocking_intervals[task_id] = blocking_interval

    # --- Resource Modeling (Skill & Quality Specific) ---
    mechanic_skill_teams = sorted([team for team in scheduler.team_capacity if ' (Skill ' in team])
    quality_teams = sorted(list(scheduler.quality_team_capacity.keys()))
    customer_teams = sorted(list(scheduler.customer_team_capacity.keys()))
    all_resource_teams = mechanic_skill_teams + quality_teams + customer_teams

    team_max_requirement = defaultdict(lambda: 1)
    for task_id, task_info in tasks.items():
        mechanics_required = task_info.get('mechanics_required', 1)
        assigned_team = None
        if task_info.get('is_quality'):
            assigned_team = task_info.get('team')
        elif task_info.get('is_customer'):
            assigned_team = task_info.get('team')
        else: # Mechanic task
            assigned_team = task_info.get('team_skill')

        if assigned_team:
            team_max_requirement[assigned_team] = max(team_max_requirement[assigned_team], mechanics_required)

    team_capacity_vars = {}
    for team in all_resource_teams:
        min_req = team_max_requirement.get(team, 1)
        team_capacity_vars[team] = model.NewIntVar(min_req, 100, f'capacity_{team}')

    # --- Constraints ---
    # 1. Precedence Constraints
    dynamic_constraints = scheduler.build_dynamic_dependencies()
    for constraint in dynamic_constraints:
        pred_id, succ_id = constraint.get('First'), constraint.get('Second')
        if pred_id in task_intervals and succ_id in task_intervals:
            model.Add(task_intervals[succ_id].StartExpr() >= task_intervals[pred_id].EndExpr())

    # 2. Late Part Constraints
    for lp_constraint in scheduler.late_part_constraints:
        pred_id = lp_constraint.get('First')
        if pred_id in task_intervals and pred_id in scheduler.on_dock_dates:
            on_dock_date = scheduler.on_dock_dates[pred_id]
            earliest_start_date = on_dock_date + timedelta(days=scheduler.late_part_delay_days)
            earliest_start_minutes = date_to_minutes(earliest_start_date) + MINUTES_PER_DAY - 1
            model.Add(task_intervals[pred_id].StartExpr() >= earliest_start_minutes)
        try:
            succ_str = str(lp_constraint.get('Second'))
            succ_baseline_id = int(re.findall(r'\d+', succ_str)[0])
            product = lp_constraint.get('Product_Line')
            if product:
                succ_id = scheduler.task_instance_map.get((product, succ_baseline_id))
                if pred_id in task_intervals and succ_id in task_intervals:
                    model.Add(task_intervals[succ_id].StartExpr() >= task_intervals[pred_id].EndExpr())
        except (ValueError, IndexError):
            print(f"[WARNING] Could not parse successor ID for late part constraint: {lp_constraint}")

    # 3. Resource Cumulative Constraints
    for team in all_resource_teams:
        demands = []
        intervals_for_team = []
        is_mechanic_team = team in mechanic_skill_teams

        for task_id, task_info in tasks.items():
            assigned_team = None
            if task_info.get('is_quality'):
                assigned_team = task_info.get('team')
            elif task_info.get('is_customer'):
                assigned_team = task_info.get('team')
            else: # is mechanic task
                assigned_team = task_info.get('team_skill')

            if assigned_team == team:
                demands.append(task_info.get('mechanics_required', 1))
                if is_mechanic_team and task_id in mechanic_blocking_intervals:
                    intervals_for_team.append(mechanic_blocking_intervals[task_id])
                else:
                    intervals_for_team.append(task_intervals[task_id])

        if intervals_for_team:
            model.AddCumulative(intervals_for_team, demands, team_capacity_vars[team])

    # --- Objective Function ---
    total_workforce = model.NewIntVar(0, 100 * len(all_resource_teams), 'total_workforce')
    model.Add(total_workforce == sum(team_capacity_vars.values()))

    lateness_vars = []
    product_final_tasks = defaultdict(list)
    for task_id, task_info in tasks.items():
        if not scheduler.get_successors(task_id):
            if product := scheduler.instance_to_product.get(task_id):
                product_final_tasks[product].append(task_id)

    for product, final_tasks in product_final_tasks.items():
        if (delivery_date := scheduler.delivery_dates.get(product)) and final_tasks:
            due_date_minutes = date_to_minutes(delivery_date)
            product_completion_var = model.NewIntVar(0, horizon, f'completion_{product}')
            model.AddMaxEquality(product_completion_var, [task_intervals[tid].EndExpr() for tid in final_tasks])
            lateness = model.NewIntVar(0, horizon, f'lateness_{product}')
            model.AddDivisionEquality(lateness, product_completion_var - due_date_minutes, MINUTES_PER_DAY)
            lateness_vars.append(lateness)

    total_lateness_days = model.NewIntVar(0, horizon, 'total_lateness_days')
    model.Add(total_lateness_days == sum(lateness_vars)) if lateness_vars else model.Add(total_lateness_days == 0)

    model.Minimize(10 * total_lateness_days + 1 * total_workforce)

    # --- Solve ---
    print(f"Starting CP-SAT solver with a time limit of {time_limit_seconds} seconds...")
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.max_time_in_seconds = time_limit_seconds
    status = solver.Solve(model)

    # --- Result Extraction ---
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Solver finished with status: {solver.StatusName(status)}")
        scheduler.task_schedule.clear()

        scheduler.team_capacity.clear()
        scheduler.quality_team_capacity.clear()
        scheduler.customer_team_capacity.clear()

        for team, cap_var in team_capacity_vars.items():
            optimized_capacity = solver.Value(cap_var)
            if team in scheduler._original_team_capacity:
                scheduler.team_capacity[team] = optimized_capacity
            elif team in scheduler._original_quality_capacity:
                scheduler.quality_team_capacity[team] = optimized_capacity
            elif team in scheduler._original_customer_team_capacity:
                scheduler.customer_team_capacity[team] = optimized_capacity

        # **FIX START: Populate schedule with the correct team name for validation**
        for task_id, interval in task_intervals.items():
            task_info = scheduler.tasks[task_id]

            assigned_team = task_info.get('team', '')
            if not task_info.get('is_quality') and not task_info.get('is_customer') and task_info.get('team_skill'):
                assigned_team = task_info.get('team_skill')

            scheduler.task_schedule[task_id] = {
                'task_id': task_id, 'start_time': minutes_to_date(solver.Value(interval.StartExpr())),
                'end_time': minutes_to_date(solver.Value(interval.EndExpr())),
                'duration': task_info.get('duration', 0),
                'team': assigned_team,  # Use the corrected team name
                'team_skill': task_info.get('team_skill', ''),
                'mechanics_required': task_info.get('mechanics_required', 1), 'product': task_info.get('product', 'Unknown'),
                'original_task_id': task_info.get('original_task_id', task_id), 'task_type': task_info.get('task_type', 'Production'),
                'skill': task_info.get('skill', ''), 'shift': '1st',
                'is_quality': task_info.get('is_quality', False),
                'is_customer': task_info.get('is_customer', False),
            }
        # **FIX END**

        priority_data = []
        for task_id, schedule in scheduler.task_schedule.items():
            task_info = scheduler.tasks.get(task_id, {})
            priority_data.append({ 'task_instance_id': task_id, 'task_type': task_info.get('task_type', 'Production'),
                'product_line': task_info.get('product', 'Unknown'), 'scheduled_start': schedule['start_time'], 'slack_hours': 999 })
        priority_data.sort(key=lambda x: x['scheduled_start'])
        for i, task in enumerate(priority_data, 1): task['global_priority'] = i
        scheduler.global_priority_list = priority_data

        print(f"\nSCENARIO 3 OPTIMIZATION COMPLETE: Lateness={solver.Value(total_lateness_days)} days, Workforce={solver.Value(total_workforce)} people")

        print("\n" + "-" * 40 + "\nOptimized Workforce Breakdown:\n" + "-" * 40)
        makespan = scheduler.calculate_makespan()
        team_work_minutes = defaultdict(float)
        for task_id, schedule in scheduler.task_schedule.items():
            team = schedule.get('team') # Use 'team' as it's now corrected
            if team: team_work_minutes[team] += schedule.get('duration', 0) * schedule.get('mechanics_required', 1)

        all_optimized_teams = {**scheduler.team_capacity, **scheduler.quality_team_capacity, **scheduler.customer_team_capacity}
        by_shift = defaultdict(lambda: defaultdict(list))
        for team, capacity in all_optimized_teams.items():
            if capacity > 0:
                shifts = scheduler.team_shifts.get(team) or scheduler.quality_team_shifts.get(team) or scheduler.customer_team_shifts.get(team, ['Undefined'])
                shift_str = ', '.join(shifts) if shifts else 'Undefined'
                team_type = "Mechanic"
                if team in scheduler.quality_team_capacity: team_type = "Quality"
                elif team in scheduler.customer_team_capacity: team_type = "Customer"
                total_work = team_work_minutes.get(team, 0.0)
                available_minutes = capacity * makespan * 8 * 60
                utilization = (total_work / available_minutes) * 100 if available_minutes > 0 else 0
                by_shift[shift_str][team_type].append(f"  - {team}: {capacity} people ({utilization:.1f}% utilization)")

        for shift, types in sorted(by_shift.items()):
            print(f"\nShift: {shift}")
            for team_type, teams in sorted(types.items()):
                print(f" {team_type} Teams:")
                for team_line in sorted(teams): print(team_line)
        print("-" * 40)

        return {'status': 'SUCCESS'}
    else:
        print(f"Solver could not find a solution. Status: {solver.StatusName(status)}")
        return None


def run_what_if_scenario(scheduler, prioritized_product, time_limit_seconds=60):
    """
    Scenario "What-If": Prioritize a specific product and see the impact.
    This is a modification of scenario_3_optimal_schedule, but uses fixed resources.
    """
    print("\n" + "=" * 80)
    print(f"SCENARIO WHAT-IF: Prioritizing {prioritized_product}")
    print("=" * 80)

    model = cp_model.CpModel()

    # --- Time Conversion Setup ---
    MINUTES_PER_DAY = 8 * 60
    horizon = sum(task.get('duration', 0) for task in scheduler.tasks.values()) + len(scheduler.tasks) * 2 * MINUTES_PER_DAY
    if not scheduler.on_dock_dates: return None
    project_start_date = min(d for d in scheduler.on_dock_dates.values() if d is not None)

    minutes_to_date_map = {}
    date_to_minutes_map = {}
    generic_product_line = list(scheduler.delivery_dates.keys())[0] if scheduler.delivery_dates else None

    if generic_product_line:
        cumulative_minutes = 0
        for day in range(365 * 5):
            current_date = project_start_date + timedelta(days=day)
            if scheduler.is_working_day(current_date, generic_product_line):
                day_start_time = current_date.replace(hour=6, minute=0, second=0, microsecond=0)
                date_to_minutes_map[current_date.date()] = cumulative_minutes
                for minute_of_day in range(MINUTES_PER_DAY):
                    exact_time = day_start_time + timedelta(minutes=minute_of_day)
                    minutes_to_date_map[cumulative_minutes + minute_of_day] = exact_time
                cumulative_minutes += MINUTES_PER_DAY

    def date_to_minutes(d):
        current_d = d.date()
        while current_d not in date_to_minutes_map:
            current_d += timedelta(days=1)
            if (current_d - d.date()).days > 365:
                return horizon
        return date_to_minutes_map[current_d]

    def minutes_to_date(m): return minutes_to_date_map.get(m, project_start_date)

    # --- Task Interval Variables ---
    tasks = scheduler.tasks
    task_intervals = {}
    mechanic_blocking_intervals = {}

    for task_id, task_info in tasks.items():
        duration = task_info.get('duration', 0) or 60
        start_var = model.NewIntVar(0, horizon, f'start_{task_id}')
        end_var = model.NewIntVar(0, horizon, f'end_{task_id}')
        interval = model.NewIntervalVar(start_var, duration, end_var, f'interval_{task_id}')
        task_intervals[task_id] = interval

        if task_info.get('task_type') in ['Production', 'Rework', 'Late Part'] and task_id in scheduler.quality_requirements:
            qi_task_id = scheduler.quality_requirements[task_id]
            if qi_task_id in tasks:
                qi_duration = tasks[qi_task_id].get('duration', 0)
                blocking_duration = duration + qi_duration
                blocking_end_var = model.NewIntVar(0, horizon, f'blocking_end_{task_id}')
                blocking_interval = model.NewIntervalVar(start_var, blocking_duration, blocking_end_var, f'blocking_interval_{task_id}')
                mechanic_blocking_intervals[task_id] = blocking_interval

    # --- Resource Modeling (Use original capacities, not optimized vars) ---
    team_capacities = {**scheduler._original_team_capacity,
                       **scheduler._original_quality_capacity,
                       **scheduler._original_customer_team_capacity}

    # --- Constraints ---
    # 1. Precedence Constraints
    dynamic_constraints = scheduler.build_dynamic_dependencies()
    for constraint in dynamic_constraints:
        pred_id, succ_id = constraint.get('First'), constraint.get('Second')
        if pred_id in task_intervals and succ_id in task_intervals:
            model.Add(task_intervals[succ_id].StartExpr() >= task_intervals[pred_id].EndExpr())

    # 2. Late Part Constraints
    for lp_constraint in scheduler.late_part_constraints:
        pred_id = lp_constraint.get('First')
        if pred_id in task_intervals and pred_id in scheduler.on_dock_dates:
            on_dock_date = scheduler.on_dock_dates[pred_id]
            earliest_start_date = on_dock_date + timedelta(days=scheduler.late_part_delay_days)
            earliest_start_minutes = date_to_minutes(earliest_start_date) + MINUTES_PER_DAY - 1
            model.Add(task_intervals[pred_id].StartExpr() >= earliest_start_minutes)
        try:
            succ_str = str(lp_constraint.get('Second'))
            succ_baseline_id = int(re.findall(r'\d+', succ_str)[0])
            product = lp_constraint.get('Product_Line')
            if product:
                succ_id = scheduler.task_instance_map.get((product, succ_baseline_id))
                if pred_id in task_intervals and succ_id in task_intervals:
                    model.Add(task_intervals[succ_id].StartExpr() >= task_intervals[pred_id].EndExpr())
        except (ValueError, IndexError):
            pass

    # 3. Resource Cumulative Constraints
    for team, capacity in team_capacities.items():
        demands = []
        intervals_for_team = []
        is_quality_team = team in scheduler._original_quality_capacity
        is_customer_team = team in scheduler._original_customer_team_capacity

        for task_id, task_info in tasks.items():
            task_assigned_team = None
            if is_quality_team and task_info.get('is_quality', False):
                task_assigned_team = task_info.get('team')
            elif is_customer_team and task_info.get('is_customer', False):
                task_assigned_team = task_info.get('team')
            elif not is_quality_team and not is_customer_team:
                task_assigned_team = task_info.get('team_skill')

            if task_assigned_team == team:
                demands.append(task_info.get('mechanics_required', 1))
                if not is_quality_team and not is_customer_team and task_id in mechanic_blocking_intervals:
                    intervals_for_team.append(mechanic_blocking_intervals[task_id])
                else:
                    intervals_for_team.append(task_intervals[task_id])

        if intervals_for_team:
            model.AddCumulative(intervals_for_team, demands, capacity)

    # --- Objective Function (MODIFIED FOR WHAT-IF) ---
    product_final_tasks = defaultdict(list)
    for task_id, task_info in tasks.items():
        if not scheduler.get_successors(task_id):
            if product := scheduler.instance_to_product.get(task_id):
                product_final_tasks[product].append(task_id)

    # 1. Prioritized Product Completion Time
    prioritized_product_completion_var = model.NewIntVar(0, horizon, f'completion_{prioritized_product}')
    if prioritized_product in product_final_tasks:
        final_tasks_for_product = product_final_tasks[prioritized_product]
        model.AddMaxEquality(prioritized_product_completion_var, [task_intervals[tid].EndExpr() for tid in final_tasks_for_product])
    else:
        model.Add(prioritized_product_completion_var == 0)

    # 2. Lateness for all OTHER products
    other_lateness_vars = []
    for product, final_tasks in product_final_tasks.items():
        if product == prioritized_product:
            continue

        if (delivery_date := scheduler.delivery_dates.get(product)) and final_tasks:
            due_date_minutes = date_to_minutes(delivery_date)
            product_completion_var = model.NewIntVar(0, horizon, f'completion_{product}')
            model.AddMaxEquality(product_completion_var, [task_intervals[tid].EndExpr() for tid in final_tasks])
            lateness = model.NewIntVar(0, horizon, f'lateness_{product}')
            model.AddDivisionEquality(lateness, product_completion_var - due_date_minutes, MINUTES_PER_DAY)
            other_lateness_vars.append(lateness)

    total_other_lateness_days = model.NewIntVar(0, horizon, 'total_other_lateness_days')
    if other_lateness_vars:
        model.Add(total_other_lateness_days == sum(other_lateness_vars))
    else:
        model.Add(total_other_lateness_days == 0)

    # 3. Combine objectives with weights
    model.Minimize(1000 * prioritized_product_completion_var + 10 * total_other_lateness_days)

    # --- Solve ---
    print(f"Starting CP-SAT solver for What-If with a time limit of {time_limit_seconds} seconds...")
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.max_time_in_seconds = time_limit_seconds
    status = solver.Solve(model)

    # --- Result Extraction ---
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Solver finished with status: {solver.StatusName(status)}")

        temp_scheduler = copy.deepcopy(scheduler)
        temp_scheduler.task_schedule.clear()

        temp_scheduler.team_capacity = scheduler._original_team_capacity.copy()
        temp_scheduler.quality_team_capacity = scheduler._original_quality_capacity.copy()
        temp_scheduler.customer_team_capacity = scheduler._original_customer_team_capacity.copy()

        for task_id, interval in task_intervals.items():
            task_info = temp_scheduler.tasks[task_id]
            temp_scheduler.task_schedule[task_id] = {
                'task_id': task_id, 'start_time': minutes_to_date(solver.Value(interval.StartExpr())),
                'end_time': minutes_to_date(solver.Value(interval.EndExpr())),
                'duration': task_info.get('duration', 0), 'team': task_info.get('team', ''),
                'team_skill': task_info.get('team_skill', ''),
                'mechanics_required': task_info.get('mechanics_required', 1), 'product': task_info.get('product', 'Unknown'),
                'original_task_id': task_info.get('original_task_id', task_id), 'task_type': task_info.get('task_type', 'Production'),
                'skill': task_info.get('skill', ''), 'shift': '1st', 'is_quality': task_info.get('is_quality', False),
                'is_customer': task_info.get('is_customer', False),
            }

        priority_data = []
        for task_id, schedule in temp_scheduler.task_schedule.items():
            task_info = temp_scheduler.tasks.get(task_id, {})
            priority_data.append({
                'task_instance_id': task_id, 'task_type': task_info.get('task_type', 'Production'),
                'product_line': task_info.get('product', 'Unknown'), 'scheduled_start': schedule['start_time'],
                'slack_hours': 999
            })
        priority_data.sort(key=lambda x: x['scheduled_start'])
        for i, task in enumerate(priority_data, 1): task['global_priority'] = i
        temp_scheduler.global_priority_list = priority_data

        print(f"\nWHAT-IF SCENARIO COMPLETE: Prioritized {prioritized_product}")
        return temp_scheduler
    else:
        print(f"Solver could not find a solution for What-If scenario. Status: {solver.StatusName(status)}")
        return None
