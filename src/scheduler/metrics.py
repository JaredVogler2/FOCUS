# src/scheduler/metrics.py

import pandas as pd
from collections import defaultdict
from datetime import datetime, timedelta

def calculate_lateness_metrics(scheduler):
    """Calculate lateness metrics per product"""
    metrics = {}

    product_task_schedules = defaultdict(list)
    for task_instance_id, schedule in scheduler.task_schedule.items():
        product = schedule.get('product')
        if product:
            product_task_schedules[product].append(schedule)

    for product, delivery_date in scheduler.delivery_dates.items():
        product_tasks = product_task_schedules.get(product, [])

        if product_tasks:
            last_task_end = max(task['end_time'] for task in product_tasks)
            lateness_days = (last_task_end - delivery_date).days

            task_type_counts = defaultdict(int)
            for task in product_tasks:
                task_type_counts[task['task_type']] += 1

            metrics[product] = {
                'delivery_date': delivery_date,
                'projected_completion': last_task_end,
                'lateness_days': lateness_days,
                'on_time': lateness_days <= 0,
                'total_tasks': len(product_tasks),
                'task_breakdown': dict(task_type_counts)
            }
        else:
            metrics[product] = {
                'delivery_date': delivery_date,
                'projected_completion': None,
                'lateness_days': 999999,
                'on_time': False,
                'total_tasks': 0,
                'task_breakdown': {}
            }

    return metrics

def calculate_makespan(scheduler):
    """Calculate makespan in working days"""
    if not scheduler.task_schedule:
        return 0

    scheduled_count = len(scheduler.task_schedule)
    total_tasks = len(scheduler.tasks)
    if scheduled_count < total_tasks:
        return 999999

    start_time = min(sched['start_time'] for sched in scheduler.task_schedule.values())
    end_time = max(sched['end_time'] for sched in scheduler.task_schedule.values())

    current = start_time.date()
    end_date = end_time.date()
    working_days = 0

    while current <= end_date:
        is_working = False
        for product in scheduler.delivery_dates.keys():
            if scheduler.is_working_day(datetime.combine(current, datetime.min.time()), product):
                is_working = True
                break
        if is_working:
            working_days += 1
        current += timedelta(days=1)

    return working_days

def calculate_slack_time(scheduler, task_id):
    """Calculate slack time for a task with overflow protection"""
    original_task_id = task_id.split('---part')[0]

    if task_id not in scheduler.task_schedule:
        return float('inf')

    scheduled_start = scheduler.task_schedule[task_id]['start_time']

    # Get product and delivery date if available
    product = scheduler.tasks.get(original_task_id, {}).get('product')

    # For tasks without successors, use delivery date as constraint
    successors = scheduler.get_successors(original_task_id)

    if not successors:
        # No successors - use product delivery date if available
        if product and product in scheduler.delivery_dates:
            try:
                delivery_date = pd.Timestamp(scheduler.delivery_dates[product])
                # Add a reasonable buffer to avoid overflow
                if delivery_date.year > 2050:
                    return float('inf')

                slack = (delivery_date - scheduled_start).total_seconds() / 3600
                return max(0, slack)
            except (OverflowError, ValueError, AttributeError):
                return float('inf')
        else:
            # No delivery constraint, effectively infinite slack
            return float('inf')

    # Calculate based on successors
    latest_start = None

    for successor_id in successors:
        # Successor might be split, so we find the earliest start time of its parts
        successor_parts = [sid for sid in scheduler.task_schedule.keys() if sid.startswith(successor_id)]
        if not successor_parts:
            continue

        successor_start = min(scheduler.task_schedule[sp]['start_time'] for sp in successor_parts)

        # Account for task duration
        task_duration_hours = scheduler.tasks[original_task_id].get('duration', 0) / 60

        # Calculate when this task must start to not delay successor
        required_start = successor_start - timedelta(hours=task_duration_hours)

        if latest_start is None or required_start < latest_start:
            latest_start = required_start

    # If no valid latest start found, use delivery date
    if latest_start is None:
        if product and product in scheduler.delivery_dates:
            try:
                latest_start = pd.Timestamp(scheduler.delivery_dates[product])
                # Safety check
                if latest_start.year > 2050:
                    return float('inf')
            except (ValueError, AttributeError):
                return float('inf')
        else:
            return float('inf')

    # Safety check for overflow
    try:
        # Check for unreasonable dates
        if latest_start.year > 2050 or scheduled_start.year > 2050:
            return float('inf')

        # Calculate slack
        slack_hours = (latest_start - scheduled_start).total_seconds() / 3600

        # Sanity check the result
        if abs(slack_hours) > 365 * 24:  # More than a year of slack seems wrong
            return float('inf')

        return max(0, slack_hours)

    except (OverflowError, ValueError, AttributeError) as e:
        if scheduler.debug:
            print(f"[WARNING] Error calculating slack for task {task_id}: {str(e)}")
        return float('inf')

def calculate_utilization_variance(scheduler):
    """Calculate variance in daily utilization across all teams"""
    daily_utilizations = defaultdict(list)

    # Calculate utilization for each team for each day
    for team in list(scheduler.team_capacity.keys()) + list(scheduler.quality_team_capacity.keys()):
        team_tasks = [(t, s) for t, s in scheduler.task_schedule.items() if s['team'] == team]

        if not team_tasks:
            continue

        # Find date range
        min_date = min(s['start_time'].date() for _, s in team_tasks)
        max_date = max(s['end_time'].date() for _, s in team_tasks)

        current_date = min_date
        while current_date <= max_date:
            if scheduler.is_working_day(datetime.combine(current_date, datetime.min.time()),
                                   list(scheduler.delivery_dates.keys())[0]):
                util = calculate_day_utilization(scheduler, team, current_date)
                daily_utilizations[team].append(util)
            current_date += timedelta(days=1)

    # Calculate variance
    all_utilizations = []
    for team_utils in daily_utilizations.values():
        all_utilizations.extend(team_utils)

    if not all_utilizations:
        return 0

    mean = sum(all_utilizations) / len(all_utilizations)
    variance = sum((x - mean) ** 2 for x in all_utilizations) / len(all_utilizations)
    return variance

def calculate_day_utilization(scheduler, team, target_date):
    """Calculate the utilization for a team on a specific date"""
    total_minutes = 0
    capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)

    if capacity == 0:
        return 0

    for task_id, schedule in scheduler.task_schedule.items():
        if schedule['team'] == team:
            task_date = schedule['start_time'].date()
            if task_date == target_date:
                total_minutes += schedule['duration'] * schedule['mechanics_required']

    # FIX: Use actual shift hours instead of hardcoded 8
    team_shifts = scheduler.team_shifts.get(team, scheduler.quality_team_shifts.get(team, ['1st']))
    total_available_minutes = 0

    for shift in team_shifts:
        shift_info = scheduler.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
        start_hour, start_min = _parse_shift_time(shift_info['start'])
        end_hour, end_min = _parse_shift_time(shift_info['end'])

        # Calculate shift duration
        if shift == '3rd':  # Crosses midnight
            shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
        else:
            shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)

        total_available_minutes += shift_minutes * capacity

    if total_available_minutes > 0:
        return (total_minutes / total_available_minutes) * 100
    return 0

def calculate_peak_utilization(scheduler):
    """Calculate peak single-day utilization across all teams"""
    if not scheduler.task_schedule:
        return 0

    # Find date range
    all_dates = set()
    for schedule in scheduler.task_schedule.values():
        all_dates.add(schedule['start_time'].date())

    if not all_dates:
        return 0

    peak_util = 0
    peak_date = None
    peak_team = None

    for check_date in sorted(all_dates)[:5]:  # Check first 5 days only
        for team in list(scheduler.team_capacity.keys()) + list(scheduler.quality_team_capacity.keys()):
            util = calculate_day_utilization(scheduler, team, check_date)
            if util > peak_util:
                peak_util = util
                peak_date = check_date
                peak_team = team

    if scheduler.debug:
        print(f"Peak utilization: {peak_util:.1f}% on {peak_date} for {peak_team}")

    return peak_util

def calculate_team_utilization(scheduler, team, makespan):
    """Calculate utilization for a specific team"""
    if makespan == 0:
        return 0

    capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)
    if capacity == 0:
        return 0

    # Sum up work hours for this team
    total_work_minutes = 0
    for task_id, schedule in scheduler.task_schedule.items():
        if schedule.get('team_skill', schedule.get('team')) == team:
            total_work_minutes += schedule['duration'] * schedule.get('mechanics_required', 1)

    # Available capacity (8 hours per day per person)
    available_minutes = capacity * 8 * 60 * makespan

    if available_minutes > 0:
        return (total_work_minutes / available_minutes) * 100
    return 0

def calculate_discrete_utilization(scheduler):
    """Calculate utilization for discrete product scheduling"""
    if not scheduler.task_schedule:
        return 0

    makespan = calculate_makespan(scheduler)
    if makespan == 0 or makespan >= 999999:
        return 0

    # Sum total work content
    total_work_minutes = sum(
        schedule['duration'] * schedule.get('mechanics_required', 1)
        for schedule in scheduler.task_schedule.values()
    )

    # Sum total available capacity
    total_available_minutes = 0
    for team, capacity in scheduler.team_capacity.items():
        if capacity > 0:
            total_available_minutes += capacity * 8 * 60 * makespan

    for team, capacity in scheduler.quality_team_capacity.items():
        if capacity > 0:
            total_available_minutes += capacity * 8 * 60 * makespan

    if total_available_minutes > 0:
        return (total_work_minutes / total_available_minutes) * 100
    return 0

def calculate_average_utilization(scheduler):
    """Calculate average utilization across all teams"""
    if not scheduler.task_schedule:
        return 0

    makespan = calculate_makespan(scheduler)
    if makespan == 0 or makespan >= 999999:
        return 0

    total_utilization = 0
    team_count = 0

    # Calculate for mechanic teams
    for team, capacity in scheduler.team_capacity.items():
        if capacity > 0:
            util = calculate_day_utilization(scheduler, team, datetime.now().date())
            if util > 0:
                total_utilization += util
                team_count += 1

    # Calculate for quality teams
    for team, capacity in scheduler.quality_team_capacity.items():
        if capacity > 0:
            util = calculate_day_utilization(scheduler, team, datetime.now().date())
            if util > 0:
                total_utilization += util
                team_count += 1

    return total_utilization / team_count if team_count > 0 else 0

def calculate_average_utilization_properly(scheduler):
    """Calculate utilization for first day only (continuous flow assumption)"""
    if not scheduler.task_schedule:
        return 0

    # Find the first working day
    start_date = min(s['start_time'].date() for s in scheduler.task_schedule.values())

    # Calculate total work on day 1
    day1_work_minutes = 0
    for task_id, schedule in scheduler.task_schedule.items():
        if schedule['start_time'].date() == start_date:
            day1_work_minutes += schedule['duration'] * schedule.get('mechanics_required', 1)

    # Calculate total available capacity for day 1
    day1_capacity_minutes = 0

    # Add mechanic team capacity
    for team, capacity in scheduler.team_capacity.items():
        if capacity > 0:
            shifts = scheduler.team_shifts.get(team, ['1st'])
            for shift in shifts:
                shift_info = scheduler.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
                start_hour, start_min = _parse_shift_time(shift_info['start'])
                end_hour, end_min = _parse_shift_time(shift_info['end'])

                if shift == '3rd':  # Crosses midnight
                    shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
                else:
                    shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)

                day1_capacity_minutes += shift_minutes * capacity

    # Add quality team capacity
    for team, capacity in scheduler.quality_team_capacity.items():
        if capacity > 0:
            shifts = scheduler.quality_team_shifts.get(team, ['1st'])
            for shift in shifts:
                shift_info = scheduler.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
                start_hour, start_min = _parse_shift_time(shift_info['start'])
                end_hour, end_min = _parse_shift_time(shift_info['end'])

                if shift == '3rd':
                    shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
                else:
                    shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)

                day1_capacity_minutes += shift_minutes * capacity

    if day1_capacity_minutes > 0:
        return (day1_work_minutes / day1_capacity_minutes) * 100
    return 0

def calculate_team_utilizations(scheduler):
    """Calculate utilization for each team"""
    if not scheduler.task_schedule:
        return {}

    makespan = calculate_makespan(scheduler)
    if makespan == 0:
        return {}

    utilizations = {}

    for team, capacity in scheduler.team_capacity.items():
        if capacity > 0:
            util = calculate_team_utilization(scheduler, team, makespan)
            utilizations[team] = util

    for team, capacity in scheduler.quality_team_capacity.items():
        if capacity > 0:
            util = calculate_team_utilization(scheduler, team, makespan)
            utilizations[team] = util

    return utilizations

def calculate_initial_utilization(scheduler, days_to_check=1):
    """Calculate average utilization for first few days only (continuous flow assumption)"""
    if not scheduler.task_schedule:
        return 0

    # Find earliest start date
    start_date = min(s['start_time'].date() for s in scheduler.task_schedule.values())
    end_date = start_date + timedelta(days=days_to_check)

    total_util = 0
    team_count = 0

    for team in list(scheduler.team_capacity.keys()) + list(scheduler.quality_team_capacity.keys()):
        capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)
        if capacity == 0:
            continue

        team_total_minutes = 0
        team_available_minutes = 0

        current_date = start_date
        while current_date < end_date:
            if scheduler.is_working_day(datetime.combine(current_date, datetime.min.time()),
                                   list(scheduler.delivery_dates.keys())[0]):
                day_util = calculate_day_utilization(scheduler, team, current_date)
                team_total_minutes += day_util
                team_available_minutes += 100  # Each day can be up to 100% utilized
            current_date += timedelta(days=1)

        if team_available_minutes > 0:
            team_avg_util = team_total_minutes / days_to_check
            total_util += team_avg_util
            team_count += 1

    return total_util / team_count if team_count > 0 else 0


def calculate_gap_penalty(scheduler, team, proposed_start, proposed_end):
    """Calculate penalty for creating gaps in the schedule"""
    penalty = 0

    # Find tasks for this team on the same day
    day_tasks = []
    for task_id, schedule in scheduler.task_schedule.items():
        if schedule['team'] == team and schedule['start_time'].date() == proposed_start.date():
            day_tasks.append((schedule['start_time'], schedule['end_time']))

    if day_tasks:
        day_tasks.sort()

        # Check for gaps before and after proposed task
        for start, end in day_tasks:
            if end < proposed_start:
                gap = (proposed_start - end).total_seconds() / 3600  # Gap in hours
                if gap > 1:  # Penalty for gaps > 1 hour
                    penalty += gap * 10
            elif start > proposed_end:
                gap = (start - proposed_end).total_seconds() / 3600
                if gap > 1:
                    penalty += gap * 10

    return penalty

def _parse_shift_time(time_str):
    """Helper to parse shift time string into hour and minute"""
    # Remove any whitespace
    time_str = time_str.strip()

    # Handle AM/PM format if present
    if 'AM' in time_str or 'PM' in time_str:
        time_str_clean = time_str.replace(' AM', '').replace(' PM', '').replace('AM', '').replace('PM', '')
        time_parts = time_str_clean.split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        # Adjust for PM
        if 'PM' in time_str and hour != 12:
            hour += 12
        elif 'AM' in time_str and hour == 12:
            hour = 0
    else:
        # 24-hour format
        time_parts = time_str.split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

    return hour, minute
