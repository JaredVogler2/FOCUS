# src/scheduler/algorithms.py

from datetime import datetime, timedelta
from collections import defaultdict
import heapq
from . import metrics

def schedule_tasks(scheduler, allow_late_delivery=False, silent_mode=False):
    """Schedule all task instances with proper error handling including customer inspections"""
    original_debug = scheduler.debug
    if silent_mode:
        scheduler.debug = False

    scheduler.task_schedule = {}
    scheduler._critical_path_cache = {}

    if not silent_mode and not scheduler.validate_dag():
        raise ValueError("DAG validation failed!")

    dynamic_constraints = scheduler.build_dynamic_dependencies()
    start_date = datetime(2025, 8, 22, 6, 0)

    constraints_by_second = defaultdict(list)
    constraints_by_first = defaultdict(list)

    for constraint in dynamic_constraints:
        constraints_by_second[constraint['Second']].append(constraint)
        constraints_by_first[constraint['First']].append(constraint)

    all_tasks = set(scheduler.tasks.keys())
    total_tasks = len(all_tasks)
    ready_tasks = []

    if not silent_mode:
        print(f"\nStarting scheduling for {total_tasks} task instances...")

    # Find initially ready tasks
    tasks_with_incoming_constraints = set()
    tasks_with_outgoing_constraints = set()

    for constraint in dynamic_constraints:
        tasks_with_incoming_constraints.add(constraint['Second'])
        tasks_with_outgoing_constraints.add(constraint['First'])

    orphaned_tasks = all_tasks - tasks_with_incoming_constraints - tasks_with_outgoing_constraints

    if not silent_mode and orphaned_tasks:
        print(f"[DEBUG] Found {len(orphaned_tasks)} orphaned tasks with no constraints")

    for task in orphaned_tasks:
        priority = calculate_task_priority(scheduler, task)
        heapq.heappush(ready_tasks, (priority, task))

    tasks_with_only_outgoing = tasks_with_outgoing_constraints - tasks_with_incoming_constraints
    for task in tasks_with_only_outgoing:
        priority = calculate_task_priority(scheduler, task)
        heapq.heappush(ready_tasks, (priority, task))

    for task in tasks_with_incoming_constraints:
        constraints = constraints_by_second.get(task, [])
        has_blocking_constraints = False
        for c in constraints:
            rel = c['Relationship']
            if rel in ['Finish <= Start', 'Finish = Start', 'Finish <= Finish']:
                has_blocking_constraints = True
                break
        if not has_blocking_constraints:
            priority = calculate_task_priority(scheduler, task)
            heapq.heappush(ready_tasks, (priority, task))

    if not silent_mode:
        print(f"[DEBUG] Initial ready queue has {len(ready_tasks)} tasks")

    scheduled_count = 0
    max_iterations = total_tasks * 10
    iteration_count = 0
    failed_tasks = set()
    task_retry_counts = defaultdict(int)

    # Track scheduling failures
    cannot_schedule = []
    far_future_schedules = []

    while ready_tasks and scheduled_count < total_tasks and iteration_count < max_iterations:
        iteration_count += 1

        if not ready_tasks:
            for task in all_tasks:
                if task in scheduler.task_schedule or task in failed_tasks:
                    continue

                all_predecessors_scheduled = True
                for constraint in constraints_by_second.get(task, []):
                    if constraint['First'] not in scheduler.task_schedule:
                        all_predecessors_scheduled = False
                        break

                if all_predecessors_scheduled:
                    priority = calculate_task_priority(scheduler, task)
                    heapq.heappush(ready_tasks, (priority, task))

            if not ready_tasks:
                if not silent_mode:
                    unscheduled = [t for t in all_tasks if t not in scheduler.task_schedule and t not in failed_tasks]
                    print(f"[WARNING] No ready tasks but {len(unscheduled)} tasks remain unscheduled")
                break

        priority, task_instance_id = heapq.heappop(ready_tasks)

        if task_retry_counts[task_instance_id] >= 3:
            if task_instance_id not in failed_tasks:
                failed_tasks.add(task_instance_id)
                if not silent_mode:
                    print(f"[ERROR] Task {task_instance_id} failed after 3 retries")
            continue

        task_info = scheduler.tasks[task_instance_id]
        duration = task_info['duration']
        mechanics_needed = task_info['mechanics_required']
        is_quality = task_info['is_quality']
        is_customer = task_info.get('is_customer', False)
        task_type = task_info['task_type']
        product = task_info.get('product', 'Unknown')

        earliest_start = start_date
        latest_start_constraint = None

        if task_instance_id in scheduler.late_part_tasks:
            earliest_start = get_earliest_start_for_late_part(scheduler, task_instance_id)

        for constraint in constraints_by_second.get(task_instance_id, []):
            first_task = constraint['First']
            relationship = constraint['Relationship']

            if first_task in scheduler.task_schedule:
                first_schedule = scheduler.task_schedule[first_task]

                if relationship == 'Finish <= Start':
                    constraint_time = first_schedule['end_time']
                elif relationship == 'Finish = Start':
                    constraint_time = first_schedule['end_time']
                elif relationship == 'Start <= Start' or relationship == 'Start = Start':
                    constraint_time = first_schedule['start_time']
                elif relationship == 'Finish <= Finish':
                    constraint_time = first_schedule['end_time'] - timedelta(minutes=duration)
                elif relationship == 'Start <= Finish':
                    constraint_time = first_schedule['start_time'] - timedelta(minutes=duration)
                else:
                    constraint_time = first_schedule['end_time']

                earliest_start = max(earliest_start, constraint_time)

                if relationship == 'Start = Start':
                    latest_start_constraint = first_schedule['start_time']

        if latest_start_constraint:
            earliest_start = latest_start_constraint

        try:
            if is_customer:
                # Find any available customer team
                best_team = None
                best_start_time = None
                best_shift = None
                earliest_available = datetime.max

                # Try each customer team to find the earliest available slot
                for team, capacity in scheduler.customer_team_capacity.items():
                    if capacity >= mechanics_needed:
                        result = get_next_working_time_with_capacity(
                            scheduler, earliest_start, product, team,
                            mechanics_needed, duration, is_quality=False, is_customer=True
                        )

                        if result and result[0] and result[0] < earliest_available:
                            earliest_available = result[0]
                            best_team = team
                            best_start_time = result[0]
                            best_shift = result[1]

                if not best_team or not best_start_time:
                    cannot_schedule.append(task_instance_id)
                    task_retry_counts[task_instance_id] += 1
                    if task_retry_counts[task_instance_id] < 3:
                        heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                    else:
                        failed_tasks.add(task_instance_id)
                        if not silent_mode:
                            print(f"[FAILED] Cannot find slot for customer task {task_instance_id}")
                    continue

                scheduled_start = best_start_time
                shift = best_shift
                team_for_schedule = best_team
                base_team_for_schedule = best_team

            elif is_quality:
                base_mechanic_team = task_info.get('team', '')
                quality_team = scheduler.map_mechanic_to_quality_team(base_mechanic_team)

                if not quality_team:
                    print(f"[ERROR] Quality task {task_instance_id} has no team assigned!")
                    if task_instance_id in scheduler.quality_inspections:
                        primary_task_id = scheduler.quality_inspections[task_instance_id].get('primary_task')
                        if primary_task_id and primary_task_id in scheduler.tasks:
                            primary_team = scheduler.tasks[primary_task_id].get('team')
                            quality_team = scheduler.map_mechanic_to_quality_team(primary_team)
                            if quality_team:
                                task_info['team'] = primary_team
                                print(f"[RECOVERY] Assigned {quality_team} to {task_instance_id}")

                    if not quality_team:
                        task_retry_counts[task_instance_id] += 1
                        if task_retry_counts[task_instance_id] < 3:
                            heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                        continue

                result = get_next_working_time_with_capacity(
                    scheduler, earliest_start, product, quality_team,
                    mechanics_needed, duration, is_quality=True, is_customer=False)

                if result is None or result[0] is None:
                    cannot_schedule.append(task_instance_id)
                    task_retry_counts[task_instance_id] += 1
                    if task_retry_counts[task_instance_id] < 3:
                        heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                    else:
                        failed_tasks.add(task_instance_id)
                        if not silent_mode:
                            print(f"[FAILED] Cannot find slot for quality task {task_instance_id}")
                    continue

                scheduled_start, shift = result
                team_for_schedule = quality_team
                base_team_for_schedule = quality_team

            else:
                team_for_scheduling = task_info.get('team_skill', task_info['team'])

                if '(' in team_for_scheduling and ')' in team_for_scheduling:
                    base_team = team_for_scheduling.split(' (')[0].strip()
                else:
                    base_team = task_info.get('team', team_for_scheduling)

                result = get_next_working_time_with_capacity(
                    scheduler, earliest_start, product, team_for_scheduling,
                    mechanics_needed, duration, is_quality=False, is_customer=False)

                if result is None or result[0] is None:
                    cannot_schedule.append(task_instance_id)
                    task_retry_counts[task_instance_id] += 1
                    if task_retry_counts[task_instance_id] < 3:
                        heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                    else:
                        failed_tasks.add(task_instance_id)
                        if not silent_mode:
                            print(f"[FAILED] Cannot find slot for mechanic task {task_instance_id}")
                    continue

                scheduled_start, shift = result
                team_for_schedule = team_for_scheduling
                base_team_for_schedule = base_team

            # Check if scheduled to far future (year 7501 problem)
            if scheduled_start.year > 2030:
                far_future_schedules.append(task_instance_id)
                failed_tasks.add(task_instance_id)
                if not silent_mode:
                    print(
                        f"[ERROR] Task {task_instance_id} scheduled to year {scheduled_start.year} - marking as failed")
                continue

            scheduled_end = scheduled_start + timedelta(minutes=int(duration))

            scheduler.task_schedule[task_instance_id] = {
                'start_time': scheduled_start,
                'end_time': scheduled_end,
                'team': base_team_for_schedule,
                'team_skill': team_for_schedule,
                'skill': task_info.get('skill'),
                'product': product,
                'duration': duration,
                'mechanics_required': mechanics_needed,
                'is_quality': is_quality,
                'is_customer': is_customer,
                'task_type': task_type,
                'shift': shift,
                'original_task_id': scheduler.instance_to_original_task.get(task_instance_id)
            }

            scheduled_count += 1

            # Add newly ready tasks
            for constraint in constraints_by_first.get(task_instance_id, []):
                dependent = constraint['Second']
                if dependent in scheduler.task_schedule or dependent in failed_tasks:
                    continue

                all_satisfied = True
                for dep_constraint in constraints_by_second.get(dependent, []):
                    predecessor = dep_constraint['First']
                    if predecessor not in scheduler.task_schedule:
                        all_satisfied = False
                        break

                if all_satisfied and dependent not in [t[1] for t in ready_tasks]:
                    dep_priority = calculate_task_priority(scheduler, dependent)
                    heapq.heappush(ready_tasks, (dep_priority, dependent))

        except Exception as e:
            if scheduler.debug:
                print(f"[ERROR] Failed to schedule {task_instance_id}: {str(e)}")
            task_retry_counts[task_instance_id] += 1
            if task_retry_counts[task_instance_id] < 3:
                heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
            else:
                failed_tasks.add(task_instance_id)

    if not silent_mode:
        print(f"\n[DEBUG] Scheduling complete! Actually scheduled {scheduled_count}/{total_tasks} task instances.")
        if scheduled_count < total_tasks:
            unscheduled = total_tasks - scheduled_count
            print(f"[WARNING] {unscheduled} tasks could not be scheduled")

            if cannot_schedule:
                print(f"[WARNING] {len(cannot_schedule)} tasks couldn't find time slots")

            if far_future_schedules:
                print(f"[WARNING] {len(far_future_schedules)} tasks scheduled to far future (>2030)")

            unscheduled_list = [t for t in all_tasks if t not in scheduler.task_schedule][:10]
            print(f"[DEBUG] First 10 unscheduled tasks: {unscheduled_list}")

    scheduler.debug = original_debug

def get_earliest_start_for_late_part(scheduler, task_instance_id):
    """Calculate earliest start time for a late part task"""
    # task_instance_id is now like "LP_1001"
    if task_instance_id not in scheduler.on_dock_dates:
        return datetime(2025, 8, 22, 6, 0)

    on_dock_date = scheduler.on_dock_dates[task_instance_id]
    earliest_start = on_dock_date + timedelta(days=scheduler.late_part_delay_days)
    earliest_start = earliest_start.replace(hour=6, minute=0, second=0, microsecond=0)
    return earliest_start

def get_next_working_time_with_capacity(scheduler, current_time, product_line, team,
                                        mechanics_needed, duration, is_quality=False, is_customer=False):
    """Find next available working time with sufficient team capacity"""

    # Get capacity and shifts based on team type
    if is_customer:
        capacity = scheduler.customer_team_capacity.get(team, 0)
        shifts = scheduler.customer_team_shifts.get(team, ['1st'])
    elif is_quality:
        capacity = scheduler.quality_team_capacity.get(team, 0)
        shifts = scheduler.quality_team_shifts.get(team, ['1st'])
    else:
        capacity = scheduler.team_capacity.get(team, 0)
        base_team = team.split('(')[0].strip() if '(' in team else team
        shifts = scheduler.team_shifts.get(base_team, scheduler.team_shifts.get(team, ['1st']))

    if capacity == 0 or mechanics_needed > capacity:
        return None, None

    # Start from current time
    search_time = current_time
    max_days_ahead = 30

    for days_ahead in range(max_days_ahead):
        check_date = (current_time + timedelta(days=days_ahead)).replace(
            hour=0, minute=0, second=0, microsecond=0)

        if not scheduler.is_working_day(check_date, product_line):
            continue

        for shift in shifts:
            shift_info = scheduler.shift_hours.get(shift)
            if not shift_info:
                continue

            start_hour, start_min = scheduler._parse_shift_time(shift_info['start'])
            end_hour, end_min = scheduler._parse_shift_time(shift_info['end'])

            # Calculate shift boundaries
            if shift == '3rd':
                # 3rd shift: 23:00 today to 6:00 tomorrow
                shift_start = check_date.replace(hour=23, minute=0)
                shift_end = (check_date + timedelta(days=1)).replace(hour=6, minute=0)

                # Special case: if we're checking today and current time is 0:00-6:00,
                # check if we're still in yesterday's 3rd shift
                if days_ahead == 0 and current_time.hour < 6:
                    # We're in the tail end of yesterday's 3rd shift
                    shift_start = (check_date - timedelta(days=1)).replace(hour=23, minute=0)
                    shift_end = check_date.replace(hour=6, minute=0)
            else:
                shift_start = check_date.replace(hour=start_hour, minute=start_min)
                shift_end = check_date.replace(hour=end_hour, minute=end_min)

            # Skip if shift already ended
            if shift_end <= current_time:
                continue

            # Find earliest possible start within this shift
            earliest_in_shift = max(shift_start, current_time)

            # Round up to next 15-minute mark
            minutes = earliest_in_shift.minute
            if minutes % 15 != 0:
                rounded_minutes = ((minutes // 15) + 1) * 15
                if rounded_minutes >= 60:
                    earliest_in_shift = earliest_in_shift.replace(minute=0) + timedelta(hours=1)
                else:
                    earliest_in_shift = earliest_in_shift.replace(minute=rounded_minutes)

            # Check if task fits in shift
            task_end = earliest_in_shift + timedelta(minutes=duration)
            if task_end > shift_end:
                continue

            # Check capacity
            conflicts = 0
            for task_id, schedule in scheduler.task_schedule.items():
                # Check if same team (considering all team types)
                scheduled_team = schedule.get('team_skill', schedule.get('team'))

                # For customer teams, check against team directly
                if is_customer:
                    if scheduled_team == team:
                        # Check for time overlap
                        if (schedule['start_time'] < task_end and
                                schedule['end_time'] > earliest_in_shift):
                            conflicts += schedule.get('mechanics_required', 1)
                else:
                    # For mechanic and quality teams, check team_skill or team
                    if scheduled_team == team or (not is_quality and schedule.get('team') == team):
                        # Check for time overlap
                        if (schedule['start_time'] < task_end and
                                schedule['end_time'] > earliest_in_shift):
                            conflicts += schedule.get('mechanics_required', 1)

            if capacity - conflicts >= mechanics_needed:
                return earliest_in_shift, shift

    return None, None

def check_constraint_satisfied(scheduler, first_schedule, second_schedule, relationship):
    """Check if a scheduling constraint is satisfied between two tasks"""
    if not first_schedule or not second_schedule:
        return True, None, None

    first_start = first_schedule['start_time']
    first_end = first_schedule['end_time']
    second_start = second_schedule['start_time']
    second_end = second_schedule['end_time']
    second_duration = second_schedule['duration']

    relationship = scheduler._normalize_relationship_type(relationship)

    if relationship == 'Finish <= Start':
        is_satisfied = first_end <= second_start
        earliest_start = first_end
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Finish = Start':
        is_satisfied = abs((first_end - second_start).total_seconds()) < 60
        earliest_start = first_end
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Finish <= Finish':
        is_satisfied = first_end <= second_end
        earliest_end = max(first_end, second_start + timedelta(minutes=second_duration))
        earliest_start = earliest_end - timedelta(minutes=second_duration)

    elif relationship == 'Start <= Start':
        is_satisfied = first_start <= second_start
        earliest_start = first_start
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Start = Start':
        is_satisfied = abs((first_start - second_start).total_seconds()) < 60
        earliest_start = first_start
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Start <= Finish':
        is_satisfied = first_start <= second_end
        earliest_end = max(first_start, second_start + timedelta(minutes=second_duration))
        earliest_start = earliest_end - timedelta(minutes=second_duration)

    else:
        is_satisfied = first_end <= second_start
        earliest_start = first_end
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    return is_satisfied, earliest_start, earliest_end

def is_valid_start_time(scheduler, task_id, proposed_start, constraints):
    """Check if starting a task at proposed_start violates any constraints"""
    task_info = scheduler.tasks[task_id]
    duration = task_info['duration']
    proposed_end = proposed_start + timedelta(minutes=duration)

    # Check predecessors
    for constraint in constraints:
        if constraint['Second'] == task_id and constraint['First'] in scheduler.task_schedule:
            predecessor = scheduler.task_schedule[constraint['First']]
            relationship = constraint['Relationship']

            if not check_constraint_satisfied(scheduler, predecessor,
                                                   {'start_time': proposed_start, 'end_time': proposed_end,
                                                    'duration': duration},
                                                   relationship)[0]:
                return False

    # Check successors already scheduled
    for constraint in constraints:
        if constraint['First'] == task_id and constraint['Second'] in scheduler.task_schedule:
            successor = scheduler.task_schedule[constraint['Second']]
            relationship = constraint['Relationship']

            if not check_constraint_satisfied(scheduler,
                    {'start_time': proposed_start, 'end_time': proposed_end, 'duration': duration},
                    successor, relationship)[0]:
                return False

    return True

def can_reschedule_task(scheduler, task_id, new_start_time):
    """
    Check if a task can be rescheduled to a new time without violating constraints
    """
    task_info = scheduler.tasks[task_id]
    duration = task_info['duration']
    new_end_time = new_start_time + timedelta(minutes=duration)

    # Get all constraints for this task
    dynamic_constraints = scheduler.build_dynamic_dependencies()

    # Check predecessor constraints
    for constraint in dynamic_constraints:
        if constraint['Second'] == task_id:
            first_task = constraint['First']
            if first_task in scheduler.task_schedule:
                first_schedule = scheduler.task_schedule[first_task]
                temp_schedule = {
                    'start_time': new_start_time,
                    'end_time': new_end_time,
                    'duration': duration
                }
                is_satisfied, _, _ = check_constraint_satisfied(
                    scheduler, first_schedule, temp_schedule, constraint['Relationship']
                )
                if not is_satisfied:
                    return False

    # Check successor constraints
    for constraint in dynamic_constraints:
        if constraint['First'] == task_id:
            second_task = constraint['Second']
            if second_task in scheduler.task_schedule:
                second_schedule = scheduler.task_schedule[second_task]
                temp_schedule = {
                    'start_time': new_start_time,
                    'end_time': new_end_time,
                    'duration': duration
                }
                is_satisfied, _, _ = check_constraint_satisfied(
                    scheduler, temp_schedule, second_schedule, constraint['Relationship']
                )
                if not is_satisfied:
                    return False

    return True

def calculate_task_priority(scheduler, task_instance_id):
    """Calculate priority for a task instance considering dependent task timing"""
    original_task_id = task_instance_id.split('---part')[0]
    task_info = scheduler.tasks[original_task_id]


    # For late parts, check on-dock date
    if original_task_id in scheduler.late_part_tasks:
        # Check if we have an on-dock date
        if task_instance_id in scheduler.on_dock_dates:
            on_dock_date = scheduler.on_dock_dates[task_instance_id]
            days_until_available = (on_dock_date - datetime.now()).days
            # Priority based on how soon the part arrives
            return -3000 + (days_until_available * 10)  # Less urgent if arriving later
        return -3000

    # For quality inspections, inherit priority from primary task
    if original_task_id in scheduler.quality_inspections:
        primary_task = scheduler.quality_inspections[original_task_id].get('primary_task')
        if primary_task and primary_task in scheduler.task_schedule:
            # QI should happen right after primary task
            return calculate_task_priority(scheduler, primary_task) - 1
        return -2000

    # For rework tasks, consider when the dependent tasks need them
    if original_task_id in scheduler.rework_tasks:
        # Find all tasks that depend on this rework
        dynamic_constraints = scheduler.build_dynamic_dependencies()
        dependent_tasks = []

        for constraint in dynamic_constraints:
            if constraint['First'] == original_task_id:
                dependent_tasks.append(constraint['Second'])

        if dependent_tasks:
            # Calculate the earliest dependent task's priority
            min_dependent_priority = float('inf')
            for dep_task in dependent_tasks:
                if dep_task in scheduler.tasks:
                    # Get the product and delivery date of the dependent task
                    dep_task_info = scheduler.tasks[dep_task]
                    dep_product = dep_task_info.get('product')

                    if dep_product and dep_product in scheduler.delivery_dates:
                        delivery_date = scheduler.delivery_dates[dep_product]
                        days_to_delivery = (delivery_date - datetime.now()).days

                        # Calculate priority based on delivery urgency
                        dep_priority = (100 - days_to_delivery) * 20
                        min_dependent_priority = min(min_dependent_priority, dep_priority)

            if min_dependent_priority < float('inf'):
                # Rework should be slightly higher priority than its dependents
                # but not universally high priority
                return min_dependent_priority - 100

        # Fallback for rework with no clear dependents
        return -500  # Still important but not top priority

    # Standard priority calculation for baseline tasks
    product = task_info.get('product')
    if product and product in scheduler.delivery_dates:
        delivery_date = scheduler.delivery_dates[product]
        days_to_delivery = (delivery_date - datetime.now()).days
    else:
        days_to_delivery = 999

    critical_path_length = calculate_critical_path_length(scheduler, original_task_id)
    duration = int(task_info['duration'])

    priority = (
            (100 - days_to_delivery) * 20 +
            (10000 - critical_path_length) * 5 +
            (100 - duration / 10) * 2
    )

    return priority

def classify_task_criticality(scheduler, task_instance_id):
    """
    Classify task as CRITICAL, BUFFER, or FLEXIBLE based on slack time
    """
    task_info = scheduler.tasks.get(task_instance_id, {})
    product = task_info.get('product')

    if not product or product not in scheduler.delivery_dates:
        return 'FLEXIBLE'

    # Calculate total slack in days
    slack_hours = metrics.calculate_slack_time(scheduler, task_instance_id)

    # Handle infinite slack
    if slack_hours == float('inf'):
        return 'FLEXIBLE'

    slack_days = slack_hours / 24

    # Classification thresholds
    if slack_days < 2:
        return 'CRITICAL'  # Must schedule ASAP
    elif slack_days < 5:
        return 'BUFFER'  # Some flexibility, but careful
    else:
        return 'FLEXIBLE'  # Can spread out safely

def calculate_critical_path_length(scheduler, task_instance_id):
    """Calculate critical path length from this task"""
    if task_instance_id in scheduler._critical_path_cache:
        return scheduler._critical_path_cache[task_instance_id]

    dynamic_constraints = scheduler.build_dynamic_dependencies()

    def get_path_length(task):
        if task in scheduler._critical_path_cache:
            return scheduler._critical_path_cache[task]

        max_successor_path = 0
        task_duration = scheduler.tasks[task]['duration']

        for constraint in dynamic_constraints:
            if constraint['First'] == task:
                successor = constraint['Second']
                if successor in scheduler.tasks:
                    successor_path = get_path_length(successor)
                    max_successor_path = max(max_successor_path, successor_path)

        scheduler._critical_path_cache[task] = task_duration + max_successor_path
        return scheduler._critical_path_cache[task]

    return get_path_length(task_instance_id)
